"""Local run diagnostics, separate from the evidence/publication audit.

Only explicitly selected operational metadata is recorded. Never pass prompts,
source excerpts, model responses, filenames, credentials, or exception messages.
Context variables follow LangGraph's copied worker contexts; the writer itself
is locked so concurrent company branches share one ordered JSONL event stream.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import re
import threading
from collections import Counter
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
from functools import wraps
from pathlib import Path
from time import perf_counter
from uuid import uuid4

from pydantic import ValidationError

_current: ContextVar[RunTrace | None] = ContextVar("research_trace", default=None)
_parent: ContextVar[str | None] = ContextVar("research_span", default=None)
_ticker: ContextVar[str | None] = ContextVar("research_ticker", default=None)
_FIELDS = {
    "ticker",
    "provider",
    "model",
    "schema",
    "attempt",
    "max_attempts",
    "max_tokens",
    "timeout_seconds",
    "concurrency",
    "category",
    "host",
    "resource_id",
    "status_code",
    "exit_code",
    "bytes",
    "pages",
    "chunks",
    "result_count",
    "metric_count",
    "warning_count",
    "finding_count",
    "issue_count",
    "code",
    "error_type",
    "error_kind",
    "error_line",
    "error_column",
    "validation_types",
    "validation_fields",
    "request_id",
    "finish_reason",
    "input_tokens",
    "output_tokens",
    "reasoning_tokens",
    "prompt_chars",
    "retry_delay_seconds",
    "report_id",
    "passed",
    "outcome",
    "as_of",
    "api_style",
    "logger",
    "level",
}
_VALIDATION_FIELDS = {
    "ticker",
    "company",
    "business",
    "management",
    "fundamentals",
    "valuation",
    "observations",
    "code",
    "value",
    "unit",
    "as_of_date",
    "period_type",
    "accounting_basis",
    "source_id",
    "source_ids",
    "status",
    "score",
    "summary",
    "strengths",
    "risks",
    "text",
    "confidence",
    "agent",
    "metrics",
    "growth_score",
    "label",
    "period",
    "measurement_type",
    "definition",
    "formula",
    "input_metric_codes",
    "flags",
}


def log_root() -> Path:
    root = Path(os.getenv("COMPETITIVE_SCORING_RUN_LOG_DIR", "outputs/run_logs")).expanduser()
    if not root.is_absolute():
        root = Path(__file__).resolve().parents[2] / root
    return root.resolve()


def resource_id(value: str) -> str:
    """Correlate repeated URLs/files without retaining their private contents."""
    return hashlib.sha256(value.encode()).hexdigest()[:16]


def _metadata(values: dict) -> dict:
    clean = {}
    for key, value in values.items():
        if key not in _FIELDS or value is None:
            continue
        if isinstance(value, (bool, int)):
            clean[key] = value
        elif isinstance(value, float) and math.isfinite(value):
            clean[key] = round(value, 6)
        elif isinstance(value, str):
            clean[key] = value[:200]
    return clean


def error_metadata(exc: BaseException) -> dict:
    """Retain failure classification, never str(exc), repr(exc), or raw inputs."""
    name = type(exc).__name__
    kind = "exception"
    details: dict = {"error_type": name}
    if isinstance(exc, SystemExit):
        details["error_kind"] = "process_exit"
        if isinstance(exc.code, int):
            details["exit_code"] = exc.code
        return details
    if "Timeout" in name:
        kind = "timeout"
    elif isinstance(exc, ValidationError):
        kind = "schema_validation"
        issues = exc.errors(include_input=False, include_url=False)
        details["validation_types"] = ",".join(sorted({item["type"] for item in issues}))
        details["validation_fields"] = ";".join(
            ".".join(
                str(part) if isinstance(part, int) or part in _VALIDATION_FIELDS else "<field>"
                for part in item["loc"]
            )
            for item in issues[:6]
        )
    elif isinstance(exc, json.JSONDecodeError):
        kind = "json_syntax"
        details.update(error_line=exc.lineno, error_column=exc.colno)
    status = (
        getattr(exc, "status_code", None)
        or getattr(exc, "code", None)
        or getattr(getattr(exc, "response", None), "status_code", None)
    )
    if isinstance(status, int) and 100 <= status <= 599:
        details["status_code"] = status
        kind = "rate_limit" if status == 429 else "http_error"
    if name == "LengthFinishReasonError":
        kind = "token_limit"
    details["error_kind"] = kind
    return details


class RunTrace:
    def __init__(self, settings, *, run_id: str | None = None):
        self.run_id = run_id or uuid4().hex
        if not re.fullmatch(r"[a-f0-9]{32}", self.run_id):
            raise ValueError("Run id must be a 32-character hexadecimal identifier.")
        self.directory = log_root() / settings.mode / self.run_id
        self.started_at = datetime.now(UTC).isoformat()
        self.start = perf_counter()
        self.status = "running"
        self.settings = _metadata(
            {
                "provider": settings.llm_provider if settings.mode == "live" else "offline",
                "model": settings.llm_model if settings.mode == "live" else "deterministic-demo",
                "as_of": settings.research_as_of.isoformat(),
                "api_style": settings.llm_api_style,
                "max_attempts": settings.llm_max_attempts,
                "timeout_seconds": settings.llm_request_timeout_seconds,
                "concurrency": settings.llm_max_concurrent_requests,
            }
        )
        self.mode = settings.mode
        self.lock = threading.RLock()
        self.events: list[dict] = []
        self.io_error: str | None = None
        self._last_snapshot = self.start

    def _io_failed(self, exc: OSError) -> None:
        if self.io_error is None:
            self.io_error = type(exc).__name__
            logging.getLogger(__name__).warning(
                "Run diagnostics could not be written (%s); research continues.", self.io_error
            )

    def emit(self, event: str, **values) -> dict:
        with self.lock:
            record = {
                "sequence": len(self.events) + 1,
                "run_id": self.run_id,
                "timestamp": datetime.now(UTC).isoformat(),
                "elapsed_seconds": round(perf_counter() - self.start, 6),
                "event": event,
                **values,
            }
            self.events.append(record)
            try:
                self.directory.mkdir(parents=True, exist_ok=True)
                with (self.directory / "events.jsonl").open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(record, allow_nan=False) + "\n")
                    handle.flush()
            except OSError as exc:
                self._io_failed(exc)
            if (
                event == "span_started"
                or perf_counter() - self._last_snapshot >= 2
                or values.get("operation")
                in {
                    "llm.provider",
                    "llm.queue_wait",
                    "search.http",
                    "sources.http",
                    "pdf.scan_pages",
                }
            ):
                self._last_snapshot = perf_counter()
                self.save_summary()
            return record

    def summary(self) -> dict:
        with self.lock:
            finished = [e for e in self.events if e["event"] == "span_finished"]
            completed_ids = {e["span_id"] for e in finished}
            active = [
                e
                for e in self.events
                if e["event"] == "span_started" and e["span_id"] not in completed_ids
            ]
            groups: dict[str, dict] = {}
            for e in finished:
                row = groups.setdefault(
                    e["operation"],
                    {
                        "operation": e["operation"],
                        "count": 0,
                        "total_seconds": 0.0,
                        "max_seconds": 0.0,
                        "failed": 0,
                    },
                )
                row["count"] += 1
                row["total_seconds"] = round(row["total_seconds"] + e["duration_seconds"], 6)
                row["max_seconds"] = max(row["max_seconds"], e["duration_seconds"])
                row["failed"] += e["status"] == "failed"
            failures = [e for e in finished if e["status"] == "failed"]
            diagnostics = [e for e in self.events if e["event"] == "diagnostic"]
            completions = [e["metadata"] for e in self.events if e["event"] == "llm.completion"]
            return {
                "schema_version": 1,
                "run_id": self.run_id,
                "mode": self.mode,
                "started_at": self.started_at,
                "status": self.status,
                "wall_seconds": round(perf_counter() - self.start, 6),
                "settings": self.settings,
                "event_count": len(self.events),
                "io_error": self.io_error,
                "timing_note": "Durations include children and overlap across parallel work; "
                "do not sum them to estimate run wall time.",
                "operations": sorted(groups.values(), key=lambda r: -r["total_seconds"]),
                "slowest_steps": sorted(finished, key=lambda e: -e["duration_seconds"])[:20],
                "failed_attempts": failures,
                "llm_usage": {
                    key: sum(e[key] for e in completions if isinstance(e.get(key), int))
                    if any(isinstance(e.get(key), int) for e in completions)
                    else None
                    for key in ("input_tokens", "output_tokens", "reasoning_tokens")
                },
                "retry_count": sum(
                    e["event"] in {"llm.retry", "search.retry"} for e in self.events
                ),
                "audit_findings": [
                    e["metadata"] for e in self.events if e["event"] == "audit.finding"
                ],
                "diagnostic_counts": dict(
                    Counter(e["metadata"].get("code", "unknown") for e in diagnostics)
                ),
                "active_steps": active,
                "errors": [
                    e
                    for e in self.events
                    if e["event"] in {"run_error", "run.result"}
                    and (e.get("metadata", {}).get("error_type") is not None)
                ],
            }

    def save_summary(self) -> None:
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            temporary = self.directory / "summary.json.tmp"
            temporary.write_text(json.dumps(self.summary(), indent=2), encoding="utf-8")
            temporary.replace(self.directory / "summary.json")
        except OSError as exc:
            self._io_failed(exc)


@contextmanager
def trace_run(settings, *, run_id: str | None = None):
    """Reuse a caller's run so report persistence is part of the same trace."""
    existing = _current.get()
    if existing is not None:
        yield existing
        return
    trace = RunTrace(settings, run_id=run_id)
    token = _current.set(trace)
    parent_token = _parent.set(None)
    ticker_token = _ticker.set(None)
    trace.emit("run_started", metadata=trace.settings)
    trace.save_summary()
    handler = _LibraryWarnings(trace)
    logging.getLogger("pypdf").addHandler(handler)
    try:
        yield trace
        if trace.status == "running":
            trace.status = "completed"
    except BaseException as exc:
        if trace.status != "unvalidated" or not isinstance(exc, SystemExit):
            trace.status = "interrupted" if isinstance(exc, KeyboardInterrupt) else "failed"
        trace.emit("run_error", metadata=error_metadata(exc))
        raise
    finally:
        trace.emit("run_finished", status=trace.status, metadata={})
        trace.save_summary()
        logging.getLogger("pypdf").removeHandler(handler)
        _ticker.reset(ticker_token)
        _parent.reset(parent_token)
        _current.reset(token)


