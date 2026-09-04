"""Small, provider-neutral progress messages for long research runs.

The graph is deliberately unaware of Streamlit.  It emits these plain Python
objects, and any caller (the web app, a CLI, or a test) may decide how to show
them.  Keeping this boundary small also means worker threads never call a
Streamlit function directly.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True, slots=True)
class ProgressEvent:
    """One real workflow transition reported by a LangGraph node."""

    key: str
    stage: str
    message: str
    status: Literal["started", "completed"]
    ticker: str | None = None


ProgressCallback = Callable[[ProgressEvent], None]


# The percentages reflect where this application normally spends its time.
# Four companies complete every ticker-prefixed item, so the full table adds to
# exactly 100.  A completed-key set makes retries idempotent and monotonic.
FIXED_PROGRESS_WEIGHTS: dict[str, float] = {
    "setup": 3.0,
    "peers": 2.0,
    "audit": 3.0,
    "final": 4.0,
}

TICKER_PROGRESS_WEIGHTS: dict[str, float] = {
    "source": 5.0,
    "business": 2.5,
    "fundamentals": 3.0,
    "management": 2.5,
    "technicals": 1.0,
    "valuation": 5.0,
    "book": 3.0,
}


def weight_for_key(key: str) -> float:
    """Return the configured percentage for a completed workflow key."""

    if key in FIXED_PROGRESS_WEIGHTS:
        return FIXED_PROGRESS_WEIGHTS[key]
    prefix, separator, _ticker = key.partition(":")
    if separator:
        return TICKER_PROGRESS_WEIGHTS.get(prefix, 0.0)
    return 0.0
