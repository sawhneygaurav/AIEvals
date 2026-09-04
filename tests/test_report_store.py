"""Focused tests for durable, report-first application state."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import pytest
from pydantic import ValidationError

import competitive_scoring.report_store as report_store_module
from competitive_scoring.models import AuditResult, FinalBriefing, TraceEvent
from competitive_scoring.report_store import ReportStore, ReportStoreError, StoredReport

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _briefing() -> FinalBriefing:
    # The checked-in demo briefing exercises the entire nested schema (sources,
    # metrics, book citations, audit, ranking, and rendered Markdown) without
    # invoking the graph or any network-backed service.
    return FinalBriefing.model_validate_json(
        (PROJECT_ROOT / "examples" / "sample_briefing.json").read_text(encoding="utf-8")
    )


def _trace(detail: str = "All required agents completed.") -> list[TraceEvent]:
    return [
        TraceEvent(
            stage="Stage 4",
            agent="Audit Agent",
            status="completed",
            detail=detail,
            timestamp=datetime(2026, 9, 4, 9, 0, tzinfo=UTC),
        )
    ]


def test_save_round_trip_preserves_complete_briefing_trace_and_metadata(tmp_path: Path) -> None:
    now = datetime(2026, 9, 4, 14, 22, 3, 123456, tzinfo=UTC)
    report_id = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
    store = ReportStore(tmp_path, clock=lambda: now, id_factory=lambda: report_id)
    briefing = _briefing()
    trace = _trace()

    saved = store.save(
        briefing,
        trace,
        run_metadata={"provider": "Nebius", "model": "example-model", "attempt": 1},
    )
    loaded = store.load_latest()

    assert loaded == saved
    assert loaded is not None
    assert loaded.briefing.model_dump(mode="json") == briefing.model_dump(mode="json")
    assert [item.model_dump(mode="json") for item in loaded.trace] == [
        item.model_dump(mode="json") for item in trace
    ]
    assert loaded.run_metadata == {
        "provider": "Nebius",
        "model": "example-model",
        "attempt": 1,
    }
    history = store.list_history()
    assert history == [
        tmp_path
        / "history"
        / "2026-09-04T14-22-03.123456Z_aaaaaaaaaaaa4aaa8aaaaaaaaaaaaaaa.json"
    ]
    assert json.loads(store.latest_path.read_text(encoding="utf-8")) == json.loads(
        history[0].read_text(encoding="utf-8")
    )
    assert store.load_latest_attempt() == loaded


def test_mode_factory_isolates_demo_and_live_state(tmp_path: Path) -> None:
    demo = ReportStore.for_mode("demo", base_dir=tmp_path)
    live = ReportStore.for_mode("live", base_dir=tmp_path)

    demo.save(_briefing(), _trace())

    assert demo.root == tmp_path / "demo"
    assert live.root == tmp_path / "live"
    assert demo.load_latest() is not None
    assert live.load_latest() is None

    with pytest.raises(ValueError, match="Cannot save a 'demo' briefing"):
        live.save(_briefing(), _trace())

    with pytest.raises(ValueError, match="demo.*live"):
        ReportStore.for_mode("../escape", base_dir=tmp_path)  # type: ignore[arg-type]


def test_second_save_promotes_latest_without_overwriting_history(tmp_path: Path) -> None:
    times = iter(
        (
            datetime(2026, 9, 4, 10, 0, tzinfo=UTC),
            datetime(2026, 9, 4, 11, 0, tzinfo=UTC),
        )
    )
    ids = iter(
        (
            UUID("11111111-1111-4111-8111-111111111111"),
            UUID("22222222-2222-4222-8222-222222222222"),
        )
    )
    store = ReportStore(tmp_path, clock=lambda: next(times), id_factory=lambda: next(ids))

    first = store.save(_briefing(), _trace("first"))
    second = store.save(_briefing(), _trace("second"))

    assert store.load_latest() == second
    history = store.list_history()
    assert len(history) == 2
    assert store.load_history(history[0]) == second
    assert store.load_history(history[1]) == first


def test_failed_audit_is_rejected_before_existing_latest_is_touched(tmp_path: Path) -> None:
    store = ReportStore(tmp_path)
    original = store.save(_briefing(), _trace("valid"))
    latest_before = store.latest_path.read_bytes()
    failed = _briefing().model_copy(update={"audit": AuditResult(passed=False)})

    with pytest.raises(ValueError, match="passed evidence audit"):
        store.save(failed, _trace("invalid"))

    assert store.latest_path.read_bytes() == latest_before
    assert store.load_latest() == original
    assert len(store.list_history()) == 1


def test_failed_result_updates_attempt_without_promoting_validated_latest(tmp_path: Path) -> None:
    store = ReportStore(tmp_path)
    validated = store.save(_briefing(), _trace("validated"))
    failed = _briefing().model_copy(update={"audit": AuditResult(passed=False)})

    attempt = store.save_result(failed, _trace("failed audit"))

    assert attempt.briefing.audit.passed is False
    assert store.load_latest_attempt() == attempt
    assert store.load_latest() == validated
    assert len(store.list_history()) == 1


def test_self_claimed_pass_with_research_warning_is_attempt_only(tmp_path: Path) -> None:
    """A stale ``audit.passed=True`` cannot bypass the final publication scan."""

    store = ReportStore(tmp_path)
    validated = store.save(_briefing(), _trace("validated"))
    first = _briefing().companies[0]
    warned_research = first.research.model_copy(update={"warnings": ["needs review"]})
    warned_company = first.model_copy(update={"research": warned_research})
    warned = _briefing().model_copy(
        update={"companies": [warned_company, *_briefing().companies[1:]]}
    )

    attempt = store.save_result(warned, _trace("rejected"))

    assert attempt.briefing.audit.passed is True
    assert store.load_latest_attempt() == attempt
    assert store.load_latest() == validated
    assert len(store.list_history()) == 1
    with pytest.raises(ValueError, match="RESEARCH_WARNINGS_PRESENT"):
        store.save(warned, _trace("must raise"))


def test_load_latest_rechecks_derived_ranking_and_markdown(tmp_path: Path) -> None:
    """Even a valid JSON file is rejected when its final score has drifted."""

    store = ReportStore(tmp_path)
    stored = store.save(_briefing(), _trace())
    first = stored.briefing.companies[0].model_copy(update={"final_score": 1.0})
    tampered_briefing = stored.briefing.model_copy(
        update={"companies": [first, *stored.briefing.companies[1:]]}
    )
    tampered = stored.model_copy(update={"briefing": tampered_briefing})
    store.latest_path.write_text(tampered.model_dump_json(indent=2), encoding="utf-8")

    with pytest.raises(ReportStoreError, match="did not pass its evidence audit"):
        store.load_latest()


def test_fixed_identity_and_book_date_are_publication_requirements(tmp_path: Path) -> None:
    store = ReportStore(tmp_path)
    briefing = _briefing()
    first = briefing.companies[0]
    wrong_identity = first.research.company.model_copy(update={"exchange": "BSE"})
    wrong_research = first.research.model_copy(update={"company": wrong_identity})
    wrong_book = first.book_score.model_copy(
        update={"as_of_date": briefing.as_of_date + timedelta(days=1)}
    )
    wrong_company = first.model_copy(update={"research": wrong_research, "book_score": wrong_book})
    invalid = briefing.model_copy(update={"companies": [wrong_company, *briefing.companies[1:]]})

    attempt = store.save_result(invalid, _trace())

    assert store.load_latest() is None
    assert store.load_latest_attempt() == attempt
    with pytest.raises(ValueError, match="COMPANY_IDENTITY_MISMATCH"):
        store.save(invalid, _trace())


def test_atomic_promotion_failure_keeps_previous_latest_and_cleans_temp_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = ReportStore(tmp_path)
    original = store.save(_briefing(), _trace("original"))
    real_replace = report_store_module.os.replace

    def fail_latest(source: str | Path, destination: str | Path) -> None:
        if Path(destination) == store.latest_path:
            raise OSError("simulated promotion failure")
        real_replace(source, destination)

    monkeypatch.setattr(report_store_module.os, "replace", fail_latest)

    with pytest.raises(OSError, match="simulated promotion failure"):
        store.save(_briefing(), _trace("replacement"))

    assert store.load_latest() == original
    assert not list(store.root.rglob("*.tmp"))
    # History is written first by design, so the complete unpromoted run remains
    # recoverable even though latest.json still identifies the previous report.
    assert len(store.list_history()) == 2


def test_missing_latest_is_normal_but_corrupt_latest_is_reported(tmp_path: Path) -> None:
    store = ReportStore(tmp_path)
    assert store.load_latest() is None

    store.root.mkdir(parents=True, exist_ok=True)
    store.latest_path.write_text('{"schema_version": 1, "briefing":', encoding="utf-8")

    with pytest.raises(ReportStoreError, match="Could not load a valid stored report"):
        store.load_latest()


def test_legacy_bootstrap_uses_newest_valid_matching_mode_and_is_idempotent(
    tmp_path: Path,
) -> None:
    outputs = tmp_path / "outputs"
    outputs.mkdir()
    (outputs / "newest-invalid.json").write_text("not JSON", encoding="utf-8")
    legacy = outputs / "live-report.json"
    live_failed = _briefing().model_copy(
        update={"mode": "live", "audit": AuditResult(passed=False)}
    )
    legacy.write_text(live_failed.model_dump_json(indent=2), encoding="utf-8")
    old_demo = outputs / "older-demo.json"
    old_demo.write_text(_briefing().model_dump_json(indent=2), encoding="utf-8")
    # Explicit mtimes make selection deterministic on filesystems with coarse
    # timestamp resolution.
    import os

    os.utime(old_demo, (1_700_000_000, 1_700_000_000))
    os.utime(legacy, (1_700_000_010, 1_700_000_010))
    os.utime(outputs / "newest-invalid.json", (1_700_000_020, 1_700_000_020))
    store = ReportStore.for_mode("live", base_dir=outputs / "report_store")

    imported = store.bootstrap_from_legacy(outputs)

    assert imported is not None
    assert imported.briefing.mode == "live"
    assert imported.briefing.audit.passed is False
    assert imported.run_metadata["legacy_import"] is True
    assert imported.trace[0].status == "warning"
    assert "original agent execution trace was not available" in imported.trace[0].detail
    assert store.load_latest() is None
    assert store.load_latest_attempt() == imported
    assert store.bootstrap_from_legacy(outputs) is None


def test_snapshot_rejects_naive_timestamp_and_empty_trace() -> None:
    briefing = _briefing().model_dump(mode="json")
    base = {
        "schema_version": 1,
        "report_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        "briefing": briefing,
        "run_metadata": {},
    }

    with pytest.raises(ValidationError, match="timezone"):
        StoredReport.model_validate(
            {
                **base,
                "saved_at": datetime(2026, 9, 4, tzinfo=UTC).replace(tzinfo=None),
                "trace": _trace(),
            }
        )

    with pytest.raises(ValidationError, match="execution trace"):
        StoredReport.model_validate(
            {
                **base,
                "saved_at": datetime(2026, 9, 4, tzinfo=UTC) + timedelta(hours=0),
                "trace": [],
            }
        )