@contextmanager
def span(operation: str, **metadata):
    trace = _current.get()
    fields = _metadata({"ticker": _ticker.get(), **metadata})
    if trace is None:
        yield fields
        return
    span_id = uuid4().hex
    parent_id = _parent.get()
    token = _parent.set(span_id)
    ticker_token = _ticker.set(fields.get("ticker"))
    started = perf_counter()
    trace.emit(
        "span_started",
        span_id=span_id,
        parent_id=parent_id,
        operation=operation,
        metadata=fields.copy(),
    )
    status = "completed"
    try:
        yield fields
    except BaseException as exc:
        status = "failed"
        fields.update(error_metadata(exc))
        raise
    finally:
        trace.emit(
            "span_finished",
            span_id=span_id,
            parent_id=parent_id,
            operation=operation,
            status=status,
            duration_seconds=round(perf_counter() - started, 6),
            metadata=_metadata(fields),
        )
        _ticker.reset(ticker_token)
        _parent.reset(token)


def event(name: str, **metadata) -> None:
    trace = _current.get()
    if trace is not None:
        trace.emit(
            name, parent_id=_parent.get(), metadata=_metadata({"ticker": _ticker.get(), **metadata})
        )


def traced(operation: str):
    """Time a function without serializing its arguments or return value."""

    def decorate(function):
        @wraps(function)
        def wrapped(*args, **kwargs):
            with span(operation):
                return function(*args, **kwargs)

        return wrapped

    return decorate


