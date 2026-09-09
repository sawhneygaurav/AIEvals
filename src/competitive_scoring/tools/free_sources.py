"""Policy-conservative sources for the fixed jewellery-company peer set.

The collector deliberately has a narrow trust boundary:

* network requests are limited to hard-coded NSE and company-owned pages;
* only a few relevant PDF links discovered on an official IR page are followed;
* Screener data is read only from exports that the user placed on disk; and
* Economic Times and Moneycontrol are optional *manual links*.  Their pages are
  never requested and no text from either publisher is stored here.

Every recoverable error becomes a warning and leaves the other records intact.
That makes the module suitable for an agent workflow where an IR site may be
temporarily unavailable without invalidating local filings or another issuer.
"""

from __future__ import annotations

import re
import zipfile
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from html import unescape
from html.parser import HTMLParser
from io import BytesIO
from pathlib import Path
from types import MappingProxyType
from typing import Any, Literal, Self
from urllib.parse import quote, unquote, urljoin, urlsplit, urlunsplit
from xml.etree import ElementTree

import httpx
import pandas as pd
from pypdf import PdfReader

from ..tracing import event, resource_id, span, traced
from .document_cache import CachedDocument, DocumentCache, digest

SourceType = Literal[
    "official_page",
    "official_document",
    "screener_export",
    "user_upload",
    "manual_reference_et",
    "manual_reference_moneycontrol",
]
Authority = Literal["primary_regulatory", "primary_company", "secondary"]

NSE_QUOTE_URL = "https://www.nseindia.com/get-quotes/equity?symbol={ticker}"
NSE_ANNOUNCEMENTS_URL = (
    "https://www.nseindia.com/companies-listing/corporate-filings-announcements"
    "?symbol={ticker}&tabIndex=equity"
)
ECONOMIC_TIMES_JEWELLERY_URL = "https://economictimes.indiatimes.com/topic/jewellery"
MONEYCONTROL_JEWELLERY_URL = "https://www.moneycontrol.com/news/tags/jewellery.html"

_NSE_HOSTS = ("www.nseindia.com", "nseindia.com", "nsearchives.nseindia.com")
_PAGE_BYTE_LIMIT = 2_000_000
_DOCUMENT_BYTE_LIMIT = 20_000_000
_LOCAL_FILE_BYTE_LIMIT = 20_000_000
_MAX_TABLE_ROWS = 80
_MAX_TABLE_COLUMNS = 60
_MAX_XLSX_UNCOMPRESSED_BYTES = 50_000_000
_MAX_EMBEDDED_PDF_LINKS = 100
# Annual reports for the supported issuers are normally well below this limit.
# We inspect every page up to the limit so a cash-flow table in the middle of a
# long report cannot be silently skipped.  An unusually large PDF is rejected
# explicitly (and therefore blocks publication) rather than being half-read.
_MAX_PDF_SCAN_PAGES = 600
_DOCUMENT_KEYWORD_WEIGHTS = {
    "annual report": 90,
    "financial result": 85,
    "quarterly result": 80,
    "investor presentation": 65,
    "quarterly update": 55,
    "earnings": 45,
    "transcript": 30,
    "press release": 25,
}
_PDF_FINANCIAL_KEYWORDS = (
    "statement of cash flows",
    "cash flow statement",
    "cash generated from operations",
    "cash from operating activities",
    "cash flows from operating activities",
    "net cash generated from operating activities",
    "net cash flow from operating activities",
    "net cash flows from operating activities",
    "financial statements",
    "financial ratios",
    "revenue from operations",
    "profit after tax",
    "net income",
    "profit for the period",
    "profit for the year",
    "profit attributable to owners",
    "debt equity",
    "interest coverage",
    "current ratio",
    "return on equity",
    "return on capital employed",
    "roce",
    "roe",
)
_PDF_GOVERNANCE_KEYWORDS = (
    "corporate governance",
    "board of directors",
    "independent directors",
    "related party transactions",
    "promoter shareholding",
    "management discussion and analysis",
    "risk management",
    "audit committee",
)

# Reserve evidence space for each concept separately. A filing can repeat
# "revenue" hundreds of times while mentioning operating cash flow only once.
# Treating all financial words as one score used to let the repeated word crowd
# the cash-flow page out of the excerpt sent to the agents.
_PDF_EVIDENCE_KEYWORD_GROUPS = (
    (
        "statement of cash flows",
        "cash flow statement",
        "cash generated from operations",
        "cash from operating activities",
        "cash flows from operating activities",
        "net cash generated from operating activities",
        "net cash flow from operating activities",
        "net cash flows from operating activities",
        "cash generated from / (used in) operations",
    ),
    (
        "profit after tax",
        "net income",
        "net profit",
        "profit for the period",
        "profit for the year",
        "profit attributable to owners",
        # OCR can damage the word "profit" in otherwise readable Indian
        # filing tables. The tax-expense row immediately precedes the net-of-tax
        # profit row, so it is a safe anchor for preserving that local window.
        "tax expense",
        "total tax expenses",
    ),
    ("return on equity", "roe"),
    ("return on capital employed", "roce"),
    ("debt equity", "debt/equity", "interest coverage", "current ratio"),
    ("revenue from operations", "sales growth", "revenue growth"),
)


@dataclass(frozen=True, slots=True)
class CompanyProfile:
    """Trusted URLs and hosts for one supported listed company."""

    ticker: str
    company_name: str
    investor_relations_url: str
    nse_quote_url: str
    nse_announcements_url: str
    company_hosts: tuple[str, ...]
    linked_document_hosts: tuple[str, ...]
    report_index_urls: tuple[str, ...] = ()


def _profile(
    ticker: str,
    company_name: str,
    investor_relations_url: str,
    *,
    company_hosts: tuple[str, ...],
    linked_document_hosts: tuple[str, ...] = (),
    report_index_urls: tuple[str, ...] = (),
) -> CompanyProfile:
    return CompanyProfile(
        ticker=ticker,
        company_name=company_name,
        investor_relations_url=investor_relations_url,
        nse_quote_url=NSE_QUOTE_URL.format(ticker=quote(ticker, safe="")),
        nse_announcements_url=NSE_ANNOUNCEMENTS_URL.format(ticker=quote(ticker, safe="")),
        company_hosts=company_hosts,
        linked_document_hosts=tuple(dict.fromkeys(company_hosts + linked_document_hosts)),
        report_index_urls=report_index_urls,
    )


