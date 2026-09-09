"""Streamlit interface for the PNGJL competitive-scoring graph.

The page follows a "report first" design:

* opening the app reads the last complete report from disk immediately;
* clicking **Run all agents** starts one background refresh;
* the old report stays visible while the progress panel updates; and
* only an audit-passed run replaces the last validated report.

This is intentionally written with extra comments so a new Python/Streamlit
learner can follow the control flow without first understanding LangGraph.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st

from competitive_scoring.config import Settings
from competitive_scoring.models import FinalBriefing, TraceEvent
from competitive_scoring.presentation import build_summary_table
from competitive_scoring.rag import BookKnowledgeBase
from competitive_scoring.report_store import ReportStore, ReportStoreError, StoredReport
from competitive_scoring.run_manager import RunManager, RunSnapshot
from competitive_scoring.tracing import recent_summaries, run_directory
from competitive_scoring.uploads import save_private_upload

PROJECT_ROOT = Path(__file__).resolve().parent


def _project_path_from_env(variable: str, default: str) -> Path:
    """Resolve an optional app path relative to this repository.

    The defaults are what a normal user needs. The environment override gives
    automated UI tests an isolated report directory, so they cannot accidentally
    publish or read a developer's real saved report.
    """

    configured = Path(os.getenv(variable, default).strip()).expanduser()
    if not configured.is_absolute():
        configured = PROJECT_ROOT / configured
    return configured.resolve()


REPORT_STORE_BASE = _project_path_from_env(
    "COMPETITIVE_SCORING_REPORT_STORE_BASE", "outputs/report_store"
)
LEGACY_OUTPUTS_DIR = _project_path_from_env(
    "COMPETITIVE_SCORING_LEGACY_OUTPUTS_DIR", "outputs"
)
IST = ZoneInfo("Asia/Kolkata")


@st.cache_resource
def get_run_manager() -> RunManager:
    """Create one run manager for the whole Streamlit server process.

    Streamlit executes this file again after every widget interaction. The
    cache keeps this object (and its worker thread) alive across those reruns.
    ``RunManager.start`` has its own lock too, so two browser tabs cannot launch
    duplicate paid API calls.
    """

    return RunManager()


def load_preloaded_report(
    store: ReportStore,
) -> tuple[StoredReport | None, StoredReport | None, list[str]]:
    """Load the best report without calling You.com, Nebius, or LangGraph.

    ``validated`` is the only report that may be rendered. Once one exists, the
    diagnostic attempt file is intentionally not opened at all: a corrupt or
    failed newer draft must never disturb a healthy published report. An
    audit-failed attempt is loaded only on a first run, so the page can explain
    why no report is available without exposing its tables or conclusions.
    """

    errors: list[str] = []
    try:
        # Older versions wrote bare JSON files into outputs/. This one-time,
        # idempotent migration lets the redesigned app preload those reports.
        store.bootstrap_from_legacy(LEGACY_OUTPUTS_DIR)
    except (OSError, ValueError, ReportStoreError) as exc:
        errors.append(f"Legacy report import: {exc}")

    try:
        validated = store.load_latest()
    except ReportStoreError as exc:
        validated = None
        errors.append(str(exc))

    if validated is not None:
        return validated, None, errors

    try:
        attempt = store.load_latest_attempt()
    except ReportStoreError as exc:
        attempt = None
        errors.append(str(exc))

    return validated, attempt, errors


def format_duration(seconds: float) -> str:
    """Turn seconds into a short fresher-friendly label such as ``2m 08s``."""

    total = max(0, round(seconds))
    minutes, remainder = divmod(total, 60)
    return f"{minutes}m {remainder:02d}s" if minutes else f"{remainder}s"


def approximate_eta(snapshot: RunSnapshot) -> str:
    """Estimate a range from actual completed work rather than a fake timer."""

    progress = snapshot.progress_percent
    elapsed = snapshot.elapsed_seconds
    if progress < 8 or elapsed < 10:
        return "approximately 3–5 minutes for a typical live run"

    # Parallel model calls finish in clusters, so a range is more honest than a
    # precise countdown. Bound the result so one early/late event cannot create
    # a wildly misleading ETA.
    projected_total = elapsed * 100 / max(progress, 1)
    remaining = max(0.0, projected_total - elapsed)
    lower = max(10.0, remaining * 0.7)
    upper = min(300.0, max(lower + 20.0, remaining * 1.45))
    return f"roughly {format_duration(lower)}–{format_duration(upper)} remaining"


def render_refresh_panel(manager: RunManager, *, selected_mode: str) -> None:
    """Poll background state every second while leaving the report untouched."""

    snapshot = manager.snapshot()
    if snapshot.mode != selected_mode or snapshot.status == "idle":
        return

    if snapshot.is_running:
        with st.container(border=True):
            st.markdown(f"#### ⟳ Refreshing {selected_mode} report")
            st.progress(
                snapshot.progress_percent / 100,
                text=f"{snapshot.progress_percent}% · {snapshot.stage}",
            )
            st.write(snapshot.message)
            completed_companies = sum(
                key.startswith("valuation:") for key in snapshot.completed_keys
            )
            st.caption(
                f"Company research completed: {completed_companies}/4 · "
                f"Elapsed: {format_duration(snapshot.elapsed_seconds)} · "
                f"ETA: {approximate_eta(snapshot)}"
            )
            st.caption("The complete previous report remains available below while this runs.")
        return

    # A fragment rerun updates only this panel. Trigger exactly one full-page
    # rerun when the worker finishes so the newly saved report is loaded from
    # disk. The session key prevents an infinite rerun loop.
    acknowledgement_key = f"_acknowledged_run_{selected_mode}"
    if snapshot.run_id and st.session_state.get(acknowledgement_key) != snapshot.run_id:
        st.session_state[acknowledgement_key] = snapshot.run_id
        st.rerun(scope="app")

    if snapshot.status == "succeeded":
        st.success(f"Refresh complete in {format_duration(snapshot.elapsed_seconds)}.")
    elif snapshot.status == "unvalidated":
        st.info(snapshot.message)
        if snapshot.error_detail:
            st.caption(snapshot.error_detail)
    else:
        st.error(snapshot.message)
        if snapshot.error_detail:
            with st.expander("Technical details", expanded=False):
                st.code(
                    f"{snapshot.error_type or 'Error'}: {snapshot.error_detail}",
                    language="text",
                )


def render_run_diagnostics(mode: str) -> None:
    """Include failed and unfinished runs even when no report was published."""
    summaries = recent_summaries(mode)
    if not summaries:
        return
    with st.expander("Run timings and logs", expanded=False):
        by_id = {item["run_id"]: item for item in summaries}
        selected = st.selectbox(
            "Research run", options=list(by_id), key=f"diagnostic_run_{mode}",
            format_func=lambda run_id: (
                f"{by_id[run_id]['started_at'][:19]} UTC · {by_id[run_id]['status']} "
                f"· {run_id[:8]}"
            ),
        )
        summary = by_id[selected]
        st.caption(f"Run ID: {selected} · Recorded elapsed time: "
                   f"{format_duration(summary['wall_seconds'])}")
        if summary["status"] == "running":
            st.caption("No completion recorded yet. This run may still be running or may have "
                       "been interrupted; the event log retains the last started steps.")
        if summary.get("active_steps"):
            st.write("Last recorded active steps: " + ", ".join(
                f"{row['operation']} ({row['metadata'].get('ticker', 'all')})"
                for row in summary["active_steps"]
            ))
        operations = summary.get("operations", [])
        if operations:
            st.dataframe(pd.DataFrame(operations).rename(columns={
                "operation": "Step", "count": "Calls", "total_seconds": "Total seconds",
                "max_seconds": "Longest call (s)", "failed": "Failed calls",
            }), hide_index=True, width="stretch")
            st.caption(summary["timing_note"])
        failures = summary.get("failed_attempts", [])
        if failures:
            st.write("Failed calls remain in these diagnostics even when a later retry succeeds.")
            st.dataframe(pd.DataFrame([{
                "Step": row["operation"], "Company": row["metadata"].get("ticker", ""),
                "Seconds": row["duration_seconds"],
                "Error": row["metadata"].get("error_kind", ""),
                "Type": row["metadata"].get("error_type", ""),
                "Attempt": row["metadata"].get("attempt"),
            } for row in failures]), hide_index=True, width="stretch")
        if summary.get("diagnostic_counts"):
            st.json(summary["diagnostic_counts"])
        if summary.get("audit_findings"):
            st.write("Evidence audit findings")
            st.dataframe(pd.DataFrame(summary["audit_findings"]), hide_index=True,
                         width="stretch")
        usage = summary.get("llm_usage", {})
        if any(value is not None for value in usage.values()):
            st.caption(f"Provider-reported tokens: input={usage.get('input_tokens')}, "
                       f"output={usage.get('output_tokens')}, "
                       f"reasoning={usage.get('reasoning_tokens')}. "
                       "Only responses with available usage are counted.")
        directory = run_directory(mode, selected)
        st.caption(f"Local logs: {directory}")
        for filename, label, mime in (
            ("summary.json", "Download timing summary", "application/json"),
            ("events.jsonl", "Download event log", "application/x-ndjson"),
        ):
            try:
                data = (directory / filename).read_bytes()
            except OSError:
                st.caption(f"{filename} is unavailable.")
                continue
            st.download_button(label, data, file_name=f"{selected}-{filename}", mime=mime,
                               key=f"{mode}-{selected}-{filename}")
        st.caption("Logs contain timings, counts and error classifications; prompts, document "
                   "text, model answers, credentials and raw exception messages are excluded.")


def render_report_header(stored: StoredReport) -> None:
    """Show freshness and provenance for an already validated report.

    This function deliberately has no ``validated=False`` branch. The caller's
    publication gate makes an unvalidated report impossible to render.
    """

    local_time = stored.saved_at.astimezone(IST).strftime("%d %B %Y, %I:%M %p IST")
    provider = str(stored.run_metadata.get("provider", "unknown"))
    model = str(stored.run_metadata.get("model", "not recorded"))
    if stored.run_metadata.get("legacy_import"):
        provider = "not recorded in legacy export"
        model = "not recorded in legacy export"

    st.success(f"Loaded saved validated report · {local_time}")
    st.caption(
        f"Report ID: {stored.report_id} · Mode: {stored.briefing.mode} · "
        f"Provider: {provider} · Model: {model}"
    )


def render_briefing(briefing: FinalBriefing, trace: list[TraceEvent]) -> None:
    """Render every section of one already-loaded report."""

    st.subheader("Result")
    st.write(briefing.conclusion)

    # Rank is an explicit text column. Audit-blocked results use ``Not ranked``
    # rather than a nullable dataframe index, which avoids browser-grid errors.
    frame = build_summary_table(briefing.companies)
    st.dataframe(
        frame,
        hide_index=True,
        width="stretch",
        column_config={
            "Rank": st.column_config.TextColumn("Rank"),
            "Confidence": st.column_config.ProgressColumn(
                format="percent", min_value=0, max_value=1
            ),
            "Book coverage": st.column_config.ProgressColumn(
                format="percent", min_value=0, max_value=1
            ),
        },
    )

    tabs = st.tabs([item.research.company.ticker for item in briefing.companies])
    for tab, item in zip(tabs, briefing.companies, strict=True):
        with tab:
            for warning in item.research.warnings:
                st.warning(warning)
            left, right = st.columns((1, 1))
            with left:
                st.markdown("#### Stage 2 evidence scores")
                core_text = (
                    f"{item.research.core_score:.2f}/100"
                    if item.research.core_score is not None
                    else "Not scored"
                )
                st.metric("Core Score", core_text)
                st.write(f"**Business:** {item.research.business.summary}")
                st.write(f"**Fundamentals:** {item.research.fundamentals.summary}")
                st.write(f"**Management:** {item.research.management.summary}")
                st.write(f"**Valuation:** {item.research.valuation.summary}")
                st.write(f"**Technicals:** {item.research.technicals.summary}")
            with right:
                st.markdown("#### Stage 3 private-book alignment")
                book_rows = []
                for category in item.book_score.categories:
                    pages = ", ".join(
                        f"p.{citation.printed_page} / PDF {citation.pdf_page}"
                        for citation in category.book_basis
                    )
                    book_rows.append(
                        {
                            "Category": category.label,
                            "Score /5": category.score,
                            "Weight": f"{category.weight}%",
                            "Book pages": pages or "Unresolved",
                        }
                    )
                st.dataframe(pd.DataFrame(book_rows), hide_index=True, width="stretch")

            st.markdown("#### Sources")
            for source in item.research.sources:
                if briefing.mode == "demo":
                    qualifier = " _(reference only in demo)_"
                elif source.source_type.startswith("manual_reference"):
                    qualifier = " _(manual link only; not fetched or used for scoring)_"
                elif source.source_type in {"screener_export", "user_upload"}:
                    qualifier = " _(user-provided local file)_"
                else:
                    qualifier = ""
                st.markdown(f"- [{source.title}]({source.url}){qualifier}")

    with st.expander("Agent execution trace"):
        for event in trace:
            st.write(f"`{event.stage}` **{event.agent}** — {event.detail}")

    with st.expander("Evidence audit"):
        st.write(f"Passed: **{briefing.audit.passed}**")
        for finding in briefing.audit.findings:
            ticker = f" [{finding.company_ticker}]" if finding.company_ticker else ""
            st.write(f"- {finding.severity.upper()}{ticker} `{finding.code}` — {finding.message}")

    col1, col2 = st.columns(2)
    with col1:
        st.download_button(
            "Download Markdown briefing",
            data=briefing.markdown,
            file_name="pngjl_competitive_scoring_briefing.md",
            mime="text/markdown",
            width="stretch",
        )
    with col2:
        st.download_button(
            "Download structured JSON",
            data=json.dumps(briefing.model_dump(mode="json"), indent=2),
            file_name="pngjl_competitive_scoring_briefing.json",
            mime="application/json",
            width="stretch",
        )

    st.caption(briefing.disclaimer)


# ---------------------------------------------------------------------------
# Page execution starts here.
# ---------------------------------------------------------------------------

st.set_page_config(page_title="PNGJL Competitive Scoring", page_icon="💎", layout="wide")

st.title("💎 PNGJL Competitive Investment Scoring")
st.caption(
    "LangGraph pipeline · You.com primary search/news · NSE/IR verification · "
    "Nebius live analysis · local Peaceful Investing RAG"
)

manager = get_run_manager()
manager_snapshot = manager.snapshot()
configured_mode = Settings.from_env().mode

with st.sidebar:
    st.header("Run settings")
    mode_options = ("demo", "live")
    mode = st.radio(
        "Data mode",
        mode_options,
        index=mode_options.index(configured_mode),
        disabled=manager_snapshot.is_running,
        help=(
            "Demo is deterministic and offline. Live searches You.com first, verifies with "
            "Indian primary sources/uploads, and uses Nebius by default for analysis."
        ),
    )
    st.text_input("Target", value="P N Gadgil Jewellers (PNGJL)", disabled=True)
    st.text_input("Horizon", value="2–3 years", disabled=True)
    st.info("‘RSA’ is interpreted as the standard Wilder RSI(14).")

    settings = Settings.from_env(mode=mode)  # type: ignore[arg-type]
    if settings.book_pdf_path.exists():
        st.success(f"Private book found: {settings.book_pdf_path.name}")
    else:
        st.error("Book not found. Set BOOK_PDF_PATH in .env.")

    if mode == "live":
        st.divider()
        st.subheader("Live connections")
        if settings.ydc_api_key:
            st.success("You.com primary search/news is configured.")
        else:
            st.warning("Add YDC_API_KEY to .env before running live mode.")

        if settings.llm_api_key and settings.llm_model:
            st.success(
                f"{settings.llm_provider.title()} is configured (model: {settings.llm_model})."
            )
        else:
            expected_key = (
                "NEBIUS_API_KEY + NEBIUS_MODEL"
                if settings.llm_provider == "nebius"
                else "OPENAI_API_KEY + OPENAI_MODEL"
            )
            st.warning(f"Add {expected_key} to .env before running live mode.")

        st.caption(
            "Research order: You.com first → NSE/company IR verification and gap fill → "
            "optional user uploads. ET and Moneycontrol stay manual-reference only."
        )
        upload_ticker = st.selectbox(
            "Company for the uploaded file",
            ("PNGJL", "KALYANKJIL", "SENCO", "THANGAMAYL"),
            disabled=manager_snapshot.is_running,
        )
        screener_file = st.file_uploader(
            "Optional Screener export (.csv or .xlsx)",
            type=("csv", "xlsx"),
            key=f"screener-upload-{upload_ticker}",
            disabled=manager_snapshot.is_running,
            help="Download it yourself from Screener; this app never scrapes screener.in.",
        )
        if screener_file is not None:
            try:
                saved = save_private_upload(
                    root=settings.screener_export_dir,
                    ticker=upload_ticker,
                    original_name=screener_file.name,
                    content=screener_file.getvalue(),
                    kind="screener",
                )
                st.caption(f"Saved locally as {saved.name}.")
            except ValueError as exc:
                st.error(str(exc))

        company_files = st.file_uploader(
            "Optional official filings/results (.pdf, .txt, .csv)",
            type=("pdf", "txt", "csv"),
            accept_multiple_files=True,
            key=f"company-documents-{upload_ticker}",
            disabled=manager_snapshot.is_running,
            help="Good inputs are the latest annual report, quarterly results, and presentation.",
        )
        for company_file in company_files:
            try:
                saved = save_private_upload(
                    root=settings.company_documents_dir,
                    ticker=upload_ticker,
                    original_name=company_file.name,
                    content=company_file.getvalue(),
                    kind="company_document",
                )
                st.caption(f"Saved locally: {upload_ticker}/{saved.name}.")
            except ValueError as exc:
                st.error(f"{company_file.name}: {exc}")

        st.info(
            "In live mode, You.com results and evidence excerpts (including uploaded files) "
            "are sent to your configured LLM for extraction. The investing book itself stays "
            "in local RAG and is not sent."
        )

    if st.button(
        "Build / refresh private book index",
        width="stretch",
        disabled=manager_snapshot.is_running,
    ):
        try:
            with st.spinner("Reading the book locally and building Chroma…"):
                result = BookKnowledgeBase(
                    pdf_path=settings.book_pdf_path,
                    index_dir=settings.book_index_dir,
                ).ensure_index(force=True)
            st.success(f"Indexed {result['pages']} pages into {result['chunks']} chunks.")
        except Exception as exc:  # noqa: BLE001 - UI boundary shows an actionable failure.
            st.exception(exc)

    run_clicked = st.button(
        "Run all agents",
        type="primary",
        width="stretch",
        disabled=manager_snapshot.is_running,
    )

store = ReportStore.for_mode(mode, base_dir=REPORT_STORE_BASE)  # type: ignore[arg-type]
validated_report, latest_attempt, preload_errors = load_preloaded_report(store)
# Pre-publication gate: never render a partial/latest-attempt report. It remains
# on disk for developer diagnosis, while users keep seeing the last fully
# validated snapshot (or a neutral first-run message).
displayed_report = validated_report

if run_clicked:
    try:
        if not manager.start(settings, store):
            st.warning("A report refresh is already running. This click did not start a duplicate.")
    except Exception as exc:  # noqa: BLE001 - fail before launching the background worker.
        st.error(f"The refresh could not start: {type(exc).__name__}: {exc}")

architecture_path = PROJECT_ROOT / "pngjl-competitive-investment-scoring-architecture-hybrid-v2.png"
with st.expander("See the sequential / parallel architecture", expanded=False):
    if architecture_path.exists():
        st.image(str(architecture_path), width="stretch")

if mode == "demo":
    st.info(
        "Demo mode uses invented values. It proves the software flow only and must not be "
        "used for an investment decision."
    )
else:
    st.info(
        "Live results are time-sensitive research, not personal financial advice. Confirm every "
        "important value in the linked exchange/company source."
    )

for preload_error in preload_errors:
    st.warning(preload_error)


@st.fragment(run_every=1.0)
def refresh_fragment() -> None:
    render_refresh_panel(manager, selected_mode=mode)
    render_run_diagnostics(mode)


refresh_fragment()

if displayed_report is not None:
    render_report_header(displayed_report)
    render_briefing(displayed_report.briefing, displayed_report.trace)
else:
    if latest_attempt is not None:
        st.info(
            "The latest draft did not pass the full pre-publication audit, so its tables and "
            "conclusions are not displayed. A clean refresh is required before publication."
        )
    else:
        st.info(
            "No validated saved report exists for this mode yet. Click **Run all agents** once; "
            "future app opens will preload the completed report."
        )
