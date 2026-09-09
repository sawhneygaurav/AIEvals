"""Exercise real tracing across failures, SDK retries, and LangGraph workers."""

from __future__ import annotations

import json
import logging
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from urllib.error import HTTPError

import httpx
import pytest

from competitive_scoring.agents import RuntimeServices
from competitive_scoring.book_policy import BookScoringAgent
from competitive_scoring.config import Settings
from competitive_scoring.graph import build_workflow, run_research
from competitive_scoring.models import TraceEvent
from competitive_scoring.publication import scan_for_publication
from competitive_scoring.report_store import ReportStore
from competitive_scoring.run_manager import RunManager
from competitive_scoring.tools.free_sources import FreeSourceCollector, _extract_pdf_text
from competitive_scoring.tools.you_search import YouSearchClient
from competitive_scoring.tracing import (
    event,
    log_root,
    recent_summaries,
    resource_id,
    run_directory,
    span,
    trace_run,
)
from tests.test_free_sources import _pdf_with_text
from tests.test_graph_demo import FakeBookKnowledgeBase
from tests.test_llm import ExampleAnswer, FakeResponse, make_llm
from tests.test_run_manager import _briefing
from tests.test_you_search import FakeResponse as FakeSearchResponse


def _read(trace):
    return [
        json.loads(line) for line in (trace.directory / "events.jsonl").read_text().splitlines()
    ]


def test_parallel_spans_share_run_and_keep_parent_and_ticker():
    settings = Settings(mode="demo")
    with trace_run(settings) as trace:

        def worker(ticker):
            with span("company", ticker=ticker):
                for _ in range(5):
                    with span("child"):
                        event("sample", result_count=1)

        with span("root"), ThreadPoolExecutor(max_workers=4) as pool:
            futures = [
                pool.submit(copy_context().run, worker, ticker)
                for ticker in ("PNGJL", "SENCO", "KALYANKJIL", "THANGAMAYL")
            ]
            for future in futures:
                future.result()
        with trace_run(settings) as nested:
            assert nested is trace
    events = _read(trace)
    assert [e["sequence"] for e in events] == list(range(1, len(events) + 1))
    assert {e["run_id"] for e in events} == {trace.run_id}
    starts = {e["span_id"]: e for e in events if e["event"] == "span_started"}
    children = [
        e for e in events if e.get("operation") == "child" and e["event"] == "span_finished"
    ]
    assert len(children) == 20
    for child in children:
        assert starts[child["parent_id"]]["metadata"]["ticker"] == child["metadata"]["ticker"]
    summary = json.loads((trace.directory / "summary.json").read_text())
    assert summary["status"] == "completed"
    assert not summary["active_steps"]
    assert len(recent_summaries("demo")) == 1


def test_real_langgraph_propagates_trace_context_to_company_and_book_workers():
    settings = Settings(mode="demo")
    runtime = RuntimeServices(
        settings=settings,
        book_agent=BookScoringAgent(
            FakeBookKnowledgeBase(),
            as_of_date=settings.research_as_of,
        ),
    )
    with trace_run(settings) as trace:
        result = build_workflow(runtime).invoke(
            {"company_reports": {}, "book_scores": {}, "trace": [], "retry_count": 0},
            config={"recursion_limit": 50, "max_concurrency": 4},
        )
        # Diagnostic spans must never become release-blocking audit TraceEvents.
        assert scan_for_publication(result["final"], result["trace"]).passed
    events = _read(trace)
    for operation in ("graph.company_worker", "graph.source_research_agent", "graph.book_worker"):
        assert {
            e["metadata"]["ticker"]
            for e in events
            if e.get("operation") == operation and e["event"] == "span_finished"
        } == {
            "PNGJL",
            "KALYANKJIL",
            "SENCO",
            "THANGAMAYL",
        }
    assert not trace.summary()["active_steps"]