# This is intentionally fixed rather than discovered from search results.  It
# prevents an untrusted page or model-generated hostname from becoming a fetch
# target.  CDN hosts below are followed only when linked by the corresponding
# first-party investor-relations page.
TRUSTED_COMPANY_PROFILES: Mapping[str, CompanyProfile] = MappingProxyType(
    {
        "PNGJL": _profile(
            "PNGJL",
            "P N Gadgil Jewellers Limited",
            "https://www.pngjewellers.com/pages/investors",
            company_hosts=("www.pngjewellers.com", "pngjewellers.com"),
            linked_document_hosts=("cdn.shopify.com",),
        ),
        "KALYANKJIL": _profile(
            "KALYANKJIL",
            "Kalyan Jewellers India Limited",
            "https://www.kalyanjewellers.net/investors.php",
            company_hosts=("www.kalyanjewellers.net", "kalyanjewellers.net"),
            report_index_urls=(
                "https://www.kalyanjewellers.net/investors/annual-report/annual-reports.php",
                "https://www.kalyanjewellers.net/investors/quarterly-results/quarterly-results.php",
            ),
        ),
        "SENCO": _profile(
            "SENCO",
            "Senco Gold Limited",
            "https://sencogoldanddiamonds.com/investor-relations",
            company_hosts=(
                "sencogoldanddiamonds.com",
                "www.sencogoldanddiamonds.com",
                "sencogold.com",
                "www.sencogold.com",
            ),
            linked_document_hosts=("sencowebfiles.s3.ap-south-1.amazonaws.com",),
        ),
        "THANGAMAYL": _profile(
            "THANGAMAYL",
            "Thangamayil Jewellery Limited",
            "https://www.thangamayil.com/corporate/investor-relationship/",
            company_hosts=("www.thangamayil.com", "thangamayil.com"),
            report_index_urls=(
                "https://www.thangamayil.com/corporate/annual-reports/",
                "https://www.thangamayil.com/corporate/quarterly-reports/",
            ),
        ),
    }
)


@dataclass(frozen=True, slots=True)
class FreeSourceRecord:
    """A compact source excerpt plus provenance for downstream research agents."""

    ticker: str
    source_type: SourceType
    title: str
    url: str
    excerpt: str
    publisher: str
    is_private: bool = False
    retrieved_at: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class FreeSourceCollection:
    """Partial results and non-fatal collection warnings."""

    records: tuple[FreeSourceRecord, ...]
    warnings: tuple[str, ...]

    def for_ticker(self, ticker: str) -> tuple[FreeSourceRecord, ...]:
        """Return records for ``ticker`` using the collector's normalization."""

        normalized = ticker.strip().upper()
        return tuple(record for record in self.records if record.ticker == normalized)


@dataclass(frozen=True, slots=True)
class _Link:
    url: str
    text: str


