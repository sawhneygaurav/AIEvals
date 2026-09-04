"""Concurrency and safety tests for Streamlit's background run controller."""

from __future__ import annotations

import threading
from datetime import UTC, datetime
from pathlib import Path

from competitive_scoring.config import Settings
from competitive_scoring.models import AuditResult, FinalBriefing, TraceEvent
from competitive_scoring.progress import (
    FIXED_PROGRESS_WEIGHTS,
    TICKER_PROGRESS_WEIGHTS,
    ProgressEvent,
)
from competitive_scoring.report_store import ReportStore
from competitive_scoring.run_manager import RunManager

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_four_company_progress_weights_total_one_hundred_percent() -> None:
    total = sum(FIXED_PROGRESS_WEIGHTS.values()) + 4 * sum(
        TICKER_PROGRESS_WEIGHTS.values()
    )

    assert total == 100


def _briefing(*, audit_passed: bool = True) -> FinalBriefing:
    briefing = FinalBriefing.model_validate_json(
        (PROJECT_ROOT / "examples" / "sample_briefing.json").read_text(encoding="utf-8")
    )
    if audit_passed:
        return briefing
    return briefing.model_copy(update={"audit": AuditResult(passed=False)})


def test_start_is_single_flight_and_progress_is_monotonic(tmp_path: Path) -> None:
    entered = threading.Event()
    release = threading.Event()
    calls = 0

    def runner(_settings: Settings, *, progress_callback):
        nonlocal calls
        calls += 1
        progress_callback(ProgressEvent("setup", "Setup", "ready", "completed"))
        entered.set()
        assert release.wait(2)
        # A duplicate event must not add the weight twice.
        progress_callback(ProgressEvent("setup", "Setup", "still ready", "completed"))
        return _briefing(), [
            TraceEvent(
                stage="Stage 4",
                agent="Test Agent",
                status="completed",
                detail="done",
                timestamp=datetime.now(UTC),
            )
        ]

    manager = RunManager(runner=runner)
    store = ReportStore(tmp_path)
    settings = Settings(mode="demo")

    assert manager.start(settings, store) is True
    assert entered.wait(1)
    assert manager.start(settings, store) is False
    assert manager.snapshot().progress_percent == 3
    release.set()
    assert manager.wait(2)

    snapshot = manager.snapshot()
    assert calls == 1
    assert snapshot.status == "succeeded"
    assert snapshot.progress_percent == 100
    assert store.load_latest() is not None


def test_failed_refresh_keeps_previous_validated_report(tmp_path: Path) -> None:
    store = ReportStore(tmp_path)

    def successful_runner(_settings: Settings, *, progress_callback):
        del progress_callback
        return _briefing(), [
            TraceEvent(
                stage="Stage 4",
                agent="Test Agent",
                status="completed",
                detail="original",
                timestamp=datetime.now(UTC),
            )
        ]

    first = RunManager(runner=successful_runner)
    assert first.start(Settings(mode="demo"), store)
    assert first.wait(2)
    original_id = store.load_latest().report_id  # type: ignore[union-attr]

    def failed_runner(_settings: Settings, *, progress_callback):
        del progress_callback
        raise RuntimeError("simulated provider outage")

    second = RunManager(runner=failed_runner)
    assert second.start(Settings(mode="demo"), store)
    assert second.wait(2)

    assert second.snapshot().status == "failed"
    assert store.load_latest().report_id == original_id  # type: ignore[union-attr]


def test_unvalidated_attempt_is_saved_but_not_promoted(tmp_path: Path) -> None:
    store = ReportStore(tmp_path)

    def runner(_settings: Settings, *, progress_callback):
        del progress_callback
        return _briefing(audit_passed=False), [
            TraceEvent(
                stage="Stage 4",
                agent="Test Agent",
                status="warning",
                detail="audit failed",
                timestamp=datetime.now(UTC),
            )
        ]

    manager = RunManager(runner=runner)
    assert manager.start(Settings(mode="demo"), store)
    assert manager.wait(2)

    assert manager.snapshot().status == "unvalidated"
    assert store.load_latest() is None
    assert store.load_latest_attempt() is not None


def test_passed_audit_with_trace_warning_is_still_unvalidated(tmp_path: Path) -> None:
    """Run status follows the complete release scan, not a self-reported flag."""

    store = ReportStore(tmp_path)

    def runner(_settings: Settings, *, progress_callback):
        del progress_callback
        return _briefing(), [
            TraceEvent(
                stage="Stage 4",
                agent="Test Agent",
                status="warning",
                detail="a late warning",
                timestamp=datetime.now(UTC),
            )
        ]

    manager = RunManager(runner=runner)
    assert manager.start(Settings(mode="demo"), store)
    assert manager.wait(2)

    snapshot = manager.snapshot()
    assert snapshot.status == "unvalidated"
    assert snapshot.error_type == "EvidenceAudit"
    assert "TRACE_WARNINGS_PRESENT" in (snapshot.error_detail or "")
    assert store.load_latest() is None
    assert store.load_latest_attempt() is not None
