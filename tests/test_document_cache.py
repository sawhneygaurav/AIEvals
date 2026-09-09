"""Verify freshness, exact evidence reuse and cache failure recovery offline."""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO

import httpx
import pytest
from pypdf import PdfReader, PdfWriter
from pypdf._page import PageObject
from pypdf.generic import DecodedStreamObject, NameObject

from competitive_scoring.config import Settings
from competitive_scoring.tools import document_cache, free_sources
from competitive_scoring.tools.document_cache import CachedDocument, DocumentCache, digest
from competitive_scoring.tools.free_sources import FreeSourceCollector, _extract_pdf_text
from competitive_scoring.tracing import trace_run
from tests.test_free_sources import _pdf_with_pages, _pdf_with_text

URL = "https://cdn.shopify.com/reports/annual-report.pdf"
HOSTS = ("cdn.shopify.com",)
PROFILE = free_sources.TRUSTED_COMPANY_PROFILES["PNGJL"]
LINK = free_sources._Link(URL, "Annual Report")


def _fetch(handler, **kwargs):
    with FreeSourceCollector(transport=httpx.MockTransport(handler), **kwargs) as collector:
        return collector._fetch_official_document(PROFILE, LINK)


@pytest.mark.parametrize("validator,header,value", [
    ("etag", "if-none-match", '"edition-1"'),
    ("last-modified", "if-modified-since", "Sat, 05 Sep 2026 09:00:00 GMT"),
])
def test_new_collector_revalidates_pdf_and_reuses_exact_excerpt(monkeypatch, validator, header, value):
    pdf = _pdf_with_text("Consolidated profit after tax INR 209 crore FY2026")

    def first(request):
        assert header not in request.headers
        return httpx.Response(200, content=pdf, headers={validator: value})

    initial, warning = _fetch(first)
    assert warning is None
    assert "[PDF page 1]" in initial.excerpt

    def no_parse(*args, **kwargs):
        raise AssertionError("unchanged filing must not be parsed again")

    monkeypatch.setattr(free_sources, "PdfReader", no_parse)

    def second(request):
        assert request.headers[header] == value
        return httpx.Response(304)

    with trace_run(Settings(mode="demo")) as trace:
        reused, warning = _fetch(second)
    assert warning is None
    assert reused.excerpt == initial.excerpt
    assert reused.url == initial.url
    assert reused.retrieved_at >= initial.retrieved_at
    events = [json.loads(line) for line in (trace.directory / "events.jsonl").read_text().splitlines()]
    assert any(e["event"] == "document.cache" and e["metadata"]["outcome"] == "revalidated"
               for e in events)
    assert any(e["event"] == "pdf.cache" and e["metadata"]["outcome"] == "hit" for e in events)
    assert not any(e.get("operation") == "pdf.scan_pages" for e in events)


def test_changed_pdf_at_same_url_invalidates_text():
    old = _pdf_with_text("Profit after tax INR 100 crore FY2025")
    new = _pdf_with_text("Profit after tax INR 200 crore FY2026")
    initial, _ = _fetch(lambda _: httpx.Response(200, content=old, headers={"etag": '"old"'}))

    def changed(request):
        assert request.headers["if-none-match"] == '"old"'
        return httpx.Response(200, content=new, headers={"etag": '"new"'})

    latest, warning = _fetch(changed)
    assert warning is None
    assert "200 crore FY2026" in latest.excerpt
    assert initial.excerpt != latest.excerpt


def test_without_http_validators_downloads_again_but_reuses_extraction(monkeypatch):
    pdf = _pdf_with_text("Profit after tax INR 100 crore")
    requests = []

    def handler(request):
        requests.append(request)
        assert "if-none-match" not in request.headers
        assert "if-modified-since" not in request.headers
        return httpx.Response(200, content=pdf)

    first, _ = _fetch(handler)
    monkeypatch.setattr(free_sources, "PdfReader", lambda _: pytest.fail("must reuse extraction"))
    second, warning = _fetch(handler)
    assert len(requests) == 2
    assert warning is None
    assert first.excerpt == second.excerpt


def test_failed_revalidation_does_not_use_stale_evidence():
    pdf = _pdf_with_text("Prior financial statements")
    _fetch(lambda _: httpx.Response(200, content=pdf, headers={"etag": '"old"'}))
    result, warning = _fetch(lambda _: httpx.Response(503))
    assert warning
    assert result.excerpt == ""
    assert result.metadata["retrieval_status"] == "failed"


