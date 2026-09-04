"""Durable storage for the last successfully audited research report.

Streamlit session state disappears when the process restarts.  This module keeps
the application-facing result in a small, versioned JSON envelope instead.  A
snapshot contains the complete :class:`FinalBriefing` *and* its execution trace,
so loading a report from disk is enough to render the same page without running
any agents.

``latest.json`` is deliberately stricter than an ordinary result: only a report
whose evidence audit passed can be promoted there.  A structurally valid failed
audit may still be kept as ``latest_attempt.json`` for developer diagnosis, but
the Streamlit publication gate never renders it.  Writes use a temporary file in
the destination directory followed by ``os.replace``; readers therefore observe
either the previous complete JSON document or the new complete document, never a
partly-written report.
"""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from uuid import UUID, uuid4

from pydantic import Field, JsonValue, ValidationError, field_validator, model_validator

from .models import FinalBriefing, StrictModel, TraceEvent
from .publication import PublicationValidationError, require_publishable

REPORT_SCHEMA_VERSION = 1
DEFAULT_REPORT_STORE_BASE = Path("outputs/report_store")
DEFAULT_REPORTS_DIR = DEFAULT_REPORT_STORE_BASE / "live"
ReportMode = Literal["demo", "live"]


class StoredReport(StrictModel):
    """Versioned, self-contained representation of one completed workflow run."""

    schema_version: Literal[1] = REPORT_SCHEMA_VERSION
    report_id: UUID
    saved_at: datetime
    briefing: FinalBriefing
    trace: list[TraceEvent]
    run_metadata: dict[str, JsonValue] = Field(default_factory=dict)

    @field_validator("saved_at")
    @classmethod
    def saved_at_must_include_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("saved_at must include a timezone")
        return value

    @model_validator(mode="after")
    def report_must_be_complete(self) -> StoredReport:
        if not self.briefing.companies:
            raise ValueError("A stored report must contain at least one company.")
        if not self.briefing.markdown.strip():
            raise ValueError("A stored report must contain its rendered Markdown briefing.")
        if not self.trace:
            raise ValueError("A stored report must contain its agent execution trace.")
        return self


class ReportStoreError(RuntimeError):
    """Raised when an on-disk snapshot cannot be read or validated safely."""


