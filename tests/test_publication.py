"""Adversarial tests for the last boundary before a report reaches a user."""

from __future__ import annotations

import re
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

import competitive_scoring.cli as cli_module
from competitive_scoring.agents import (
    _fundamental_growth_score,
    _fundamental_quality_score,
    audit_results,
)
from competitive_scoring.models import FinalBriefing, TraceEvent
from competitive_scoring.publication import _conclusion_for, scan_for_publication
from competitive_scoring.report import render_markdown
from competitive_scoring.scoring import (
    calculate_core_score,
    calculate_final_score,
    rank_companies,
    research_confidence,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _trace(*, warning: bool = False) -> list[TraceEvent]:
    return [
        TraceEvent(
            stage="Stage 4",
            agent="Orchestrator Agent",
            status="warning" if warning else "completed",
            detail="warning" if warning else "Validated ranking compiled.",
            timestamp=datetime(2026, 9, 4, 12, tzinfo=UTC),
        )
    ]


def _codes(briefing: FinalBriefing) -> set[str]:
    return {issue.code for issue in scan_for_publication(briefing, tuple(_trace())).issues}


def _without_demo_words(value: str) -> str:
    return re.sub(r"\b(?:illustrative|demo)\b", "test", value, flags=re.IGNORECASE)


def _clean_block(block):
    metrics = []
    for metric in block.metrics:
        value = metric.value
        if isinstance(value, str):
            value = _without_demo_words(value)
        metrics.append(
            metric.model_copy(
                update={
                    "value": value,
                    "period": _without_demo_words(metric.period),
                    "measurement_type": "reported",
                    "flags": [],
                }
            )
        )
    return block.model_copy(
        update={
            "summary": _without_demo_words(block.summary),
            "strengths": [_without_demo_words(value) for value in block.strengths],
            "risks": [_without_demo_words(value) for value in block.risks],
            "metrics": metrics,
        }
    )


def _live_briefing() -> FinalBriefing:
    """Turn the checked-in shape into a canonical, non-illustrative live fixture."""

    sample = FinalBriefing.model_validate_json(
        (PROJECT_ROOT / "examples" / "sample_briefing.json").read_text(encoding="utf-8")
    )
    rebuilt = []
    for item in sample.companies:
        sources = [
            source.model_copy(
                update={
                    "source_type": "official_document" if index == 0 else "you_web",
                    "authority": "primary_company",
                    "excerpt": "Current audited company and market evidence.",
                    "tags": ["you_primary", "fundamentals", "valuation", "management"],
                    "published_at": sample.as_of_date.isoformat(),
                }
            )
            for index, source in enumerate(item.research.sources)
        ]
        business = _clean_block(item.research.business)
        fundamentals = _clean_block(item.research.fundamentals)
        management = _clean_block(item.research.management)
        technicals = _clean_block(item.research.technicals)
        valuation = _clean_block(item.research.valuation)
        fundamentals = fundamentals.model_copy(
            update={
                "metrics": [
                    metric
                    for metric in fundamentals.metrics
                    if metric.code
                    in {
                        "roe",
                        "roce",
                        "revenue_growth",
                        "profit_growth",
                        "debt_equity",
                        "net_debt_equity",
                        "interest_coverage",
                        "current_ratio",
                        "cfo_pat",
                    }
                ]
            }
        )
        valuation = valuation.model_copy(
            update={
                "metrics": [
                    metric
                    for metric in valuation.metrics
                    if metric.code in {"pe_ttm", "peg", "price_sales", "gsec_10y_yield"}
                ]
            }
        )

        values = {
            metric.code: float(metric.value)
            for metric in fundamentals.metrics
            if isinstance(metric.value, (int, float))
        }
        quality = _fundamental_quality_score(values)
        growth = _fundamental_growth_score(values)
        fundamentals = fundamentals.model_copy(
            update={"score": quality, "growth_score": growth}
        )
        pe = next(float(metric.value) for metric in valuation.metrics if metric.code == "pe_ttm")
        valuation = valuation.model_copy(
            update={
                "score": round(max(15.0, min(90.0, 92 - pe * 1.35 + growth * 0.25)), 2)
            }
        )
        blocks = [business, fundamentals, management, technicals, valuation]
        confidence = research_confidence(blocks, source_count=len(sources))
        core = calculate_core_score(fundamentals, valuation, management, technicals)
        book_categories = [
            category.model_copy(
                update={
                    "rationale": _without_demo_words(category.rationale),
                    "sector_context": _without_demo_words(category.sector_context),
                }
            )
            for category in item.book_score.categories
        ]
        book_score = item.book_score.model_copy(update={"categories": book_categories})
        research = item.research.model_copy(
            update={
                "sources": sources,
                "business": business,
                "fundamentals": fundamentals,
                "management": management,
                "technicals": technicals,
                "valuation": valuation,
                "core_score": core,
                "confidence": confidence,
            }
        )
        rebuilt.append(
            item.model_copy(
                update={
                    "research": research,
                    "book_score": book_score,
                    "final_score": calculate_final_score(core, book_score),
                    "rank": None,
                }
            )
        )

    ranked = rank_companies(rebuilt)
    reports = {item.research.company.ticker: item.research for item in ranked}
    books = {item.book_score.company.ticker: item.book_score for item in ranked}
    audit = audit_results(reports, books, retry_count=0, mode="live")
    conclusion = _conclusion_for(mode="live", audit=audit, ranked=ranked)
    briefing = sample.model_copy(
        update={
            "mode": "live",
            "companies": ranked,
            "audit": audit,
            "conclusion": conclusion,
            "markdown": "",
        }
    )
    return briefing.model_copy(update={"markdown": render_markdown(briefing)})


def _replace_first(briefing: FinalBriefing, company) -> FinalBriefing:
    return briefing.model_copy(update={"companies": [company, *briefing.companies[1:]]})


def test_demo_and_clean_live_fixtures_pass() -> None:
    demo = FinalBriefing.model_validate_json(
        (PROJECT_ROOT / "examples" / "sample_briefing.json").read_text(encoding="utf-8")
    )

    assert scan_for_publication(demo, tuple(_trace())).passed
    assert scan_for_publication(_live_briefing(), tuple(_trace())).passed


def test_live_report_rejects_illustrative_metrics_and_demo_sources() -> None:
    briefing = _live_briefing()
    first = briefing.companies[0]
    metric = first.research.technicals.metrics[0].model_copy(
        update={"measurement_type": "illustrative"}
    )
    technicals = first.research.technicals.model_copy(
        update={"metrics": [metric, *first.research.technicals.metrics[1:]]}
    )
    source = first.research.sources[0].model_copy(
        update={"source_type": "demo_reference_only"}
    )
    research = first.research.model_copy(
        update={
            "technicals": technicals,
            "sources": [source, *first.research.sources[1:]],
        }
    )

    assert "LIVE_ILLUSTRATIVE_DATA" in _codes(
        _replace_first(briefing, first.model_copy(update={"research": research}))
    )


@pytest.mark.parametrize(
    "field_name",
    ["business", "fundamentals", "management", "technicals", "valuation"],
)
def test_every_logical_block_identity_is_checked(field_name: str) -> None:
    briefing = _live_briefing()
    first = briefing.companies[0]
    block = getattr(first.research, field_name).model_copy(update={"agent": "Wrong Agent"})
    research = first.research.model_copy(update={field_name: block})
    codes = _codes(_replace_first(briefing, first.model_copy(update={"research": research})))

    assert "BLOCK_AGENT_MISMATCH" in codes


def test_confidence_check_covers_the_first_block_not_only_the_last() -> None:
    briefing = _live_briefing()
    first = briefing.companies[0]
    business = first.research.business.model_copy(update={"confidence": 0.1})
    research = first.research.model_copy(update={"business": business})
    codes = _codes(_replace_first(briefing, first.model_copy(update={"research": research})))

    assert "BLOCK_LOW_CONFIDENCE" in codes


def test_duplicate_metric_cannot_hide_a_stale_value() -> None:
    briefing = _live_briefing()
    first = briefing.companies[0]
    roe = next(metric for metric in first.research.fundamentals.metrics if metric.code == "roe")
    stale_roe = roe.model_copy(update={"as_of_date": briefing.as_of_date - timedelta(days=900)})
    fundamentals = first.research.fundamentals.model_copy(
        update={"metrics": [stale_roe, *first.research.fundamentals.metrics]}
    )
    research = first.research.model_copy(update={"fundamentals": fundamentals})
    codes = _codes(_replace_first(briefing, first.model_copy(update={"research": research})))

    assert "DUPLICATE_METRIC_CODE" in codes
    assert "FUNDAMENTAL_STALE" in codes


@pytest.mark.parametrize(
    ("field_name", "metric_code", "day_delta", "expected_code"),
    [
        ("management", "credit_rating", -900, "MANAGEMENT_NOT_CURRENT"),
        ("fundamentals", "roe", 1, "FUNDAMENTAL_STALE"),
        ("valuation", "pe_ttm", -500, "VALUATION_NOT_CURRENT"),
        ("valuation", "peg", 1, "VALUATION_NOT_CURRENT"),
        ("valuation", "price_sales", -500, "VALUATION_NOT_CURRENT"),
        ("valuation", "gsec_10y_yield", 1, "VALUATION_NOT_CURRENT"),
        ("technicals", "close", -8, "TECHNICALS_NOT_CURRENT"),
        ("technicals", "sma20", 1, "TECHNICALS_NOT_CURRENT"),
        ("technicals", "sma50", -8, "TECHNICALS_NOT_CURRENT"),
        ("technicals", "rsi14", 1, "TECHNICALS_NOT_CURRENT"),
    ],
)
def test_every_scored_metric_must_be_current(
    field_name: str,
    metric_code: str,
    day_delta: int,
    expected_code: str,
) -> None:
    briefing = _live_briefing()
    first = briefing.companies[0]
    block = getattr(first.research, field_name)
    metrics = [
        metric.model_copy(
            update={"as_of_date": briefing.as_of_date + timedelta(days=day_delta)}
        )
        if metric.code == metric_code
        else metric
        for metric in block.metrics
    ]
    research = first.research.model_copy(
        update={field_name: block.model_copy(update={"metrics": metrics})}
    )

    assert expected_code in _codes(
        _replace_first(briefing, first.model_copy(update={"research": research}))
    )


def test_all_deterministic_component_scores_are_recomputed() -> None:
    briefing = _live_briefing()
    first = briefing.companies[0]
    fundamentals = first.research.fundamentals.model_copy(
        update={"score": 1.0, "growth_score": 1.0}
    )
    valuation = first.research.valuation.model_copy(update={"score": 1.0})
    technicals = first.research.technicals.model_copy(update={"score": 1.0})
    research = first.research.model_copy(
        update={
            "fundamentals": fundamentals,
            "valuation": valuation,
            "technicals": technicals,
        }
    )
    codes = _codes(_replace_first(briefing, first.model_copy(update={"research": research})))

    assert {
        "FUNDAMENTALS_SCORE_MISMATCH",
        "GROWTH_SCORE_MISMATCH",
        "VALUATION_SCORE_MISMATCH",
        "TECHNICAL_SCORE_MISMATCH",
    }.issubset(codes)


def test_nonnumeric_value_cannot_keep_a_cached_numeric_score() -> None:
    briefing = _live_briefing()
    first = briefing.companies[0]
    close = first.research.technicals.metrics[0].model_copy(update={"value": "unknown"})
    technicals = first.research.technicals.model_copy(
        update={"metrics": [close, *first.research.technicals.metrics[1:]]}
    )
    research = first.research.model_copy(update={"technicals": technicals})

    assert "NUMERIC_METRIC_INVALID" in _codes(
        _replace_first(briefing, first.model_copy(update={"research": research}))
    )


def test_research_confidence_is_recomputed_before_ranking() -> None:
    briefing = _live_briefing()
    first = briefing.companies[0]
    research = first.research.model_copy(update={"confidence": 0.01})

    assert "RESEARCH_CONFIDENCE_MISMATCH" in _codes(
        _replace_first(briefing, first.model_copy(update={"research": research}))
    )


def test_book_hash_and_policy_version_are_fixed_across_companies() -> None:
    briefing = _live_briefing()
    first = briefing.companies[0]
    book = first.book_score.model_copy(
        update={"document_hash": "different-book", "score_version": "other-policy"}
    )
    codes = _codes(_replace_first(briefing, first.model_copy(update={"book_score": book})))

    assert "BOOK_DOCUMENT_HASH_MISMATCH" in codes
    assert "BOOK_SCORE_VERSION_MISMATCH" in codes

    blank = first.book_score.model_copy(update={"document_hash": " "})
    assert "BOOK_DOCUMENT_HASH_MISSING" in _codes(
        _replace_first(briefing, first.model_copy(update={"book_score": blank}))
    )


def test_live_scored_blocks_cannot_cite_an_empty_placeholder() -> None:
    briefing = _live_briefing()
    first = briefing.companies[0]
    source = first.research.sources[0].model_copy(
        update={"excerpt": "", "source_type": "manual_reference"}
    )
    research = first.research.model_copy(
        update={"sources": [source, *first.research.sources[1:]]}
    )

    assert "LIVE_CITATION_NOT_USABLE" in _codes(
        _replace_first(briefing, first.model_copy(update={"research": research}))
    )


def test_cli_rejects_before_creating_any_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    briefing = FinalBriefing.model_validate_json(
        (PROJECT_ROOT / "examples" / "sample_briefing.json").read_text(encoding="utf-8")
    )
    output = tmp_path / "not-created" / "briefing.md"
    monkeypatch.setattr(cli_module, "run_research", lambda _settings: (briefing, _trace(warning=True)))
    monkeypatch.setattr(
        sys,
        "argv",
        ["competitive-scoring", "run", "--mode", "demo", "--output", str(output)],
    )

    with pytest.raises(SystemExit, match="2"):
        cli_module.main()

    assert not output.exists()
    assert not output.with_suffix(".json").exists()
    assert not output.parent.exists()


def test_cli_still_writes_a_valid_demo_report(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Adding the final gate must not break the project's offline demo path."""

    briefing = FinalBriefing.model_validate_json(
        (PROJECT_ROOT / "examples" / "sample_briefing.json").read_text(encoding="utf-8")
    )
    output = tmp_path / "demo" / "briefing.md"
    monkeypatch.setattr(cli_module, "run_research", lambda _settings: (briefing, _trace()))
    monkeypatch.setattr(
        sys,
        "argv",
        ["competitive-scoring", "run", "--mode", "demo", "--output", str(output)],
    )

    cli_module.main()

    assert output.read_text(encoding="utf-8") == briefing.markdown
    saved = FinalBriefing.model_validate_json(
        output.with_suffix(".json").read_text(encoding="utf-8")
    )
    assert saved == briefing