@pytest.mark.parametrize("damage", ["manifest", "blob", "excerpt", "cached_host"])
def test_damaged_cache_recovers_using_fresh_source(monkeypatch, damage):
    pdf = _pdf_with_text("Verified financial results")
    initial, _ = _fetch(lambda _: httpx.Response(200, content=pdf, headers={"etag": '"v1"'}))
    cache = DocumentCache.from_env()
    assert cache
    if damage == "manifest":
        next((cache.directory / "documents").glob("*.json")).write_text("not json")
    elif damage == "blob":
        next((cache.directory / "blobs").glob("*.pdf")).write_bytes(b"corrupt")
    elif damage == "excerpt":
        path = next((cache.directory / "excerpts").glob("*.json"))
        record = json.loads(path.read_text())
        record["text"] = "wrong financial values"
        path.write_text(json.dumps(record))
    else:
        path = next((cache.directory / "documents").glob("*.json"))
        record = json.loads(path.read_text())
        record["final_url"] = "https://evil.example/stolen.pdf"
        path.write_text(json.dumps(record))

    def handler(request):
        assert request.url.host == "cdn.shopify.com"
        if damage != "excerpt":
            assert "if-none-match" not in request.headers
        return httpx.Response(200, content=pdf, headers={"etag": '"v1"'})

    recovered, warning = _fetch(handler)
    assert warning is None
    assert recovered.excerpt == initial.excerpt


def test_redirects_revalidate_only_exact_cached_target():
    pdf = _pdf_with_text("Official annual report")
    target = "https://cdn.shopify.com/target.pdf"
    new_target = "https://cdn.shopify.com/replacement.pdf"

    def first(request):
        if str(request.url) == URL:
            return httpx.Response(302, headers={"location": target})
        return httpx.Response(200, content=pdf, headers={"etag": '"same-tag"'})

    _fetch(first)

    def moved(request):
        assert "if-none-match" not in request.headers
        if str(request.url) == URL:
            return httpx.Response(302, headers={"location": new_target})
        assert str(request.url) == new_target
        return httpx.Response(200, content=_pdf_with_text("Changed filing"),
                              headers={"etag": '"same-tag"'})

    changed, warning = _fetch(moved)
    assert warning is None
    assert "Changed filing" in changed.excerpt

    blocked, warning = _fetch(lambda _: httpx.Response(302, headers={
        "location": "https://evil.example/replacement.pdf"
    }))
    assert warning
    assert not blocked.excerpt


def test_unsolicited_304_cannot_create_evidence():
    result, warning = _fetch(lambda _: httpx.Response(304))
    assert warning
    assert not result.excerpt


def test_unchanged_redirect_target_receives_validators_only_after_redirect():
    pdf = _pdf_with_text("Annual financial statements")
    target = "https://cdn.shopify.com/final.pdf"

    def first(request):
        if str(request.url) == URL:
            return httpx.Response(302, headers={"location": target})
        return httpx.Response(200, content=pdf, headers={"etag": '"v1"'})

    initial, _ = _fetch(first)

    def second(request):
        if str(request.url) == URL:
            assert "if-none-match" not in request.headers
            return httpx.Response(302, headers={"location": target})
        assert request.headers["if-none-match"] == '"v1"'
        return httpx.Response(304)

    repeated, warning = _fetch(second)
    assert warning is None
    assert repeated.excerpt == initial.excerpt


def test_origin_no_store_removes_old_reusable_manifest():
    pdf = _pdf_with_text("Annual financial statements")
    _fetch(lambda _: httpx.Response(200, content=pdf, headers={"etag": '"v1"'}))
    result, warning = _fetch(lambda _: httpx.Response(304, headers={"cache-control": "no-store"}))
    assert warning is None
    assert result.excerpt
    cache = DocumentCache.from_env()
    assert cache.load_document(URL, max_bytes=20_000_000) is None


@pytest.mark.parametrize("mode", ["no-store", "disabled", "unwritable"])
def test_cache_is_optional_and_respects_no_store(monkeypatch, tmp_path, mode):
    path = tmp_path / "cache"
    if mode == "unwritable":
        path.write_text("a file cannot be used as a cache directory")
    monkeypatch.setenv("COMPETITIVE_SCORING_DOCUMENT_CACHE_DIR", "off" if mode == "disabled" else str(path))
    pdf = _pdf_with_text("Official profit after tax")
    result, warning = _fetch(lambda _: httpx.Response(200, content=pdf, headers={
        "etag": '"v1"', "cache-control": "no-store" if mode == "no-store" else "public"
    }))
    assert warning is None
    assert "profit after tax" in result.excerpt
    if mode != "unwritable":
        assert not path.exists()


