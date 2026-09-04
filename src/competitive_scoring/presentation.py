"""Small presentation helpers shared by Streamlit and UI-focused tests."""

from __future__ import annotations

from collections.abc import Sequence

import pandas as pd

from .models import ScoredCompany


def build_summary_table(companies: Sequence[ScoredCompany]) -> pd.DataFrame:
    """Return a browser-safe table for ranked or audit-blocked results.

    A failed evidence audit deliberately leaves ``rank`` as ``None``. Making
    that nullable field the dataframe index caused Streamlit's browser grid to
    display "Error during cell creation". Rank is therefore an ordinary text
    column: successful runs show ``1``, ``2``, etc.; blocked runs clearly show
    ``Not ranked``.
    """

    rows = []
    for item in companies:
        rows.append(
            {
                "Rank": str(item.rank) if item.rank is not None else "Not ranked",
                "Ticker": item.research.company.ticker,
                "Core": item.research.core_score,
                "Book": item.book_score.total_score,
                "Final": item.final_score,
                "Confidence": item.research.confidence,
                "Book coverage": item.book_score.coverage_weight / 100,
            }
        )
    columns = (
        "Rank",
        "Ticker",
        "Core",
        "Book",
        "Final",
        "Confidence",
        "Book coverage",
    )
    frame = pd.DataFrame(rows, columns=columns)

    # Explicit extension dtypes prevent an all-None score column (normally
    # Arrow NullType) from reaching Streamlit's JavaScript grid. This occurs for
    # Final whenever the audit blocks every company from being ranked.
    frame["Rank"] = pd.array(frame["Rank"], dtype="string")
    frame["Ticker"] = pd.array(frame["Ticker"], dtype="string")
    for column in ("Core", "Book", "Final", "Confidence", "Book coverage"):
        frame[column] = pd.array(frame[column], dtype="Float64")
    return frame
