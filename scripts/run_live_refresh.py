"""Run one live refresh and publish it only when every release check passes.

This small command is intentionally separate from Streamlit.  It lets a novice
watch each LangGraph stage in the terminal, while the same ReportStore used by
the app protects ``latest.json`` from partial or warning-bearing drafts.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path

from competitive_scoring.config import Settings
from competitive_scoring.graph import run_research
from competitive_scoring.progress import ProgressEvent
from competitive_scoring.publication import scan_for_publication
from competitive_scoring.report_store import ReportStore
from competitive_scoring.tracing import trace_run


def _show_progress(event: ProgressEvent) -> None:
    """Print safe stage metadata only—never API keys or raw private evidence."""

    timestamp = datetime.now(UTC).strftime("%H:%M:%S")
    ticker = f" [{event.ticker}]" if event.ticker else ""
    print(f"{timestamp} {event.status.upper():9} {event.stage}{ticker}: {event.message}", flush=True)


def main() -> None:
    """Build, audit, store, and report the result of one Kimi live run."""

    settings = Settings.from_env(mode="live")
    with trace_run(settings) as diagnostics:
        try:
            _refresh(settings, diagnostics)
        finally:
            print(f"Run diagnostics: {diagnostics.directory}", flush=True)


def _refresh(settings: Settings, diagnostics) -> None:
    print(
        f"Starting live refresh with {settings.llm_provider}/{settings.llm_model}; "
        f"cutoff={settings.research_as_of.isoformat()}",
        flush=True,
    )

    briefing, trace = run_research(settings, progress_callback=_show_progress)
    base_dir = Path(
        os.getenv("COMPETITIVE_SCORING_REPORT_STORE_BASE", "outputs/report_store")
    )
    store = ReportStore.for_mode("live", base_dir=base_dir)
    stored = store.save_result(
        briefing,
        trace,
        run_metadata={
            "trigger": "validated_live_refresh_script",
            "run_id": diagnostics.run_id,
            "provider": settings.llm_provider,
            "model": settings.llm_model or "not configured",
        },
    )
    scan = scan_for_publication(stored.briefing, tuple(stored.trace))
    if not scan.passed:
        diagnostics.status = "unvalidated"
        print(f"REJECTED report_id={stored.report_id}; issues={len(scan.issues)}", flush=True)
        for issue in scan.issues:
            ticker = f" [{issue.company_ticker}]" if issue.company_ticker else ""
            print(f"- {issue.code}{ticker}: {issue.message}", flush=True)
        raise SystemExit(2)

    ranks = ", ".join(
        f"{item.rank}:{item.research.company.ticker}={item.final_score:.2f}"
        for item in stored.briefing.companies
        if item.rank is not None and item.final_score is not None
    )
    print(f"PUBLISHED report_id={stored.report_id}; {ranks}", flush=True)
    diagnostics.status = "succeeded"


if __name__ == "__main__":
    main()
