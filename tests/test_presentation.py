"""Regression tests for result-table values sent to Streamlit's browser grid."""

from __future__ import annotations

from types import SimpleNamespace

import pyarrow as pa

from competitive_scoring.presentation import build_summary_table


def company_row(*, ticker: str, rank: int | None) -> SimpleNamespace:
    """Create only the attributes consumed by the presentation helper."""

    return SimpleNamespace(
        rank=rank,
        final_score=71.25 if rank is not None else None,
        research=SimpleNamespace(
            company=SimpleNamespace(ticker=ticker),
            core_score=73.0,
            confidence=0.68,
        ),
        book_score=SimpleNamespace(
            total_score=64.0 if rank is not None else None,
            coverage_weight=80 if rank is not None else 40,
        ),
    )


def test_audit_blocked_companies_use_browser_safe_rank_text() -> None:
    table = build_summary_table(  # type: ignore[arg-type]
        [company_row(ticker="PNGJL", rank=None), company_row(ticker="SENCO", rank=None)]
    )

    assert list(table.index) == [0, 1]
    assert table["Rank"].tolist() == ["Not ranked", "Not ranked"]
    assert table["Rank"].isna().sum() == 0
    assert str(table["Final"].dtype) == "Float64"

    # This is the exact serialization boundary that previously emitted a null
    # field and made the browser grid show "Error during cell creation".
    arrow_schema = pa.Table.from_pandas(table, preserve_index=False).schema
    assert all(field.type != pa.null() for field in arrow_schema)


def test_successful_ranks_are_also_strings_in_the_same_column() -> None:
    table = build_summary_table(  # type: ignore[arg-type]
        [company_row(ticker="PNGJL", rank=1), company_row(ticker="SENCO", rank=2)]
    )

    assert table["Rank"].tolist() == ["1", "2"]
    assert table["Ticker"].tolist() == ["PNGJL", "SENCO"]
    assert str(table["Final"].dtype) == "Float64"