class ReportStore:
    """Persist and restore fully audited workflow results.

    ``root`` is intentionally configurable so tests and alternate deployments do
    not share state.  :meth:`for_mode` produces the normal layout::

        outputs/report_store/live/
        ├── latest.json          # audit-passed only
        ├── latest_attempt.json  # most recent structurally valid result
        └── history/
            └── 2026-09-04T14-22-03.123456Z_<report-id>.json

    ``clock`` and ``id_factory`` are dependency-injection seams for deterministic
    tests; normal callers should omit them.
    """

    def __init__(
        self,
        root: str | Path = DEFAULT_REPORTS_DIR,
        *,
        mode: ReportMode | None = None,
        clock: Callable[[], datetime] | None = None,
        id_factory: Callable[[], UUID] | None = None,
    ) -> None:
        self.root = Path(root)
        self.latest_path = self.root / "latest.json"
        self.latest_attempt_path = self.root / "latest_attempt.json"
        self.history_dir = self.root / "history"
        self.mode = mode
        self._clock = clock or (lambda: datetime.now(UTC))
        self._id_factory = id_factory or uuid4

    @classmethod
    def for_mode(
        cls,
        mode: ReportMode,
        *,
        base_dir: str | Path = DEFAULT_REPORT_STORE_BASE,
        clock: Callable[[], datetime] | None = None,
        id_factory: Callable[[], UUID] | None = None,
    ) -> ReportStore:
        """Construct an isolated ``demo`` or ``live`` store.

        Rejecting arbitrary mode strings also prevents a UI selector from
        escaping ``base_dir`` through values such as ``../somewhere``.
        """

        if mode not in ("demo", "live"):
            raise ValueError("Report mode must be 'demo' or 'live'.")
        return cls(
            Path(base_dir) / mode,
            mode=mode,
            clock=clock,
            id_factory=id_factory,
        )

    def save(
        self,
        briefing: FinalBriefing,
        trace: Sequence[TraceEvent],
        *,
        run_metadata: Mapping[str, JsonValue] | None = None,
    ) -> StoredReport:
        """Validate and atomically promote a successful report.

        Validation happens before either destination is touched.  Nested models
        are converted back to plain JSON values before validation so even models
        created through Pydantic's unsafe ``model_construct`` path cannot bypass
        the storage boundary.
        """

        snapshot = self._create_snapshot(briefing, trace, run_metadata)
        _require_passed_audit(snapshot)
        self._write_attempt(snapshot)
        self._promote(snapshot)
        return snapshot

    def save_attempt(
        self,
        briefing: FinalBriefing,
        trace: Sequence[TraceEvent],
        *,
        run_metadata: Mapping[str, JsonValue] | None = None,
    ) -> StoredReport:
        """Atomically keep a structurally valid run without promoting it."""

        snapshot = self._create_snapshot(briefing, trace, run_metadata)
        self._write_attempt(snapshot)
        return snapshot

    def save_result(
        self,
        briefing: FinalBriefing,
        trace: Sequence[TraceEvent],
        *,
        run_metadata: Mapping[str, JsonValue] | None = None,
    ) -> StoredReport:
        """Save every completed attempt and promote it only after the full release scan.

        A rejected report is an expected research outcome, not a storage error.
        It remains available as ``latest_attempt.json`` for diagnosis and this
        method returns normally; the previous validated report is untouched.
        """

        snapshot = self._create_snapshot(briefing, trace, run_metadata)
        self._write_attempt(snapshot)
        try:
            _require_passed_audit(snapshot)
        except PublicationValidationError:
            return snapshot
        self._promote(snapshot)
        return snapshot

    def load_latest(self) -> StoredReport | None:
        """Return the last promoted report, or ``None`` before the first run.

        A missing file is normal on first launch.  Corrupt or obsolete data is
        surfaced as :class:`ReportStoreError` rather than silently being mistaken
        for "no report", which lets the UI explain the recovery action clearly.
        """

        if not self.latest_path.exists():
            return None
        snapshot = self._load_path(self.latest_path)
        try:
            self._require_matching_mode(snapshot)
            _require_passed_audit(snapshot)
        except ValueError as exc:
            raise ReportStoreError(
                f"Stored latest report at {self.latest_path} did not pass its evidence audit."
            ) from exc
        return snapshot

    def load_latest_attempt(self) -> StoredReport | None:
        """Return the newest structurally valid attempt, regardless of audit result."""

        if not self.latest_attempt_path.exists():
            return None
        snapshot = self._load_path(self.latest_attempt_path)
        try:
            self._require_matching_mode(snapshot)
        except ValueError as exc:
            raise ReportStoreError(
                f"Stored attempt at {self.latest_attempt_path} belongs to another mode."
            ) from exc
        return snapshot

    def list_history(self) -> list[Path]:
        """Return immutable history snapshots newest first."""

        if not self.history_dir.exists():
            return []
        return sorted(self.history_dir.glob("*.json"), reverse=True)

    def load_history(self, path: str | Path) -> StoredReport:
        """Load one path returned by :meth:`list_history`.

        Requiring the resolved path to live directly in ``history/`` prevents a
        user-controlled selector value from turning into an arbitrary file read.
        """

        requested = Path(path)
        # ``list_history`` may itself return a relative path when the store root
        # was relative.  Only a bare filename needs to be joined to history_dir.
        candidate = self.history_dir / requested if requested.parent == Path() else requested
        resolved_history = self.history_dir.resolve()
        resolved_candidate = candidate.resolve()
        if resolved_candidate.parent != resolved_history:
            raise ValueError("History report must be a JSON file directly inside history/.")
        if resolved_candidate.suffix != ".json":
            raise ValueError("History report must be a JSON file.")
        snapshot = self._load_path(resolved_candidate)
        try:
            self._require_matching_mode(snapshot)
            _require_passed_audit(snapshot)
        except ValueError as exc:
            raise ReportStoreError(f"History report at {resolved_candidate} is not validated.") from exc
        return snapshot

    def bootstrap_from_legacy(
        self,
        outputs_dir: str | Path = "outputs",
        *,
        mode: ReportMode | None = None,
    ) -> StoredReport | None:
        """Import the newest matching legacy ``outputs/*.json`` exactly once.

        Legacy briefing files predate persisted traces.  The synthesized warning
        event makes that absence explicit instead of pretending the trace was
        recovered.  Existing store state always wins, making startup calls
        idempotent and preventing an old export from replacing a newer run.
        """

        selected_mode = mode or self.mode
        if selected_mode not in ("demo", "live"):
            raise ValueError("A demo or live mode is required for legacy bootstrap.")
        if self.latest_path.exists() or self.latest_attempt_path.exists():
            return None

        directory = Path(outputs_dir)
        try:
            candidates = sorted(
                directory.glob("*.json"),
                key=lambda path: path.stat().st_mtime_ns,
                reverse=True,
            )
        except OSError as exc:
            raise ReportStoreError(f"Could not inspect legacy reports in {directory}.") from exc

        for candidate in candidates:
            try:
                briefing = FinalBriefing.model_validate_json(candidate.read_text(encoding="utf-8"))
                modified_at = datetime.fromtimestamp(candidate.stat().st_mtime, tz=UTC)
            except (OSError, UnicodeError, ValidationError, ValueError):
                continue
            if briefing.mode != selected_mode:
                continue

            # Re-check immediately before writing in case another startup worker
            # populated the store while the legacy files were being inspected.
            if self.latest_path.exists() or self.latest_attempt_path.exists():
                return None
            trace = [
                TraceEvent(
                    stage="Persistence",
                    agent="Report Store",
                    status="warning",
                    detail=(
                        f"Imported legacy briefing from {candidate.name}; the original agent "
                        "execution trace was not available in that export."
                    ),
                    timestamp=modified_at,
                )
            ]
            return self.save_result(
                briefing,
                trace,
                run_metadata={
                    "legacy_import": True,
                    "legacy_source": str(candidate),
                },
            )
        return None

    def _create_snapshot(
        self,
        briefing: FinalBriefing,
        trace: Sequence[TraceEvent],
        run_metadata: Mapping[str, JsonValue] | None,
    ) -> StoredReport:
        if self.mode is not None and briefing.mode != self.mode:
            raise ValueError(
                f"Cannot save a {briefing.mode!r} briefing in the {self.mode!r} report store."
            )
        saved_at = self._clock()
        if saved_at.tzinfo is None or saved_at.utcoffset() is None:
            raise ValueError("The report store clock must return a timezone-aware datetime.")
        saved_at = saved_at.astimezone(UTC)
        return StoredReport.model_validate(
            {
                "schema_version": REPORT_SCHEMA_VERSION,
                "report_id": self._id_factory(),
                "saved_at": saved_at,
                "briefing": briefing.model_dump(mode="json"),
                "trace": [event.model_dump(mode="json") for event in trace],
                "run_metadata": dict(run_metadata or {}),
            }
        )

    def _write_attempt(self, snapshot: StoredReport) -> None:
        _atomic_write_text(self.latest_attempt_path, _serialize(snapshot))

    def _promote(self, snapshot: StoredReport) -> None:
        _require_passed_audit(snapshot)
        serialized = _serialize(snapshot)
        # The UUID makes concurrent runs collision-safe while the UTC prefix keeps
        # ordinary directory listings chronologically useful.
        timestamp = snapshot.saved_at.astimezone(UTC).strftime("%Y-%m-%dT%H-%M-%S.%fZ")
        history_path = self.history_dir / f"{timestamp}_{snapshot.report_id.hex}.json"
        # History is committed first. If latest promotion fails, readers retain
        # their previous validated report and the new complete snapshot is still
        # recoverable from history.
        _atomic_write_text(history_path, serialized)
        _atomic_write_text(self.latest_path, serialized)

    def _require_matching_mode(self, snapshot: StoredReport) -> None:
        if self.mode is not None and snapshot.briefing.mode != self.mode:
            raise ValueError(
                f"Stored {snapshot.briefing.mode!r} briefing does not match "
                f"the {self.mode!r} report store."
            )

    @staticmethod
    def _load_path(path: Path) -> StoredReport:
        try:
            payload = path.read_text(encoding="utf-8")
            return StoredReport.model_validate_json(payload)
        except (OSError, UnicodeError, ValidationError, ValueError) as exc:
            raise ReportStoreError(f"Could not load a valid stored report from {path}.") from exc