class _PageParser(HTMLParser):
    """Extract human-visible text and links without executing page code."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title_parts: list[str] = []
        self.text_parts: list[str] = []
        self.links: list[_Link] = []
        self._ignored_depth = 0
        self._in_title = False
        self._active_href: str | None = None
        self._active_link_text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        lowered = tag.lower()
        if lowered in {"script", "style", "noscript", "svg"}:
            self._ignored_depth += 1
            return
        if self._ignored_depth:
            return
        if lowered == "title":
            self._in_title = True
        if lowered == "a":
            attributes = dict(attrs)
            self._active_href = attributes.get("href")
            self._active_link_text = []

    def handle_endtag(self, tag: str) -> None:
        lowered = tag.lower()
        if lowered in {"script", "style", "noscript", "svg"} and self._ignored_depth:
            self._ignored_depth -= 1
            return
        if self._ignored_depth:
            return
        if lowered == "title":
            self._in_title = False
        if lowered == "a" and self._active_href:
            self.links.append(
                _Link(url=self._active_href, text=_clean_text(" ".join(self._active_link_text)))
            )
            self._active_href = None
            self._active_link_text = []

    def handle_data(self, data: str) -> None:
        if self._ignored_depth:
            return
        cleaned = _clean_text(data)
        if not cleaned:
            return
        self.text_parts.append(cleaned)
        if self._in_title:
            self.title_parts.append(cleaned)
        if self._active_href is not None:
            self._active_link_text.append(cleaned)


class FreeSourceCollector:
    """Collect first-party web excerpts and explicitly supplied local data.

    ``transport`` exists for deterministic tests.  Production requests use an
    identifiable user agent, strict byte limits, and validated HTTPS redirects.
    """

    def __init__(
        self,
        *,
        timeout: float = 20.0,
        transport: httpx.BaseTransport | None = None,
        max_documents_per_company: int = 3,
        max_excerpt_chars: int = 12_000,
        max_pdf_pages: int = 20,
    ) -> None:
        if timeout <= 0:
            raise ValueError("timeout must be greater than zero")
        if not 0 <= max_documents_per_company <= 20:
            raise ValueError("max_documents_per_company must be between zero and 20")
        if not 500 <= max_excerpt_chars <= 100_000:
            raise ValueError("max_excerpt_chars must be between 500 and 100000")
        if not 1 <= max_pdf_pages <= 100:
            raise ValueError("max_pdf_pages must be between one and 100")

        self._client = httpx.Client(
            timeout=timeout,
            transport=transport,
            headers={
                "User-Agent": "competitive-scoring/0.1 (first-party investor research)",
                "Accept-Language": "en-IN,en;q=0.9",
            },
        )
        self._max_documents = max_documents_per_company
        self._max_excerpt_chars = max_excerpt_chars
        self._max_pdf_pages = max_pdf_pages
        self._document_cache = DocumentCache.from_env()

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    @traced("sources.official_and_uploads")
    def collect(
        self,
        tickers: Iterable[str] | None = None,
        *,
        screener_export_dir: str | Path | None = None,
        company_documents_dir: str | Path | None = None,
        include_et_references: bool = False,
        include_moneycontrol_references: bool = False,
    ) -> FreeSourceCollection:
        """Collect available records; individual failures never abort the batch.

        ET and Moneycontrol flags add direct manual links with blank excerpts.
        They do not trigger HTTP calls.  Screener is likewise never contacted;
        ``screener_export_dir`` must contain a user-downloaded CSV/XLSX file.
        """

        requested, warnings = _normalize_tickers(tickers)
        records: list[FreeSourceRecord] = []

        for ticker in requested:
            profile = TRUSTED_COMPANY_PROFILES.get(ticker)
            if profile is None:
                warnings.append(f"Unsupported ticker {ticker!r}; no network request was made.")
                continue
            official_records, official_warnings = self._collect_official(profile)
            records.extend(official_records)
            warnings.extend(official_warnings)

        supported = [ticker for ticker in requested if ticker in TRUSTED_COMPANY_PROFILES]
        if screener_export_dir is not None:
            local_records, local_warnings = self._collect_screener_exports(
                Path(screener_export_dir), supported
            )
            records.extend(local_records)
            warnings.extend(local_warnings)
        if company_documents_dir is not None:
            local_records, local_warnings = self._collect_company_documents(
                Path(company_documents_dir), supported
            )
            records.extend(local_records)
            warnings.extend(local_warnings)

        for ticker in supported:
            profile = TRUSTED_COMPANY_PROFILES[ticker]
            if include_et_references:
                records.append(
                    _manual_reference(
                        profile,
                        source_type="manual_reference_et",
                        title=f"Economic Times jewellery topic — manual review for {profile.company_name}",
                        url=ECONOMIC_TIMES_JEWELLERY_URL,
                        publisher="The Economic Times",
                        tags=("manual_only", "news", "jewellery"),
                    )
                )
            if include_moneycontrol_references:
                records.append(
                    _manual_reference(
                        profile,
                        source_type="manual_reference_moneycontrol",
                        title=f"Moneycontrol jewellery topic — manual review for {profile.company_name}",
                        url=MONEYCONTROL_JEWELLERY_URL,
                        publisher="Moneycontrol",
                        tags=("manual_only", "news", "jewellery"),
                    )
                )

        return FreeSourceCollection(records=tuple(records), warnings=tuple(warnings))

    @traced("sources.official_company")
    def _collect_official(
        self, profile: CompanyProfile
    ) -> tuple[list[FreeSourceRecord], list[str]]:
        records: list[FreeSourceRecord] = []
        warnings: list[str] = []

        page_specs = (
            (
                profile.nse_quote_url,
                f"NSE quote page for {profile.ticker}",
                "NSE India",
                _NSE_HOSTS,
                "primary_regulatory",
                ("nse", "quote", "official"),
                False,
            ),
            (
                profile.nse_announcements_url,
                f"NSE corporate announcements for {profile.ticker}",
                "NSE India",
                _NSE_HOSTS,
                "primary_regulatory",
                ("nse", "corporate_filings", "official"),
                False,
            ),
            (
                profile.investor_relations_url,
                f"{profile.company_name} investor relations",
                profile.company_name,
                profile.company_hosts,
                "primary_company",
                ("investor_relations", "official"),
                True,
            ),
        )

        discovery_links: list[_Link] = []
        for url, fallback_title, publisher, hosts, authority, tags, retain_links in page_specs:
            record, links, page_warnings = self._fetch_official_page(
                profile,
                url=url,
                fallback_title=fallback_title,
                publisher=publisher,
                allowed_hosts=hosts,
                authority=authority,
                tags=tags,
                discover_documents=retain_links,
            )
            records.append(record)
            warnings.extend(page_warnings)
            if retain_links:
                discovery_links.extend(links)

        # Some issuer home/IR pages only link to a report catalogue, while the
        # actual PDF URLs live in that catalogue's HTML or embedded JSON.  We
        # follow only this fixed, reviewed list; arbitrary links discovered on
        # a page never become a new page-fetch target.
        for index_number, index_url in enumerate(profile.report_index_urls, start=1):
            record, links, page_warnings = self._fetch_official_page(
                profile,
                url=index_url,
                fallback_title=(f"{profile.company_name} official report index {index_number}"),
                publisher=profile.company_name,
                allowed_hosts=profile.company_hosts,
                authority="primary_company",
                tags=("investor_relations", "official", "report_index"),
                discover_documents=True,
            )
            records.append(record)
            warnings.extend(page_warnings)
            discovery_links.extend(links)

        document_links = _select_document_links(
            profile.investor_relations_url,
            tuple(discovery_links),
            profile.linked_document_hosts,
            limit=self._max_documents,
        )
        for link in document_links:
            record, warning = self._fetch_official_document(profile, link)
            records.append(record)
            if warning:
                warnings.append(warning)

        if self._max_documents and discovery_links and not document_links:
            warnings.append(
                f"{profile.ticker}: no safe relevant PDF links were found on the official "
                "IR/report pages."
            )
        return records, warnings

    @traced("sources.page")
    def _fetch_official_page(
        self,
        profile: CompanyProfile,
        *,
        url: str,
        fallback_title: str,
        publisher: str,
        allowed_hosts: tuple[str, ...],
        authority: Authority,
        tags: tuple[str, ...],
        discover_documents: bool = False,
    ) -> tuple[FreeSourceRecord, tuple[_Link, ...], list[str]]:
        retrieved_at = _now_iso()
        metadata = _metadata(authority, tags, retrieval_status="failed")
        try:
            response = self._safe_get(url, allowed_hosts, byte_limit=_PAGE_BYTE_LIMIT)
            parser = _parse_html(response.content, response.encoding)
            # Resolve anchor paths against the page that contained them.  This
            # matters for the fixed report catalogues, whose relative PDF links
            # are not relative to the top-level investor-relations URL.
            links = [_Link(url=urljoin(url, link.url), text=link.text) for link in parser.links]
            if discover_documents:
                links.extend(
                    _extract_embedded_pdf_links(
                        response.content,
                        response.encoding,
                        profile.linked_document_hosts,
                    )
                )
            title = _clean_text(" ".join(parser.title_parts)) or fallback_title
            excerpt = _truncate(_clean_text(" ".join(parser.text_parts)), self._max_excerpt_chars)
            if not excerpt and not links:
                raise ValueError("the page contained no extractable text")
            metadata = _metadata(
                authority,
                tags,
                retrieval_status="retrieved" if excerpt else "reference_only",
                content_type=response.headers.get("content-type", ""),
            )
            warnings: list[str] = []
            # NSE's dynamic pages sometimes return only a generic shell.  That
            # boilerplate is not evidence about the requested security, so keep
            # the official URL but do not pass the text to a downstream model.
            if authority == "primary_regulatory" and profile.ticker not in excerpt.upper():
                excerpt = ""
                metadata = _metadata(
                    authority,
                    tags,
                    retrieval_status="reference_only",
                    content_type=response.headers.get("content-type", ""),
                )
                # This is the normal response shape for NSE's client-rendered
                # announcements page, not a failed collection step.  The blank
                # reference remains visible for manual verification, but it is
                # deliberately not emitted as a runtime warning.
            return (
                FreeSourceRecord(
                    ticker=profile.ticker,
                    source_type="official_page",
                    title=title,
                    url=url,
                    excerpt=excerpt,
                    publisher=publisher,
                    retrieved_at=retrieved_at,
                    metadata=metadata,
                ),
                tuple(links),
                warnings,
            )
        except Exception as exc:  # noqa: BLE001 - a source failure must remain partial
            warning = f"{profile.ticker}: could not retrieve {url}: {_safe_error(exc)}"
            return (
                FreeSourceRecord(
                    ticker=profile.ticker,
                    source_type="official_page",
                    title=fallback_title,
                    url=url,
                    excerpt="",
                    publisher=publisher,
                    retrieved_at=retrieved_at,
                    metadata=metadata,
                ),
                (),
                [warning],
            )

    @traced("sources.document")
    def _fetch_official_document(
        self, profile: CompanyProfile, link: _Link
    ) -> tuple[FreeSourceRecord, str | None]:
        retrieved_at = _now_iso()
        title = link.text or Path(urlsplit(link.url).path).name or "Official company document"
        metadata = _metadata(
            "primary_company",
            ("official", "investor_relations", "document"),
            retrieval_status="failed",
        )
        try:
            document, cache_allowed = self._get_document(link.url, profile.linked_document_hosts)
            excerpt = _extract_pdf_text(
                document.content,
                max_pages=self._max_pdf_pages,
                max_chars=self._max_excerpt_chars,
                cache=self._document_cache if cache_allowed else None,
            )
            if not excerpt:
                raise ValueError("the PDF contained no extractable text")
            metadata = _metadata(
                "primary_company",
                ("official", "investor_relations", "document", "pdf"),
                retrieval_status="retrieved",
                content_type=document.content_type,
            )
            return (
                FreeSourceRecord(
                    ticker=profile.ticker,
                    source_type="official_document",
                    title=title,
                    url=link.url,
                    excerpt=excerpt,
                    publisher=profile.company_name,
                    retrieved_at=retrieved_at,
                    metadata=metadata,
                ),
                None,
            )
        except Exception as exc:  # noqa: BLE001 - leave a usable direct reference
            return (
                FreeSourceRecord(
                    ticker=profile.ticker,
                    source_type="official_document",
                    title=title,
                    url=link.url,
                    excerpt="",
                    publisher=profile.company_name,
                    retrieved_at=retrieved_at,
                    metadata=metadata,
                ),
                (
                    f"{profile.ticker}: could not extract official document {link.url}: "
                    f"{_safe_error(exc)}"
                ),
            )

    def _get_document(
        self, url: str, allowed_hosts: tuple[str, ...]
    ) -> tuple[CachedDocument, bool]:
        # Always visit the current allowlisted origin. Cached bytes alone never
        # establish freshness, and an unavailable origin must remain a warning.
        url = _validated_url(url, allowed_hosts)
        cache = self._document_cache
        cached = cache.load_document(url, max_bytes=_DOCUMENT_BYTE_LIMIT) if cache else None
        if cached:
            try:
                _validated_url(cached.final_url, allowed_hosts)
            except ValueError:
                event("diagnostic", code="DOCUMENT_CACHE_READ_FAILED")
                cached = None
        response = self._safe_get(
            url, allowed_hosts, byte_limit=_DOCUMENT_BYTE_LIMIT,
            conditional_url=cached.final_url if cached else None,
            validators=cached.validators if cached else None,
        )
        cache_allowed = "no-store" not in {
            directive.strip().lower() for directive in response.headers.get("cache-control", "").split(",")
        }
        if response.status_code == 304:
            # _safe_get only accepts 304 for the exact URL sent validators.
            assert cached is not None
            document = CachedDocument(
                content=cached.content, final_url=str(response.url),
                content_type=cached.content_type,
                etag=response.headers.get("etag", cached.etag),
                last_modified=response.headers.get("last-modified", cached.last_modified),
            )
            outcome = "revalidated"
        else:
            document = CachedDocument(
                content=response.content, final_url=str(response.url),
                content_type=response.headers.get("content-type", ""),
                etag=response.headers.get("etag", ""),
                last_modified=response.headers.get("last-modified", ""),
            )
            outcome = "downloaded"
        event("document.cache", outcome=outcome, resource_id=resource_id(url),
              bytes=len(document.content))
        if cache:
            if cache_allowed:
                # Skip rewriting a large blob when its validated metadata is unchanged.
                if document != cached:
                    cache.save_document(url, document)
            else:
                cache.invalidate_document(url)
        return document, cache_allowed

    def _safe_get(
        self, url: str, allowed_hosts: tuple[str, ...], *, byte_limit: int,
        conditional_url: str | None = None, validators: dict[str, str] | None = None,
    ) -> httpx.Response:
        """GET an allowlisted HTTPS URL while validating every redirect hop."""

        current = _validated_url(url, allowed_hosts)
        for _hop in range(4):
            headers = (
                validators if conditional_url and httpx.URL(current) == httpx.URL(conditional_url)
                else None
            )
            with span("sources.http", host=urlsplit(current).hostname,
                      resource_id=resource_id(current), attempt=_hop + 1) as details:
                response = self._client.get(current, follow_redirects=False, headers=headers)
                details.update(status_code=response.status_code, bytes=len(response.content))
                # Keep redirects unchanged, but classify failing HTTP responses in the trace.
                if response.status_code >= 400:
                    response.raise_for_status()
                if response.status_code == 304:
                    if not headers:
                        raise ValueError("unexpected 304 without a validated cache entry")
                    return response
            if response.status_code in {301, 302, 303, 307, 308}:
                location = response.headers.get("location")
                if not location:
                    raise ValueError("redirect did not include a Location header")
                current = _validated_url(urljoin(current, location), allowed_hosts)
                continue
            response.raise_for_status()
            content_length = response.headers.get("content-length")
            if content_length and int(content_length) > byte_limit:
                raise ValueError(f"response exceeds the {byte_limit}-byte limit")
            if len(response.content) > byte_limit:
                raise ValueError(f"response exceeds the {byte_limit}-byte limit")
            return response
        raise ValueError("too many redirects")

    def _collect_screener_exports(
        self, directory: Path, tickers: list[str]
    ) -> tuple[list[FreeSourceRecord], list[str]]:
        records: list[FreeSourceRecord] = []
        warnings: list[str] = []
        files, discovery_warning = _discover_ticker_files(
            directory, tickers, suffixes={".csv", ".xlsx"}
        )
        if discovery_warning:
            return records, [f"Screener exports: {discovery_warning}"]

        for ticker, path in files:
            try:
                frame = _read_table(path)
                excerpt = _frame_excerpt(frame, self._max_excerpt_chars)
                records.append(
                    FreeSourceRecord(
                        ticker=ticker,
                        source_type="screener_export",
                        title=f"User-provided Screener export: {path.name}",
                        url=path.resolve().as_uri(),
                        excerpt=excerpt,
                        publisher="User-provided Screener export",
                        is_private=True,
                        retrieved_at=_now_iso(),
                        metadata=_metadata(
                            "secondary",
                            ("user_supplied", "private", "screener_export", "untrusted_input"),
                            rows=int(frame.shape[0]),
                            columns=int(frame.shape[1]),
                            file_name=path.name,
                        ),
                    )
                )
            except Exception as exc:  # noqa: BLE001 - continue with the other exports
                warnings.append(
                    f"{ticker}: could not read Screener export {path.name}: {_safe_error(exc)}"
                )
        return records, warnings

    def _collect_company_documents(
        self, directory: Path, tickers: list[str]
    ) -> tuple[list[FreeSourceRecord], list[str]]:
        records: list[FreeSourceRecord] = []
        warnings: list[str] = []
        files, discovery_warning = _discover_company_documents(directory, tickers)
        if discovery_warning:
            return records, [f"Company documents: {discovery_warning}"]

        for ticker, path in files:
            try:
                excerpt, detail = self._read_company_document(path)
                records.append(
                    FreeSourceRecord(
                        ticker=ticker,
                        source_type="user_upload",
                        title=f"User-provided company document: {path.name}",
                        url=path.resolve().as_uri(),
                        excerpt=excerpt,
                        publisher="User-provided document",
                        is_private=True,
                        retrieved_at=_now_iso(),
                        metadata=_metadata(
                            "secondary",
                            ("user_supplied", "private", "company_document", "untrusted_input"),
                            file_name=path.name,
                            **detail,
                        ),
                    )
                )
            except Exception as exc:  # noqa: BLE001 - continue with remaining files
                warnings.append(
                    f"{ticker}: could not read local document {path.name}: {_safe_error(exc)}"
                )
        return records, warnings

    @traced("sources.upload")
    def _read_company_document(self, path: Path) -> tuple[str, dict[str, Any]]:
        _validate_local_file(path)
        suffix = path.suffix.lower()
        if suffix == ".pdf":
            text = _extract_pdf_text(
                path.read_bytes(),
                max_pages=self._max_pdf_pages,
                max_chars=self._max_excerpt_chars,
            )
            if not text:
                raise ValueError("the PDF contained no extractable text")
            return text, {"format": "pdf"}
        if suffix == ".txt":
            text = _truncate(
                _clean_text(path.read_text(encoding="utf-8", errors="replace")),
                self._max_excerpt_chars,
            )
            if not text:
                raise ValueError("the text file was empty")
            return text, {"format": "txt"}
        if suffix == ".csv":
            frame = _read_table(path)
            return _frame_excerpt(frame, self._max_excerpt_chars), {
                "format": "csv",
                "rows": int(frame.shape[0]),
                "columns": int(frame.shape[1]),
            }
        raise ValueError(f"unsupported file type {suffix!r}")


def collect_free_sources(
    tickers: Iterable[str] | None = None,
    *,
    screener_export_dir: str | Path | None = None,
    company_documents_dir: str | Path | None = None,
    include_et_references: bool = False,
    include_moneycontrol_references: bool = False,
    timeout: float = 20.0,
    transport: httpx.BaseTransport | None = None,
    max_documents_per_company: int = 3,
) -> FreeSourceCollection:
    """One-call convenience wrapper that also closes its HTTP client."""

    with FreeSourceCollector(
        timeout=timeout,
        transport=transport,
        max_documents_per_company=max_documents_per_company,
    ) as collector:
        return collector.collect(
            tickers,
            screener_export_dir=screener_export_dir,
            company_documents_dir=company_documents_dir,
            include_et_references=include_et_references,
            include_moneycontrol_references=include_moneycontrol_references,
        )


def _normalize_tickers(tickers: Iterable[str] | None) -> tuple[list[str], list[str]]:
    if tickers is None:
        return list(TRUSTED_COMPANY_PROFILES), []
    if isinstance(tickers, str):
        tickers = (tickers,)
    normalized: list[str] = []
    warnings: list[str] = []
    for raw_ticker in tickers:
        if not isinstance(raw_ticker, str) or not raw_ticker.strip():
            warnings.append(f"Ignored invalid ticker value {raw_ticker!r}.")
            continue
        ticker = raw_ticker.strip().upper()
        if ticker not in normalized:
            normalized.append(ticker)
    return normalized, warnings


@traced("html.parse")
def _parse_html(content: bytes, encoding: str | None) -> _PageParser:
    parser = _PageParser()
    parser.feed(content.decode(encoding or "utf-8", errors="replace"))
    parser.close()
    return parser


def _extract_embedded_pdf_links(
    content: bytes,
    encoding: str | None,
    allowed_hosts: tuple[str, ...],
) -> tuple[_Link, ...]:
    """Find allowlisted absolute PDF URLs embedded in page HTML or JSON.

    Several issuer sites hydrate their report lists from JSON inside a script
    element instead of rendering ordinary ``<a>`` tags server-side.  The HTML
    parser intentionally ignores scripts as prose, so this narrow URL scanner
    recovers only HTTPS PDF targets and validates every host before returning
    them.  The page response and result count are both bounded elsewhere/here.
    """

    raw = content.decode(encoding or "utf-8", errors="replace")
    # Normalize the common JSON/HTML encodings without evaluating JavaScript.
    normalized = unescape(raw)
    normalized = re.sub(r"\\u002[fF]", "/", normalized)
    normalized = re.sub(r"\\u0026", "&", normalized, flags=re.IGNORECASE)
    normalized = normalized.replace(r"\/", "/")
    pattern = re.compile(r"https://[^\s\"'<>\\]+?\.pdf(?:\?[^\s\"'<>\\]*)?", re.IGNORECASE)

    links: list[_Link] = []
    seen: set[str] = set()
    for match in pattern.finditer(normalized):
        candidate = _without_fragment(match.group(0).rstrip("),.;]}"))
        try:
            candidate = _validated_url(candidate, allowed_hosts)
        except ValueError:
            continue
        if candidate in seen:
            continue
        seen.add(candidate)
        filename = unquote(Path(urlsplit(candidate).path).name)
        links.append(_Link(url=candidate, text=_clean_text(filename)))
        if len(links) == _MAX_EMBEDDED_PDF_LINKS:
            break
    return tuple(links)


def _select_document_links(
    base_url: str,
    links: tuple[_Link, ...],
    allowed_hosts: tuple[str, ...],
    *,
    limit: int,
) -> tuple[_Link, ...]:
    if limit == 0:
        return ()
    candidates: list[tuple[int, int, int, _Link, str]] = []
    seen: set[str] = set()
    for index, link in enumerate(links):
        absolute = _without_fragment(urljoin(base_url, link.url))
        split = urlsplit(absolute)
        # Use only the anchor text, filename, and query.  Including the parent
        # path (for example ``/annual-reports/``) used to misclassify every PDF
        # on that page—including secretarial-compliance files—as an annual
        # report.
        raw_haystack = unquote(f"{link.text} {Path(split.path).name} {split.query}").lower()
        haystack = re.sub(r"[_-]+", " ", raw_haystack)
        if ".pdf" not in split.path.lower():
            continue
        try:
            absolute = _validated_url(absolute, allowed_hosts)
        except ValueError:
            continue
        if absolute in seen:
            continue
        seen.add(absolute)
        relevance = _document_relevance_score(haystack)
        if relevance == 0:
            continue
        # A more recent relevant filing must not lose merely because an older
        # report appeared first in the site's HTML.  Relevance breaks ties
        # between documents from the same reporting year.
        report_year = _document_report_year(raw_haystack)
        candidates.append(
            (
                -report_year,
                -relevance,
                index,
                _Link(url=absolute, text=link.text),
                haystack,
            )
        )
    candidates.sort(key=lambda item: (item[0], item[1], item[2]))

    # Report catalogues often retain ten or more years of filings.  Downloading
    # a stale PDF is both slow and actively harmful: it can add a collection
    # warning (when the old file has disappeared) and can distract the agents
    # from the current evidence.  Apply the lookback relative to the newest
    # dated candidate on the page rather than the wall clock, so the selector
    # also behaves predictably for archived/test catalogues.  Undated links are
    # retained because we cannot safely prove that they are stale.
    newest_report_year = max((-item[0] for item in candidates), default=0)
    if newest_report_year:
        minimum_report_year = newest_report_year - 2
        candidates = [
            item
            for item in candidates
            if (report_year := -item[0]) == 0 or report_year >= minimum_report_year
        ]

    # Reserve one slot for the latest annual report and one for the latest
    # periodic result.  Pure recency sorting previously selected three quarter
    # updates and crowded out the only document with multi-year cash flow and
    # governance evidence.
    selected: list[_Link] = []
    selected_families: set[str] = set()

    def document_family(text: str) -> str:
        """Return a coarse filing family used to avoid redundant downloads."""

        if "annual report" in text:
            return "annual"
        # Half-year and nine-month statements normally contain a cash-flow
        # statement, while a newer single-quarter result often does not. Keep
        # one of these alongside the latest quarter instead of treating it as a
        # redundant periodic filing.
        if "half year" in text or "half yearly" in text:
            return "half_year"
        if "nine month" in text or "9m" in text:
            return "nine_months"
        if (
            any(
                label in text
                for label in (
                    "financial result",
                    "quarterly result",
                    "quarterly update",
                    "earnings",
                )
            )
            or re.search(r"(?<!\w)q[1-4](?!\w)", text) is not None
        ):
            return "periodic"
        if "investor presentation" in text:
            return "presentation"
        if "transcript" in text:
            return "transcript"
        if "press release" in text:
            return "press_release"
        return "other"

    def add_first(predicate: Any) -> None:
        for item in candidates:
            link = item[3]
            if len(selected) == limit:
                return
            if link not in selected and predicate(item[4]):
                selected.append(link)
                selected_families.add(document_family(item[4]))
                return

    add_first(lambda text: "annual report" in text)
    add_first(
        lambda text: (
            any(
                label in text
                for label in (
                    "financial result",
                    "quarterly result",
                    "quarterly update",
                    "earnings",
                )
            )
            or re.search(r"(?<!\w)q[1-4](?!\w)", text) is not None
        )
    )
    add_first(
        lambda text: any(
            label in text for label in ("half year", "half yearly", "nine month", "9m")
        )
    )
    for item in candidates:
        if len(selected) == limit:
            break
        family = document_family(item[4])
        # One document per family avoids duplicate annual/quarter downloads,
        # while still allowing one cash-bearing half-year or nine-month filing.
        if item[3] not in selected and family not in selected_families:
            selected.append(item[3])
            selected_families.add(family)
    return tuple(selected)


def _document_relevance_score(haystack: str) -> int:
    return max(
        (weight for keyword, weight in _DOCUMENT_KEYWORD_WEIGHTS.items() if keyword in haystack),
        default=0,
    )


def _document_report_year(haystack: str) -> int:
    """Return the newest plausible calendar/FY year mentioned by a link."""

    years = [int(value) for value in re.findall(r"(?<!\d)(20\d{2})(?!\d)", haystack)]
    for first, second in re.findall(r"\b(20\d{2})\s*[-/]\s*(\d{2})(?!\d)", haystack):
        century = int(first) // 100 * 100
        years.append(century + int(second))
    for first, second in re.findall(
        r"\bfy[\s'_-]*(\d{2})(?:\s*[-/]\s*(\d{2}))?", haystack, flags=re.IGNORECASE
    ):
        years.append(2000 + int(first))
        if second:
            years.append(2000 + int(second))
    return max(years, default=0)


def _validated_url(url: str, allowed_hosts: tuple[str, ...]) -> str:
    split = urlsplit(url)
    hostname = (split.hostname or "").lower().rstrip(".")
    allowed = {host.lower().rstrip(".") for host in allowed_hosts}
    try:
        port = split.port
    except ValueError as exc:
        raise ValueError("URL has an invalid port") from exc
    if split.scheme.lower() != "https":
        raise ValueError("only HTTPS URLs are allowed")
    if split.username or split.password or (port is not None and port != 443):
        raise ValueError("URL credentials and non-default ports are not allowed")
    if hostname not in allowed:
        raise ValueError(f"host {hostname!r} is not allowlisted")
    return urlunsplit(("https", split.netloc, split.path or "/", split.query, ""))


def _without_fragment(url: str) -> str:
    split = urlsplit(url)
    return urlunsplit((split.scheme, split.netloc, split.path, split.query, ""))


@traced("pdf.extract")
def _extract_pdf_text(
    content: bytes, *, max_pages: int, max_chars: int, cache: DocumentCache | None = None
) -> str:
    if len(content) > _DOCUMENT_BYTE_LIMIT:
        raise ValueError("PDF exceeds the local byte limit")
    content_hash = digest(content)
    if cache:
        cached = cache.load_excerpt(content_hash, max_pages=max_pages, max_chars=max_chars)
        event("pdf.cache", outcome="hit" if cached is not None else "miss")
        if cached is not None:
            return cached
    reader = PdfReader(BytesIO(content), strict=False)
    page_count = len(reader.pages)
    event("pdf.input", pages=page_count, bytes=len(content))
    if page_count == 0:
        return ""

    if page_count > _MAX_PDF_SCAN_PAGES:
        raise ValueError(
            f"PDF has {page_count} pages, above the {_MAX_PDF_SCAN_PAGES}-page safety limit"
        )
    scan_indices = list(range(page_count))

    # Scan every page in plain mode to find evidence anywhere in the filing.
    # Layout extraction is expensive; only selected pages need that second pass.
    page_text: dict[int, str] = {}
    page_output_text: dict[int, str] = {}
    cacheable = True
    with span("pdf.scan_pages"):
        for index in scan_indices:
            try:
                page = reader.pages[index]
                text = _clean_text(page.extract_text() or "")
            except Exception as exc:  # noqa: BLE001 - retain other pages
                cacheable = False
                event("diagnostic", code="PDF_PAGE_EXTRACTION_FAILED",
                      error_type=type(exc).__name__)
                continue
            if text:
                page_text[index] = text
    if not page_text:
        return ""

    scores = {index: _pdf_page_scores(text) for index, text in page_text.items()}
    selected: list[int] = []
    selection_limit = min(max_pages, 4)

    def add(index: int | None) -> None:
        if index is not None and index not in selected and len(selected) < selection_limit:
            selected.append(index)

    financial_pages = sorted(
        page_text,
        key=lambda index: (-scores[index][0], -scores[index][2], index),
    )
    governance_pages = sorted(
        page_text,
        key=lambda index: (-scores[index][1], -scores[index][2], index),
    )
    # Put the best cash-flow page first and explicitly prefer consolidated
    # statements.  The output allocator below therefore gives this rare,
    # essential evidence a useful window even in a dense annual report.
    cash_aliases = _PDF_EVIDENCE_KEYWORD_GROUPS[0]
    cash_pages = [
        index
        for index in page_text
        if _page_keyword_count(page_text[index], cash_aliases)
    ]
    best_cash_index = max(
        cash_pages or page_text,
        key=lambda index: (
            _has_consolidated_label(page_text[index]),
            _page_keyword_count(page_text[index], cash_aliases),
            index,
        ),
    )
    if _page_keyword_count(page_text[best_cash_index], cash_aliases):
        add(best_cash_index)

    # Preserve the main results table as well as the cash-flow page. Auditor
    # reports repeat words such as "net profit" for small subsidiaries and used
    # to win the keyword count, hiding the actual consolidated PAT row.
    pnl_pages = [
        index
        for index, text in page_text.items()
        if "revenue from operations" in text.lower()
        and re.search(r"(?i)tax\s+exp", text) is not None
    ]
    if pnl_pages:
        add(
            max(
                pnl_pages,
                key=lambda index: (
                    _has_consolidated_label(page_text[index]),
                    len(re.findall(r"\d", page_text[index])),
                    index,
                ),
            )
        )

    # Preserve one governance page next so very small page limits still retain
    # both financial and management evidence.
    add(governance_pages[0] if scores[governance_pages[0]][1] else None)

    # Give the remaining rare concepts their own chance to enter the excerpt.
    for aliases in _PDF_EVIDENCE_KEYWORD_GROUPS[1:]:
        best_index = max(
            page_text,
            key=lambda index: (
                _page_keyword_count(page_text[index], aliases),
                _has_consolidated_label(page_text[index]),
                index,
            ),
        )
        if _page_keyword_count(page_text[best_index], aliases):
            add(best_index)

    add(financial_pages[0] if scores[financial_pages[0]][0] else None)

    # Then retain the strongest remaining evidence pages, followed by opening
    # pages for document identity/context when capacity remains.
    relevant_page_limit = selection_limit
    for index in sorted(page_text, key=lambda item: (-scores[item][2], item)):
        if scores[index][2] == 0:
            break
        add(index)
        if len(selected) == relevant_page_limit:
            break
    # Two opening pages are enough for document identity/context; filling every
    # unused slot with narrative pages only dilutes the evidence budget.
    for index in sorted(page_text)[:2]:
        add(index)

    # Preserve financial table rows, headings, years and units in the same mode
    # used before this optimization. If a selected page cannot be laid out, fail
    # this document rather than treating potentially detached numbers as evidence.
    with span("pdf.layout_selected", pages=len(selected)):
        for index in selected:
            try:
                layout = _clean_text(reader.pages[index].extract_text(extraction_mode="layout") or "")
                page_output_text[index] = layout or page_text[index]
            except Exception as exc:
                event("diagnostic", code="PDF_LAYOUT_EXTRACTION_FAILED",
                      error_type=type(exc).__name__)
                raise ValueError("selected PDF evidence page layout extraction failed") from exc

    # Do not concatenate full pages and cut only at the end. That old approach
    # meant one dense ratio page could consume the entire 12k character budget,
    # even though a later selected page contained the required cash-flow row.
    marker_allowance = sum(len(f"[PDF page {index + 1}]  ") for index in selected)
    text_budget = max(1, max_chars - marker_allowance)
    weights = [_pdf_excerpt_weight(page_text[index]) for index in selected]
    total_weight = sum(weights)
    chunks = [
        (
            f"[PDF page {index + 1}] "
            f"{_page_evidence_excerpt(page_output_text[index], max(80, text_budget * weight // total_weight))}"
        )
        for index, weight in zip(selected, weights, strict=True)
    ]
    excerpt = _truncate(_clean_text(" ".join(chunks)), max_chars)
    if cache and cacheable and excerpt:
        cache.save_excerpt(content_hash, excerpt, max_pages=max_pages, max_chars=max_chars)
    return excerpt


def _pdf_page_scores(text: str) -> tuple[int, int, int]:
    lowered = text.lower().replace("-", " ")
    financial = sum(lowered.count(keyword) for keyword in _PDF_FINANCIAL_KEYWORDS)
    governance = sum(lowered.count(keyword) for keyword in _PDF_GOVERNANCE_KEYWORDS)
    return financial, governance, financial + governance


def _page_keyword_count(text: str, aliases: tuple[str, ...]) -> int:
    """Count one concept's aliases after normalizing common PDF punctuation."""

    lowered = text.lower().replace("-", " ")
    return sum(lowered.count(alias) for alias in aliases)


