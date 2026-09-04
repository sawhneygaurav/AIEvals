"""Publication-boundary tests for the report-first Streamlit interface.

These tests execute the real page with Streamlit's ``AppTest`` harness. They do
not click the refresh button, so no search or model request can be made.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from competitive_scoring.agents import audit_results
from competitive_scoring.models import AuditResult, FinalBriefing, TraceEvent
from competitive_scoring.publication import _conclusion_for
from competitive_scoring.report import render_markdown
from competitive_scoring.report_store import ReportStore
from tests.test_publication import _live_briefing

PROJECT_ROOT = Path(__file__).resolve().parents[1]
APP_PATH = PROJECT_ROOT / "streamlit_app.py"


def _briefing(*, mode: str, conclusion: str, audit_passed: bool = True) -> FinalBriefing:
    """Build a canonical sample and place the test marker in visible content."""

    sample = (
        _live_briefing()
        if mode == "live"
        else FinalBriefing.model_validate_json(
            (PROJECT_ROOT / "examples" / "sample_briefing.json").read_text(encoding="utf-8")
        )
    )
    companies = []
    for index, item in enumerate(sample.companies):
        sources = [
            source.model_copy(
                update={
                    "source_type": "official_document" if source_index == 0 else source.source_type,
                    "authority": "primary_company",
                    "excerpt": source.excerpt or "Fixture evidence.",
                    "tags": list(dict.fromkeys([*source.tags, "you_primary"])),
                }
            )
            for source_index, source in enumerate(item.research.sources)
        ]
        business = item.research.business
        if index == 0:
            business = business.model_copy(update={"summary": f"{conclusion} — {business.summary}"})
        research = item.research.model_copy(update={"sources": sources, "business": business})
        companies.append(item.model_copy(update={"research": research}))

    reports = {item.research.company.ticker: item.research for item in companies}
    books = {item.book_score.company.ticker: item.book_score for item in companies}
    clean_audit = audit_results(
        reports,
        books,
        retry_count=sample.audit.retry_count,
        mode=mode,
    )
    chosen_audit = clean_audit if audit_passed else AuditResult(passed=False)
    canonical_conclusion = _conclusion_for(
        mode=mode,
        audit=chosen_audit,
        ranked=companies,
    )
    briefing = sample.model_copy(
        update={
            "mode": mode,
            "companies": companies,
            "conclusion": canonical_conclusion,
            "audit": chosen_audit,
            "markdown": "",
        }
    )
    return briefing.model_copy(update={"markdown": render_markdown(briefing)})


def _trace(detail: str) -> list[TraceEvent]:
    return [
        TraceEvent(
            stage="Stage 4",
            agent="Audit Agent",
            status="completed",
            detail=detail,
            timestamp=datetime(2026, 9, 4, 9, 0, tzinfo=UTC),
        )
    ]


def _configure_isolated_app(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    mode: str,
) -> ReportStore:
    """Point one page run at temporary storage and a harmless placeholder book."""

    book = tmp_path / "book.pdf"
    book.touch()
    legacy = tmp_path / "legacy"
    legacy.mkdir()
    store_base = tmp_path / "report-store"

    monkeypatch.setenv("APP_MODE", mode)
    monkeypatch.setenv("BOOK_PDF_PATH", str(book))
    monkeypatch.setenv("COMPETITIVE_SCORING_REPORT_STORE_BASE", str(store_base))
    monkeypatch.setenv("COMPETITIVE_SCORING_LEGACY_OUTPUTS_DIR", str(legacy))

    # Harmless placeholders make the live sidebar fully configured. Merely
    # opening the page performs no request; the user must click Run all agents.
    monkeypatch.setenv("YDC_API_KEY", "ui-test-you-key")
    monkeypatch.setenv("NEBIUS_API_KEY", "ui-test-nebius-key")
    monkeypatch.setenv("NEBIUS_MODEL", "moonshotai/Kimi-K3")
    monkeypatch.setenv("LLM_PROVIDER", "nebius")

    return ReportStore.for_mode(mode, base_dir=store_base)  # type: ignore[arg-type]


def _run_page() -> AppTest:
    page = AppTest.from_file(str(APP_PATH), default_timeout=20).run()
    assert not page.exception
    return page


def _markdown_values(page: AppTest) -> list[str]:
    return [str(element.value) for element in page.markdown]


def test_attempt_only_is_explained_but_never_rendered(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _configure_isolated_app(monkeypatch, tmp_path, mode="live")
    rejected_text = "REJECTED DRAFT MUST NEVER REACH THE PAGE"
    store.save_result(
        _briefing(mode="live", conclusion=rejected_text, audit_passed=False),
        _trace("Rejected."),
    )

    page = _run_page()

    assert not any(rejected_text in value for value in _markdown_values(page))
    assert not page.dataframe
    assert "Result" not in [element.value for element in page.subheader]
    assert any("latest draft did not pass" in element.value for element in page.info)


def test_valid_latest_report_is_rendered(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = _configure_isolated_app(monkeypatch, tmp_path, mode="live")
    validated_text = "VALIDATED REPORT IS THE ONLY PUBLISHED RESULT"
    store.save(
        _briefing(mode="live", conclusion=validated_text),
        _trace("Passed."),
        run_metadata={"provider": "Nebius", "model": "moonshotai/Kimi-K3"},
    )

    page = _run_page()

    assert any(validated_text in value for value in _markdown_values(page))
    assert "Result" in [element.value for element in page.subheader]
    assert len(page.dataframe) == 5
    assert any("Loaded saved validated report" in element.value for element in page.success)


def test_failed_newer_attempt_cannot_replace_or_disturb_valid_latest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _configure_isolated_app(monkeypatch, tmp_path, mode="live")
    validated_text = "STABLE VALIDATED REPORT"
    rejected_text = "NEWER FAILED ATTEMPT"
    store.save(
        _briefing(mode="live", conclusion=validated_text),
        _trace("Passed."),
    )
    store.save_result(
        _briefing(mode="live", conclusion=rejected_text, audit_passed=False),
        _trace("Rejected."),
    )

    page = _run_page()
    rendered = _markdown_values(page)

    assert any(validated_text in value for value in rendered)
    assert not any(rejected_text in value for value in rendered)
    assert not any("latest draft did not pass" in element.value for element in page.info)


def test_corrupt_attempt_is_ignored_when_valid_latest_exists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _configure_isolated_app(monkeypatch, tmp_path, mode="live")
    validated_text = "VALID REPORT SURVIVES CORRUPT DIAGNOSTIC FILE"
    store.save(
        _briefing(mode="live", conclusion=validated_text),
        _trace("Passed."),
    )
    store.latest_attempt_path.write_text('{"broken":', encoding="utf-8")

    page = _run_page()

    assert any(validated_text in value for value in _markdown_values(page))
    assert not page.warning
    assert not page.error


def test_clean_configured_page_has_no_warning_or_error_elements(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _configure_isolated_app(monkeypatch, tmp_path, mode="live")
    store.save(
        _briefing(mode="live", conclusion="CLEAN LIVE REPORT"),
        _trace("Passed."),
        run_metadata={"provider": "Nebius", "model": "moonshotai/Kimi-K3"},
    )

    page = _run_page()

    assert not page.warning
    assert not page.error