def load_latest_report(root: str | Path = DEFAULT_REPORTS_DIR) -> StoredReport | None:
    """Convenience wrapper for callers that do not need a reusable store object."""

    return ReportStore(root).load_latest()


def save_report(
    briefing: FinalBriefing,
    trace: Sequence[TraceEvent],
    *,
    root: str | Path = DEFAULT_REPORTS_DIR,
    run_metadata: Mapping[str, JsonValue] | None = None,
) -> StoredReport:
    """Convenience wrapper that validates and promotes one report."""

    return ReportStore(root).save(briefing, trace, run_metadata=run_metadata)


def _atomic_write_text(path: Path, content: str) -> None:
    """Replace ``path`` atomically with durable UTF-8 text from the same directory."""

    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
    except BaseException:
        # ``os.fdopen`` owns the descriptor after it succeeds.  If it fails before
        # taking ownership, closing here prevents a descriptor leak.
        try:
            os.close(descriptor)
        except OSError:
            pass
        temporary_path.unlink(missing_ok=True)
        raise


def _serialize(snapshot: StoredReport) -> str:
    return json.dumps(
        snapshot.model_dump(mode="json"),
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    ) + "\n"


def _require_passed_audit(snapshot: StoredReport) -> None:
    """Apply the shared graph-independent release gate at every storage boundary."""

    require_publishable(snapshot.briefing, tuple(snapshot.trace))