def test_preflight_failure_is_saved_before_runtime_exists(tmp_path):
    with pytest.raises(FileNotFoundError):
        run_research(Settings(book_pdf_path=tmp_path / "missing-private-name.pdf"))
    summary = recent_summaries("demo")[0]
    assert summary["status"] == "failed"
    assert summary["failed_attempts"][0]["operation"] == "run.preflight"
    assert summary["errors"][0]["metadata"]["error_type"] == "FileNotFoundError"
    text = (run_directory("demo", summary["run_id"]) / "events.jsonl").read_text()
    assert "missing-private-name" not in text


def test_llm_repair_has_attempts_queue_provider_timings_and_no_private_text():
    secret = "PRIVATE_CONTENT_AND_API_KEY_12345"
    successful = FakeResponse()
    successful.usage = SimpleNamespace(prompt_tokens=50, completion_tokens=12)
    successful._request_id = "req_safe_123"
    llm, api = make_llm(
        FakeResponse(output_text='{"company":"' + secret + '","score":"bad"}'),
        successful,
    )
    with trace_run(Settings()) as trace:
        result = llm.generate(schema=ExampleAnswer, instructions=secret, prompt=secret)
        event("diagnostic", code="TEST_WARNING", message=secret, api_key=secret)
        logging.getLogger("pypdf").warning("Bad PDF containing %s", secret)
    assert result.score == 82
    assert len(api.calls) == 2
    events = _read(trace)
    attempts = [
        e for e in events if e.get("operation") == "llm.attempt" and e["event"] == "span_finished"
    ]
    assert [e["status"] for e in attempts] == ["failed", "completed"]
    assert [e["metadata"]["attempt"] for e in attempts] == [1, 2]
    assert attempts[0]["metadata"]["error_kind"] == "schema_validation"
    assert (
        sum(
            e.get("operation") == "llm.queue_wait" and e["event"] == "span_finished" for e in events
        )
        == 2
    )
    assert any(e.get("operation") == "llm.provider" for e in events)
    assert trace.summary()["retry_count"] == 1
    assert trace.summary()["llm_usage"]["input_tokens"] == 50
    assert trace.summary()["diagnostic_counts"]["PDF_LIBRARY_WARNING"] == 1
    for filename in ("events.jsonl", "summary.json"):
        text = (trace.directory / filename).read_text()
        assert secret not in text
        assert "api_key" not in text


def test_failure_flushes_log_and_restores_context():
    with pytest.raises(RuntimeError), trace_run(Settings()) as trace, span("failing"):
        raise RuntimeError("secret URL https://private.invalid/?token=abc")
    summary = json.loads((trace.directory / "summary.json").read_text())
    assert summary["status"] == "failed"
    assert not summary["active_steps"]
    assert "private.invalid" not in json.dumps(summary)
    before = len(_read(trace))
    event("outside_run", code="SHOULD_NOT_BE_SAVED")
    assert len(_read(trace)) == before


def test_two_simultaneous_runs_do_not_mix_events():
    def run(ticker):
        with trace_run(Settings()) as trace, span("test", ticker=ticker):
            event("marker", ticker=ticker)
        return trace, ticker

    with ThreadPoolExecutor(max_workers=2) as pool:
        runs = list(pool.map(run, ["PNGJL", "SENCO"]))
    assert runs[0][0].run_id != runs[1][0].run_id
    for trace, ticker in runs:
        assert {
            e["metadata"]["ticker"] for e in _read(trace) if "ticker" in e.get("metadata", {})
        } == {ticker}


@pytest.mark.parametrize("outcome", ["success", "failure", "unvalidated"])
def test_run_manager_links_report_to_logs_and_preserves_failure_history(tmp_path, outcome):
    def runner(_settings, *, progress_callback):
        if outcome == "failure":
            raise RuntimeError("private provider response")
        return _briefing(audit_passed=outcome == "success"), [
            TraceEvent(
                stage="Stage 4",
                agent="Test",
                status="completed",
                detail="complete",
                timestamp=datetime.now(UTC),
            )
        ]

    manager = RunManager(runner=runner)
    store = ReportStore(tmp_path / "reports")
    assert manager.start(Settings(), store)
    assert manager.wait(3)
    snapshot = manager.snapshot()
    summary = recent_summaries("demo")[0]
    assert summary["run_id"] == snapshot.run_id
    assert summary["status"] == snapshot.status
    assert (
        summary["status"]
        == {
            "success": "succeeded",
            "failure": "failed",
            "unvalidated": "unvalidated",
        }[outcome]
    )
    if outcome == "success":
        assert store.load_latest().run_metadata["run_id"] == snapshot.run_id
        assert any(row["operation"] == "report.save" for row in summary["operations"])
    if outcome == "failure":
        assert summary["errors"][0]["metadata"]["error_type"] == "RuntimeError"
    assert "private provider response" not in json.dumps(summary)


