"""Run the expensive graph once in the background while Streamlit stays usable.

Streamlit reruns the page script after every interaction.  Starting a research
job directly inside that script can therefore create duplicate You.com and LLM
requests.  ``RunManager`` owns a process-wide lock and one worker thread so all
browser sessions observe the same single run.

The worker never calls Streamlit.  It only updates an immutable snapshot under
a lock; the page polls that snapshot and decides how to display it.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal
from uuid import uuid4

from .config import Settings
from .graph import run_research
from .llm import StructuredOutputError
from .models import FinalBriefing, TraceEvent
from .progress import ProgressEvent, weight_for_key
from .publication import scan_for_publication
from .report_store import ReportStore

RunStatus = Literal["idle", "running", "succeeded", "unvalidated", "failed"]
ResearchRunner = Callable[..., tuple[FinalBriefing, list[TraceEvent]]]


@dataclass(frozen=True, slots=True)
class RunSnapshot:
    """Thread-safe public view of the current or most recent refresh."""

    run_id: str | None = None
    mode: str | None = None
    status: RunStatus = "idle"
    progress_percent: int = 0
    stage: str = "Ready"
    message: str = "No refresh is currently running."
    started_at: datetime | None = None
    finished_at: datetime | None = None
    completed_keys: tuple[str, ...] = ()
    report_id: str | None = None
    error_type: str | None = None
    error_detail: str | None = None

    @property
    def is_running(self) -> bool:
        return self.status == "running"

    @property
    def elapsed_seconds(self) -> float:
        """Return elapsed wall time without requiring UI-side date arithmetic."""

        if self.started_at is None:
            return 0.0
        endpoint = self.finished_at or datetime.now(UTC)
        return max(0.0, (endpoint - self.started_at).total_seconds())


class RunManager:
    """Single-flight controller for manual background research refreshes."""

    def __init__(self, *, runner: ResearchRunner = run_research) -> None:
        self._runner = runner
        self._lock = threading.RLock()
        self._finished = threading.Event()
        self._snapshot = RunSnapshot()
        self._worker: threading.Thread | None = None

    def snapshot(self) -> RunSnapshot:
        """Return a copy-like immutable snapshot safe for any UI thread."""

        with self._lock:
            return self._snapshot

    def start(self, settings: Settings, store: ReportStore) -> bool:
        """Start one run and return ``False`` if another run is already active."""

        with self._lock:
            if self._snapshot.is_running:
                return False

            run_id = uuid4().hex
            self._finished.clear()
            self._snapshot = RunSnapshot(
                run_id=run_id,
                mode=settings.mode,
                status="running",
                progress_percent=0,
                stage="Starting",
                message="Validating settings and preparing the LangGraph workflow…",
                started_at=datetime.now(UTC),
            )
            self._worker = threading.Thread(
                target=self._run,
                args=(run_id, settings, store),
                name=f"competitive-scoring-{settings.mode}-{run_id[:8]}",
                daemon=True,
            )
            self._worker.start()
            return True

    def wait(self, timeout: float | None = None) -> bool:
        """Testing/CLI helper; Streamlit should poll :meth:`snapshot` instead."""

        return self._finished.wait(timeout)

    def _on_progress(self, run_id: str, event: ProgressEvent) -> None:
        with self._lock:
            current = self._snapshot
            # Ignore a late callback from an obsolete thread defensively.
            if current.run_id != run_id or not current.is_running:
                return
            completed = set(current.completed_keys)
            percent = current.progress_percent
            if event.status == "completed" and event.key not in completed:
                completed.add(event.key)
                percent = min(99, round(sum(weight_for_key(key) for key in completed)))
            self._snapshot = RunSnapshot(
                run_id=current.run_id,
                mode=current.mode,
                status=current.status,
                progress_percent=max(current.progress_percent, percent),
                stage=event.stage,
                message=event.message,
                started_at=current.started_at,
                completed_keys=tuple(sorted(completed)),
            )

    def _run(self, run_id: str, settings: Settings, store: ReportStore) -> None:
        try:
            briefing, trace = self._runner(
                settings,
                progress_callback=lambda event: self._on_progress(run_id, event),
            )
            stored = store.save_result(
                briefing,
                trace,
                run_metadata={
                    "trigger": "streamlit_manual",
                    "provider": settings.llm_provider if settings.mode == "live" else "offline",
                    "model": settings.llm_model or "deterministic-demo",
                },
            )
            # Never trust only the graph's boolean audit flag here.  The shared
            # publication scan recalculates audit, scores, ranks, conclusion,
            # and Markdown from the final data that was actually stored.
            publication_scan = scan_for_publication(stored.briefing, tuple(stored.trace))
            if publication_scan.passed:
                self._finish(
                    run_id,
                    status="succeeded",
                    message="The validated report is ready and has replaced the previous report.",
                    report_id=str(stored.report_id),
                )
            else:
                self._finish(
                    run_id,
                    status="unvalidated",
                    message=(
                        "The agents finished, but the evidence audit did not approve a ranking. "
                        "The previous validated report has not been replaced."
                    ),
                    report_id=str(stored.report_id),
                    error_type="EvidenceAudit",
                    error_detail=(
                        f"{len(publication_scan.issues)} publication check(s) require review: "
                        + ", ".join(issue.code for issue in publication_scan.issues[:8])
                    ),
                )
        except StructuredOutputError as exc:
            self._finish(
                run_id,
                status="failed",
                message="The model response did not pass structured-output validation.",
                error_type="StructuredOutputError",
                error_detail=(
                    f"{exc.context or exc.schema_name}; {exc.provider.title()} / {exc.model}; "
                    f"attempts={exc.attempts}; {exc.feedback}"
                ),
            )
        except Exception as exc:  # noqa: BLE001 - background job safety boundary.
            # Store only a compact diagnostic. Raw model output and evidence may
            # contain private uploads and therefore must never leak into the UI.
            detail = str(exc).strip() or type(exc).__name__
            self._finish(
                run_id,
                status="failed",
                message="The refresh stopped safely; the previous report is still available.",
                error_type=type(exc).__name__,
                error_detail=detail[:600],
            )
        finally:
            self._finished.set()

    def _finish(
        self,
        run_id: str,
        *,
        status: RunStatus,
        message: str,
        report_id: str | None = None,
        error_type: str | None = None,
        error_detail: str | None = None,
    ) -> None:
        with self._lock:
            current = self._snapshot
            if current.run_id != run_id:
                return
            self._snapshot = RunSnapshot(
                run_id=run_id,
                mode=current.mode,
                status=status,
                progress_percent=100 if status in {"succeeded", "unvalidated"} else current.progress_percent,
                stage="Complete" if status in {"succeeded", "unvalidated"} else "Refresh failed",
                message=message,
                started_at=current.started_at,
                finished_at=datetime.now(UTC),
                completed_keys=current.completed_keys,
                report_id=report_id,
                error_type=error_type,
                error_detail=error_detail,
            )