def traced_node(operation: str, function):
    @wraps(function)
    def wrapped(state):
        company = state.get("company")
        if company is None and state.get("report") is not None:
            company = state["report"].company
        with span(operation, ticker=getattr(company, "ticker", None)):
            return function(state)

    return wrapped


def run_directory(mode: str, run_id: str) -> Path:
    if mode not in {"demo", "live"} or not re.fullmatch(r"[a-f0-9]{32}", run_id):
        raise ValueError("Invalid run log identifier.")
    return log_root() / mode / run_id


class _LibraryWarnings(logging.Handler):
    """Count PDF warnings in the originating run, without logging their text."""

    def __init__(self, trace: RunTrace):
        super().__init__(level=logging.WARNING)
        self.trace = trace

    def emit(self, record: logging.LogRecord) -> None:
        if _current.get() is self.trace:
            event(
                "diagnostic", code="PDF_LIBRARY_WARNING", logger=record.name, level=record.levelname
            )


def recent_summaries(mode: str, *, limit: int = 10) -> list[dict]:
    """Read recent diagnostic metadata independently of published reports."""
    if mode not in {"demo", "live"}:
        return []
    try:
        paths = list((log_root() / mode).glob("*/summary.json"))
        paths.sort(key=lambda path: path.stat().st_mtime, reverse=True)
    except OSError:
        return []
    summaries = []
    for path in paths:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if (
                data.get("schema_version") == 1
                and data.get("mode") == mode
                and data.get("run_id") == path.parent.name
                and re.fullmatch(r"[a-f0-9]{32}", path.parent.name)
            ):
                summaries.append(data)
        except (OSError, ValueError, AttributeError):
            continue
        if len(summaries) >= limit:
            break
    return summaries