def _has_consolidated_label(text: str) -> bool:
    """Recognize both normal and common PDF-spaced forms of 'consolidated'."""

    return re.search(r"(?i)consol\s*idat\s*ed", text) is not None


def _pdf_excerpt_weight(text: str) -> int:
    """Allocate more excerpt characters to high-value statement pages."""

    weight = 1
    for index, aliases in enumerate(_PDF_EVIDENCE_KEYWORD_GROUPS):
        if not _page_keyword_count(text, aliases):
            continue
        # Cash-flow and PAT pages need enough space to retain table columns;
        # ratios/growth usually fit in much shorter rows.
        weight += 6 if index == 0 else (4 if index == 1 else 3)
    lowered = text.lower().replace("-", " ")
    if any(keyword in lowered for keyword in _PDF_GOVERNANCE_KEYWORDS):
        weight += 2
    return weight


def _page_evidence_excerpt(text: str, max_chars: int) -> str:
    """Keep balanced windows from one selected PDF page.

    PDF tables are commonly extracted as a single very long line. Taking only
    the start of that line can omit the numeric row beside a later label, so we
    retain short windows around each distinct financial/governance concept.
    """

    normalized = _clean_text(text)
    if len(normalized) <= max_chars:
        return normalized

    lowered = normalized.lower().replace("-", " ")
    groups = (*_PDF_EVIDENCE_KEYWORD_GROUPS, _PDF_GOVERNANCE_KEYWORDS)
    positions: list[int] = []
    for aliases in groups:
        position = _best_keyword_position(lowered, aliases)
        if position is not None:
            positions.append(position)
    positions = list(dict.fromkeys(positions))
    if not positions:
        return normalized[:max_chars]

    separator = " … "
    usable = max_chars - len(separator) * (len(positions) - 1)
    per_window = max(40, usable // len(positions))
    windows: list[str] = []
    for position in positions:
        start = max(0, position - per_window // 3)
        end = min(len(normalized), start + per_window)
        start = max(0, end - per_window)
        windows.append(normalized[start:end].strip())
    return separator.join(windows)[:max_chars]


def _best_keyword_position(text: str, aliases: tuple[str, ...]) -> int | None:
    """Prefer a keyword occurrence beside numbers over a contents-page label."""

    candidates: list[tuple[int, int, int]] = []
    for alias in aliases:
        for match in re.finditer(re.escape(alias), text):
            nearby = text[max(0, match.start() - 80) : match.end() + 180]
            number_count = len(re.findall(r"(?<![a-z])[-(₹]?[0-9][0-9,.]*(?:\.[0-9]+)?%?", nearby))
            candidates.append((number_count > 0, min(number_count, 9), match.start()))
    if not candidates:
        return None
    # Later occurrences win the final tie, which usually favors a statement
    # table over an earlier table-of-contents entry.
    return max(candidates)[2]


def _discover_ticker_files(
    directory: Path, tickers: list[str], *, suffixes: set[str]
) -> tuple[list[tuple[str, Path]], str | None]:
    try:
        root = directory.expanduser().resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        return [], f"directory is unavailable: {_safe_error(exc)}"
    if not root.is_dir():
        return [], f"{root} is not a directory"

    matches: list[tuple[str, Path]] = []
    seen: set[Path] = set()
    try:
        candidates = sorted(path for path in root.rglob("*") if path.is_file())
    except OSError as exc:
        return [], f"could not list {root}: {_safe_error(exc)}"
    for path in candidates:
        if path.suffix.lower() not in suffixes:
            continue
        try:
            resolved = path.resolve(strict=True)
            resolved.relative_to(root)
        except (OSError, RuntimeError, ValueError):
            continue
        if resolved in seen:
            continue
        for ticker in tickers:
            in_ticker_directory = any(
                part.upper() == ticker for part in path.relative_to(root).parts[:-1]
            )
            ticker_filename = bool(
                re.match(rf"^{re.escape(ticker)}(?:$|[._ -])", path.stem, flags=re.IGNORECASE)
            )
            if in_ticker_directory or ticker_filename:
                matches.append((ticker, resolved))
                seen.add(resolved)
                break
    return matches, None


def _discover_company_documents(
    directory: Path, tickers: list[str]
) -> tuple[list[tuple[str, Path]], str | None]:
    try:
        root = directory.expanduser().resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        return [], f"directory is unavailable: {_safe_error(exc)}"
    if not root.is_dir():
        return [], f"{root} is not a directory"

    matches: list[tuple[str, Path]] = []
    for ticker in tickers:
        ticker_dir = root / ticker
        if not ticker_dir.is_dir():
            continue
        safe_root = ticker_dir.resolve()
        try:
            candidates = sorted(path for path in ticker_dir.rglob("*") if path.is_file())
        except OSError as exc:
            return matches, f"could not list {ticker_dir}: {_safe_error(exc)}"
        for path in candidates:
            if path.suffix.lower() not in {".pdf", ".txt", ".csv"}:
                continue
            try:
                resolved = path.resolve(strict=True)
                resolved.relative_to(safe_root)
            except (OSError, RuntimeError, ValueError):
                continue
            matches.append((ticker, resolved))
    return matches, None


def _validate_local_file(path: Path) -> None:
    size = path.stat().st_size
    if size > _LOCAL_FILE_BYTE_LIMIT:
        raise ValueError(f"file exceeds the {_LOCAL_FILE_BYTE_LIMIT}-byte limit")


@traced("upload.table")
def _read_table(path: Path) -> pd.DataFrame:
    _validate_local_file(path)
    suffix = path.suffix.lower()
    if suffix == ".csv":
        frame = pd.read_csv(path, nrows=_MAX_TABLE_ROWS)
    elif suffix == ".xlsx":
        try:
            frame = pd.read_excel(path, sheet_name=0, nrows=_MAX_TABLE_ROWS)
        except ImportError:
            frame = _read_basic_xlsx(path)
    else:
        raise ValueError(f"unsupported table type {suffix!r}")
    if frame.shape[1] > _MAX_TABLE_COLUMNS:
        frame = frame.iloc[:, :_MAX_TABLE_COLUMNS]
    if frame.empty and len(frame.columns) == 0:
        raise ValueError("table contained no rows or columns")
    return frame


def _read_basic_xlsx(path: Path) -> pd.DataFrame:
    """Read the first XLSX worksheet when an optional Excel engine is absent.

    This intentionally supports values, shared strings, inline strings and
    cached formula values only.  It neither evaluates formulas nor handles
    macros.  Resource limits are checked before any XML member is read.
    """

    with zipfile.ZipFile(path) as archive:
        members = archive.infolist()
        if sum(member.file_size for member in members) > _MAX_XLSX_UNCOMPRESSED_BYTES:
            raise ValueError("XLSX uncompressed content exceeds the safety limit")
        names = {member.filename for member in members}
        required = {"xl/workbook.xml", "xl/_rels/workbook.xml.rels"}
        if not required.issubset(names):
            raise ValueError("XLSX workbook metadata is missing")

        workbook = ElementTree.fromstring(archive.read("xl/workbook.xml"))
        relationships = ElementTree.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
        relationship_map = {
            item.attrib.get("Id", ""): item.attrib.get("Target", "") for item in relationships
        }
        first_sheet = workbook.find("{*}sheets/{*}sheet")
        if first_sheet is None:
            raise ValueError("XLSX has no worksheets")
        relation_id = first_sheet.attrib.get(
            "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id", ""
        )
        target = relationship_map.get(relation_id, "")
        sheet_name = _xlsx_member_name(target)
        if sheet_name not in names:
            raise ValueError("XLSX first worksheet is missing")

        shared_strings: list[str] = []
        if "xl/sharedStrings.xml" in names:
            shared_root = ElementTree.fromstring(archive.read("xl/sharedStrings.xml"))
            for item in shared_root.findall("{*}si"):
                shared_strings.append("".join(node.text or "" for node in item.findall(".//{*}t")))

        sheet = ElementTree.fromstring(archive.read(sheet_name))
        rows: list[list[Any]] = []
        for row in sheet.findall(".//{*}sheetData/{*}row")[: _MAX_TABLE_ROWS + 1]:
            values: dict[int, Any] = {}
            for cell in row.findall("{*}c"):
                column = _xlsx_column_index(cell.attrib.get("r", ""))
                if column is None or column >= _MAX_TABLE_COLUMNS:
                    continue
                values[column] = _xlsx_cell_value(cell, shared_strings)
            width = min(max(values, default=-1) + 1, _MAX_TABLE_COLUMNS)
            rows.append([values.get(index, "") for index in range(width)])

    if not rows:
        raise ValueError("XLSX first worksheet was empty")
    width = max(len(row) for row in rows)
    padded = [row + [""] * (width - len(row)) for row in rows]
    headers = _unique_headers(padded[0])
    return pd.DataFrame(padded[1:], columns=headers)


def _xlsx_member_name(target: str) -> str:
    normalized = target.replace("\\", "/").lstrip("/")
    if normalized.startswith("xl/"):
        return normalized
    if normalized.startswith("../") or "/../" in normalized:
        raise ValueError("XLSX worksheet path is unsafe")
    return f"xl/{normalized}"


def _xlsx_column_index(reference: str) -> int | None:
    match = re.match(r"([A-Za-z]+)", reference)
    if match is None:
        return None
    value = 0
    for letter in match.group(1).upper():
        value = value * 26 + ord(letter) - ord("A") + 1
    return value - 1


def _xlsx_cell_value(cell: ElementTree.Element, shared_strings: list[str]) -> Any:
    cell_type = cell.attrib.get("t", "")
    if cell_type == "inlineStr":
        return "".join(node.text or "" for node in cell.findall(".//{*}t"))
    value_node = cell.find("{*}v")
    raw = value_node.text if value_node is not None and value_node.text is not None else ""
    if cell_type == "s":
        try:
            return shared_strings[int(raw)]
        except (IndexError, ValueError):
            return raw
    if cell_type == "b":
        return raw == "1"
    return raw


def _unique_headers(raw_headers: list[Any]) -> list[str]:
    headers: list[str] = []
    counts: dict[str, int] = {}
    for index, raw in enumerate(raw_headers, start=1):
        base = _clean_text(str(raw)) or f"column_{index}"
        counts[base] = counts.get(base, 0) + 1
        headers.append(base if counts[base] == 1 else f"{base}_{counts[base]}")
    return headers


def _frame_excerpt(frame: pd.DataFrame, max_chars: int) -> str:
    safe = frame.iloc[:_MAX_TABLE_ROWS, :_MAX_TABLE_COLUMNS].fillna("")
    text = safe.to_csv(index=False, lineterminator="\n")
    return _truncate(text, max_chars)


def _manual_reference(
    profile: CompanyProfile,
    *,
    source_type: Literal["manual_reference_et", "manual_reference_moneycontrol"],
    title: str,
    url: str,
    publisher: str,
    tags: tuple[str, ...],
) -> FreeSourceRecord:
    return FreeSourceRecord(
        ticker=profile.ticker,
        source_type=source_type,
        title=title,
        url=url,
        excerpt="",
        publisher=publisher,
        metadata=_metadata("secondary", tags, retrieval_status="not_fetched"),
    )


def _metadata(authority: Authority, tags: tuple[str, ...], **values: Any) -> Mapping[str, Any]:
    # A regular dict keeps records directly compatible with dataclasses.asdict,
    # Pydantic and JSON-normalization helpers used by the rest of the app.
    return {"authority": authority, "tags": tags, "published_at": None, **values}


def _clean_text(value: str) -> str:
    return " ".join(value.split())


def _truncate(value: str, max_chars: int) -> str:
    if len(value) <= max_chars:
        return value
    return value[: max_chars - 1].rstrip() + "…"


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _safe_error(exc: BaseException) -> str:
    message = _clean_text(str(exc))
    if len(message) > 240:
        message = message[:239] + "…"
    return f"{type(exc).__name__}: {message}" if message else type(exc).__name__


__all__ = [
    "ECONOMIC_TIMES_JEWELLERY_URL",
    "MONEYCONTROL_JEWELLERY_URL",
    "TRUSTED_COMPANY_PROFILES",
    "CompanyProfile",
    "FreeSourceCollection",
    "FreeSourceCollector",
    "FreeSourceRecord",
    "collect_free_sources",
]