def test_log_io_error_does_not_fail_research(monkeypatch):
    def fail(*_args, **_kwargs):
        raise PermissionError("private filename")

    monkeypatch.setattr(Path, "mkdir", fail)
    with trace_run(Settings()) as trace, span("work"):
        result = 42
    assert result == 42
    assert trace.io_error == "PermissionError"
    assert trace.status == "completed"


def test_log_reader_ignores_corrupt_files_and_validates_identifiers():
    with trace_run(Settings()) as trace:
        pass
    (trace.directory / "summary.json").write_text("invalid json")
    assert recent_summaries("demo") == []
    with pytest.raises(ValueError):
        run_directory("demo", "../../.env")
    assert resource_id("private-file.pdf") != "private-file.pdf"
    assert log_root().is_absolute()


def test_search_rate_limit_retry_is_logged_without_query_or_key():
    calls = 0

    def opener(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise HTTPError("https://private.invalid/?secret=123", 429, "private body", {}, None)
        return FakeSearchResponse({"results": {"web": [], "news": []}})

    client = YouSearchClient(api_key="secret-key", opener=opener, sleeper=lambda _delay: None)
    with trace_run(Settings()) as trace, span("query", ticker="PNGJL"):
        client.search("private query")
    events = _read(trace)
    requests = [
        e for e in events if e.get("operation") == "search.http" and e["event"] == "span_finished"
    ]
    assert len(requests) == 2
    assert requests[0]["metadata"]["error_kind"] == "rate_limit"
    assert requests[1]["metadata"]["status_code"] == 200
    assert trace.summary()["retry_count"] == 1
    text = (trace.directory / "events.jsonl").read_text()
    assert "private" not in text
    assert "secret-key" not in text


def test_http_and_pdf_timings_capture_failed_download_and_extraction():
    collector = FreeSourceCollector(
        transport=httpx.MockTransport(lambda _request: httpx.Response(503, text="private body"))
    )
    with trace_run(Settings()) as trace, span("company", ticker="PNGJL"):
        with pytest.raises(httpx.HTTPStatusError):
            collector._safe_get(
                "https://example.invalid/private.pdf?token=secret",
                ("example.invalid",),
                byte_limit=5000,
            )
        _extract_pdf_text(_pdf_with_text("private report content"), max_pages=2, max_chars=1000)
    collector.close()
    events = _read(trace)
    request = next(
        e for e in events if e.get("operation") == "sources.http" and e["event"] == "span_finished"
    )
    assert request["metadata"]["status_code"] == 503
    assert request["metadata"]["error_kind"] == "http_error"
    assert request["metadata"]["host"] == "example.invalid"
    assert request["metadata"]["resource_id"]
    assert any(e.get("operation") == "pdf.scan_pages" for e in events)
    assert any(e["event"] == "pdf.input" and e["metadata"]["pages"] == 1 for e in events)
    assert "private" not in (trace.directory / "events.jsonl").read_text()


def test_summary_shows_current_step_before_a_slow_call_finishes():
    with trace_run(Settings()) as trace, span("book.hash"):
        summary = json.loads((trace.directory / "summary.json").read_text())
        assert summary["status"] == "running"
        assert summary["active_steps"][-1]["operation"] == "book.hash"


def test_process_exit_is_not_classified_as_an_http_error():
    from competitive_scoring.tracing import error_metadata

    result = error_metadata(SystemExit(2))
    assert result == {'error_type': 'SystemExit', 'error_kind': 'process_exit', 'exit_code': 2}
    assert error_metadata(HTTPError('https://example.com', 503, 'unavailable', {}, None))['error_kind'] == 'http_error'