def test_extractor_cache_invalidates_for_policy_parser_and_limits(monkeypatch, tmp_path):
    cache = DocumentCache(tmp_path)
    pdf = _pdf_with_pages(["Company overview", "Profit after tax INR 100 crore"])
    calls = []
    original = free_sources.PdfReader

    def reader(*args, **kwargs):
        calls.append(1)
        return original(*args, **kwargs)

    monkeypatch.setattr(free_sources, "PdfReader", reader)
    for _ in range(2):
        _extract_pdf_text(pdf, max_pages=4, max_chars=12000, cache=cache)
    assert len(calls) == 1
    _extract_pdf_text(pdf, max_pages=2, max_chars=12000, cache=cache)
    _extract_pdf_text(pdf, max_pages=4, max_chars=1000, cache=cache)
    monkeypatch.setattr(document_cache, "EXTRACTION_VERSION", "next-policy")
    _extract_pdf_text(pdf, max_pages=4, max_chars=12000, cache=cache)
    monkeypatch.setattr(document_cache, "PYPDF_VERSION", "next-parser")
    _extract_pdf_text(pdf, max_pages=4, max_chars=12000, cache=cache)
    assert len(calls) == 5


def test_layout_only_on_selected_pages_preserves_table_and_page_citation(monkeypatch):
    reader = PdfReader(BytesIO(_pdf_with_pages([f"Narrative page {i}" for i in range(30)])))
    writer = PdfWriter()
    writer.append_pages_from_reader(reader)
    # Draw labels first, then number columns: plain extraction separates values
    # from their rows; layout must put them back next to the correct label.
    texts = [(72, 720, "Consolidated Statement of Cash Flows INR crore FY2026 FY2025"),
             (72, 680, "Cash from operating activities"), (72, 650, "Profit after tax"),
             (400, 680, "209.00"), (480, 680, "180.00"),
             (400, 650, "150.00"), (480, 650, "130.00")]
    stream = DecodedStreamObject()
    stream.set_data("\n".join(f"BT /F1 12 Tf {x} {y} Td ({text}) Tj ET"
                              for x, y, text in texts).encode())
    writer.pages[18][NameObject("/Contents")] = writer._add_object(stream)
    target = BytesIO()
    writer.write(target)
    modes = []
    original = PageObject.extract_text

    def track(page, *args, **kwargs):
        modes.append(kwargs.get("extraction_mode", "plain"))
        return original(page, *args, **kwargs)

    monkeypatch.setattr(PageObject, "extract_text", track)
    excerpt = _extract_pdf_text(target.getvalue(), max_pages=4, max_chars=12000)
    assert modes.count("plain") == 30
    assert modes.count("layout") <= 4
    assert "[PDF page 19]" in excerpt
    assert "Consolidated Statement of Cash Flows INR crore FY2026 FY2025" in excerpt
    assert "Cash from operating activities 209.00 180.00" in excerpt
    assert "Profit after tax 150.00 130.00" in excerpt


def test_failed_extraction_is_not_cached(monkeypatch, tmp_path):
    cache = DocumentCache(tmp_path)
    pdf = _pdf_with_text("Profit after tax INR 100 crore")
    original = PageObject.extract_text

    def fail_layout(page, *args, **kwargs):
        if kwargs.get("extraction_mode") == "layout":
            raise ValueError("broken layout")
        return original(page, *args, **kwargs)

    monkeypatch.setattr(PageObject, "extract_text", fail_layout)
    with pytest.raises(ValueError, match="layout extraction failed"):
        _extract_pdf_text(pdf, max_pages=4, max_chars=12000, cache=cache)
    assert cache.load_excerpt(digest(pdf), max_pages=4, max_chars=12000) is None


def test_concurrent_writes_never_mix_manifest_and_document(tmp_path):
    cache = DocumentCache(tmp_path)
    documents = [CachedDocument(content=f"revision {i}".encode(), final_url=URL,
                                content_type="application/pdf", etag=f'"{i}"') for i in range(12)]

    def write_and_read(document):
        cache.save_document(URL, document)
        saved = cache.load_document(URL, max_bytes=1000)
        assert saved in documents

    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(write_and_read, documents))
    assert not list(tmp_path.rglob(".tmp-*"))
