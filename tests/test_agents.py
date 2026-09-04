"""Focused tests for evidence translation and manual-reference guardrails."""

import json
from datetime import UTC, date, datetime
from types import MappingProxyType

import pytest

from competitive_scoring.agents import (
    RuntimeServices,
    _add_primary_metric_corroboration,
    _evidence_as_prompt,
    _evidence_for_agent,
    _extract_current_fy_growth_pair,
    _extract_pdf_half_year_cash_pat,
    _finance_keyword_excerpt,
    _free_record_to_evidence,
    _is_usable_scoring_evidence,
    analyze_business,
    analyze_fundamentals,
    analyze_management,
    analyze_valuation,
    extract_company_dossier,
    gather_sources,
)
from competitive_scoring.config import Settings
from competitive_scoring.llm import StructuredOutputError
from competitive_scoring.models import (
    AnalysisBlock,
    CitedClaim,
    CompanyDossierExtraction,
    EvidenceItem,
    FundamentalObservation,
    FundamentalsExtraction,
    Metric,
    QualitativeSlice,
    ValuationObservation,
)
from competitive_scoring.tools.free_sources import FreeSourceCollection, FreeSourceRecord
from competitive_scoring.tools.you_search import (
    SearchContents,
    SourceTrace,
    YouSearchResponse,
    YouSearchResult,
)


def record(*, retrieved_at: str, source_type: str = "official_page") -> FreeSourceRecord:
    return FreeSourceRecord(
        ticker="PNGJL",
        source_type=source_type,  # type: ignore[arg-type]
        title="PNGJL filing",
        url="https://www.nseindia.com/example",
        excerpt="Important extracted passage" if source_type == "official_page" else "",
        publisher="NSE India",
        retrieved_at=retrieved_at,
        metadata=MappingProxyType(
            {
                "authority": "primary_regulatory",
                "tags": ("official", "filing"),
                "published_at": "2026-09-02",
            }
        ),
    )


def observation(
    code: str,
    value: float,
    unit: str,
    *,
    source_id: str = "official",
    period_type: str = "FY",
    as_of_date: date = date(2026, 3, 31),
    accounting_basis: str = "consolidated",
) -> FundamentalObservation:
    """Build one compact observation for deterministic-agent tests."""

    return FundamentalObservation(
        code=code,  # type: ignore[arg-type]
        value=value,
        unit=unit,  # type: ignore[arg-type]
        as_of_date=as_of_date,
        period_type=period_type,  # type: ignore[arg-type]
        accounting_basis=accounting_basis,  # type: ignore[arg-type]
        source_id=source_id,
    )


def evidence_item(source_id: str, excerpt: str) -> EvidenceItem:
    """Build one finance-bearing primary source for live-agent tests."""

    return EvidenceItem(
        source_id=source_id,
        company_ticker="PNGJL",
        title=f"Filing {source_id}",
        url=f"https://www.nseindia.com/{source_id}",
        source_type="official_page",
        published_at="2026-04-01",
        accessed_at=datetime(2026, 9, 4, tzinfo=UTC),
        excerpt=excerpt,
        tags=["official", "filing"],
        authority="primary_regulatory",
    )


def test_primary_value_corroboration_keeps_both_exact_sources() -> None:
    secondary = evidence_item("secondary", "Interest Coverage Ratio 4.34").model_copy(
        update={"authority": "secondary", "url": "https://example.com/ratios"}
    )
    primary = evidence_item(
        "exchange-fy26",
        "FY26 debt profile improved, with the Interest Coverage Ratio at 4.3.",
    )
    metric = Metric(
        code="interest_coverage",
        label="Interest coverage",
        value=4.34,
        unit="x",
        as_of_date=date(2026, 3, 31),
        period_type="FY",
        accounting_basis="consolidated",
        source_ids=["secondary"],
    )

    result = _add_primary_metric_corroboration(metric, [secondary, primary])

    assert result.value == 4.34
    assert result.source_ids == ["secondary", "exchange-fy26"]


def test_ocr_spaced_pdf_rows_produce_only_a_same_half_year_cash_pat_pair() -> None:
    source = evidence_item(
        "half-year-filing",
        (
            "[PDF page 11] Consol idated Cashflow Statement for the h al f year ended "
            "Septem ber 30, 2025 (INR Million) Net ca s h inflow / (outflow) from "
            "operating activities (1,056.23) (2,824.31) "
            "[PDF page 9] C onso lidated Financial Results for the h al f year ended "
            "Septem ber 30, 2025 Revenue from o pera t ions 21,776.22 17,145.62 "
            "T ax Ex p e n se 298.41 233.83 P I\"ofi t / (L oss) for the period, "
            "net oft:l x from continuing operations 793 .1 2 693.42 349.19 "
            "1,48 6 .54 702.40 2,182.69 Other comprehensive income"
        ),
    ).model_copy(
        update={
            "source_type": "official_document",
            "title": "Half year ended September 30 2025.pdf",
        }
    )

    result = _extract_pdf_half_year_cash_pat(
        [source],
        research_as_of=date(2026, 9, 4),
    )

    assert [(item.code, item.value) for item in result] == [
        ("operating_cash_flow", -1056.23),
        ("pat", 1486.54),
    ]
    assert all(item.period_type == "half_year" for item in result)
    assert all(item.accounting_basis == "consolidated" for item in result)
    assert all(item.as_of_date == date(2025, 9, 30) for item in result)


def test_explicit_full_year_consolidated_growth_pair_is_copied_deterministically() -> None:
    source = EvidenceItem(
        source_id="fy26-growth",
        company_ticker="PNGJL",
        title="PNGJL FY26 full-year consolidated results",
        url="https://example.com/pngjl-fy26-results",
        source_type="you_web",
        published_at="2026-05-23",
        accessed_at=datetime(2026, 9, 4, tzinfo=UTC),
        excerpt=(
            "For the full year FY26, consolidated revenue rose 39.6% year on year. "
            "Profit After Tax (PAT) grew 87.8% YoY. Q4 revenue growth was 123%."
        ),
        tags=["you_primary", "growth"],
        authority="secondary",
    )

    result = _extract_current_fy_growth_pair(
        [source],
        research_as_of=date(2026, 9, 4),
    )

    assert [(item.code, item.value) for item in result] == [
        ("revenue_growth", 39.6),
        ("profit_growth", 87.8),
    ]
    assert all(item.as_of_date == date(2026, 3, 31) for item in result)
    assert all(item.period_type == "FY" for item in result)
    assert all(item.accounting_basis == "consolidated" for item in result)


def test_free_source_id_is_stable_and_provenance_is_preserved() -> None:
    first = _free_record_to_evidence(record(retrieved_at="2026-09-03T10:00:00Z"))
    second = _free_record_to_evidence(record(retrieved_at="2026-09-04T11:00:00Z"))

    assert first.source_id == second.source_id
    assert first.authority == "primary_regulatory"
    assert first.published_at == "2026-09-02"
    assert first.excerpt == "Important extracted passage"
    assert first.tags == ["official", "filing"]


def test_manual_link_is_visible_but_not_passed_to_the_llm() -> None:
    manual = _free_record_to_evidence(
        record(
            retrieved_at="2026-09-04T11:00:00Z",
            source_type="manual_reference_moneycontrol",
        )
    )

    assert _is_usable_scoring_evidence(manual) is False
    assert _evidence_as_prompt([manual]) == "[]"


def test_specialists_receive_relevant_search_results_and_all_primary_sources() -> None:
    def evidence(
        source_id: str,
        *,
        tags: list[str],
        authority: str = "secondary",
    ) -> EvidenceItem:
        return EvidenceItem(
            source_id=source_id,
            company_ticker="SENCO",
            title=source_id,
            url=f"https://example.com/{source_id}",
            source_type="you_web" if "you_primary" in tags else "official_page",
            accessed_at=datetime(2026, 9, 4, tzinfo=UTC),
            excerpt=f"Evidence for {source_id}",
            tags=tags,
            authority=authority,  # type: ignore[arg-type]
        )

    fundamentals = evidence("fundamentals", tags=["you_primary", "fundamentals"])
    unrelated_news = evidence("news", tags=["you_primary", "news"])
    official = evidence(
        "official",
        tags=["official", "filing"],
        authority="primary_regulatory",
    )

    selected = _evidence_for_agent([fundamentals, unrelated_news, official], "Fundamentals Agent")
    assert [item.source_id for item in selected] == ["fundamentals", "official"]

    # Serialization prioritizes the official filing even though it arrived last.
    prompt_rows = json.loads(_evidence_as_prompt(selected))
    assert [row["source_id"] for row in prompt_rows] == ["official", "fundamentals"]


def test_primary_you_evidence_is_available_to_every_specialist() -> None:
    primary_other_category = EvidenceItem(
        source_id="bse-filing",
        company_ticker="KALYANKJIL",
        title="BSE filing",
        url="https://www.bseindia.com/filing.pdf",
        source_type="you_web",
        accessed_at=datetime(2026, 9, 4, tzinfo=UTC),
        excerpt="Credit rating A+ and governance evidence.",
        tags=["you_primary", "fundamentals"],
        authority="primary_regulatory",
    )
    secondary_other_category = primary_other_category.model_copy(
        update={"source_id": "secondary", "authority": "secondary"}
    )

    selected = _evidence_for_agent(
        [primary_other_category, secondary_other_category], "Management Agent"
    )

    assert [item.source_id for item in selected] == ["bse-filing"]


def test_duplicate_you_url_merges_all_query_categories_and_highlights() -> None:
    """The same filing found by two searches must retain both reasons it was selected."""

    calls: list[str] = []

    class DuplicateURLYou:
        def search(self, query: str, **_kwargs: object) -> YouSearchResponse:
            calls.append(query)
            trace = SourceTrace(
                provider="you.com",
                endpoint="https://ydc-index.io/v1/search",
                query=query,
                search_uuid=f"search-{len(calls)}",
                latency_seconds=0.01,
                retrieved_at="2026-09-04T10:00:00+00:00",
            )
            result = YouSearchResult(
                source_type="web",
                rank=1,
                url="https://www.pngjewellers.com/files/shared-filing.pdf",
                title="Shared official filing",
                description=None,
                snippets=(),
                page_age="2026-08-31",
                thumbnail_url=None,
                favicon_url=None,
                contents=SearchContents(highlights=(f"Distinct highlight {len(calls)}",)),
                trace=trace,
            )
            return YouSearchResponse(web=(result,), news=(), trace=trace)

    class EmptyCollector:
        def collect(self, *_args: object, **_kwargs: object) -> FreeSourceCollection:
            return FreeSourceCollection(records=(), warnings=())

    runtime = RuntimeServices(
        settings=Settings(mode="live", research_as_of=date(2026, 9, 4)),
        book_agent=object(),  # type: ignore[arg-type]
        you_search=DuplicateURLYou(),  # type: ignore[arg-type]
        source_collector=EmptyCollector(),  # type: ignore[arg-type]
    )

    evidence = gather_sources(runtime, company=settings_target())

    assert len(calls) == 7
    assert len(evidence) == 1
    assert {
        "business",
        "fundamentals",
        "growth",
        "cash_quality",
        "management",
        "valuation",
        "news",
    }.issubset(evidence[0].tags)
    assert all(f"Distinct highlight {number}" in evidence[0].excerpt for number in range(1, 8))
    # The first hit remains the stable provenance record while later searches
    # enrich its tags and excerpt.
    assert evidence[0].search_query == calls[0]


def test_model_formatting_failure_becomes_explicit_unrankable_block() -> None:
    class FailingLLM:
        def generate(self, **kwargs: object):
            assert "Return metrics=[]" in str(kwargs["instructions"])
            raise StructuredOutputError(
                provider="nebius",
                model="moonshotai/Kimi-K3",
                schema_name="AnalysisBlock",
                context="PNGJL — Business Agent",
                attempts=3,
                feedback="Invalid JSON at line 1.",
            )

    runtime = RuntimeServices(
        settings=Settings(mode="live"),
        book_agent=object(),  # type: ignore[arg-type]
        llm=FailingLLM(),  # type: ignore[arg-type]
    )

    block = analyze_business(runtime, settings_target(), [])

    assert block.status == "insufficient_evidence"
    assert block.score is None
    assert block.metrics == []
    assert block.confidence == 0
    assert "No score or financial value was substituted" in block.summary
    assert "Invalid JSON" in block.risks[0]
    diagnostics = runtime.diagnostics_snapshot()
    assert len(diagnostics) == 1
    assert diagnostics[0].code == "QUALITATIVE_MODEL_FAILURE"
    assert "Kimi-K3" in diagnostics[0].message


def test_live_fundamentals_prompt_is_small_and_requests_only_essential_metrics() -> None:
    captured: dict[str, object] = {}

    class RecordingLLM:
        def generate(self, **kwargs: object) -> FundamentalsExtraction:
            captured.update(kwargs)
            return FundamentalsExtraction(
                observations=[
                    observation("roe", 18, "percent"),
                    observation("roce", 20, "percent"),
                    observation("revenue_growth", 15, "percent", period_type="multi_year"),
                    observation("profit_growth", 20, "percent", period_type="multi_year"),
                    observation("debt_equity", 0.5, "ratio"),
                    observation("interest_coverage", 5, "ratio"),
                    observation("cfo_pat", 0.9, "ratio"),
                ]
            )

    source = EvidenceItem(
        source_id="official",
        company_ticker="PNGJL",
        title="Annual report",
        url="https://www.nseindia.com/annual-report",
        source_type="official_page",
        accessed_at=datetime(2026, 9, 4, tzinfo=UTC),
        excerpt=(
            "IRRELEVANT-BEGIN "
            + "X" * 5_000
            + " ROE 18%, ROCE 20%, revenue growth 15%, PAT growth 20%, "
            "debt/equity 0.5x, interest coverage 5x, and CFO/PAT 0.9x for FY 2026."
        ),
        tags=["official", "filing"],
        authority="primary_regulatory",
    )
    runtime = RuntimeServices(
        settings=Settings(mode="live"),
        book_agent=object(),  # type: ignore[arg-type]
        llm=RecordingLLM(),  # type: ignore[arg-type]
    )

    block = analyze_fundamentals(runtime, settings_target(), [source])

    assert block.status == "complete"
    assert block.score == 84.25
    assert block.growth_score == 72.5
    assert len(block.metrics) == 7
    assert captured["schema"] is FundamentalsExtraction
    instructions = str(captured["instructions"])
    assert "financial fact extractor, not an analyst" in instructions
    assert "do not calculate, estimate, annualize, infer" in instructions
    assert "operating_cash_flow and pat" in instructions

    prompt = str(captured["prompt"])
    evidence = json.loads(prompt.split("Evidence JSON: ", 1)[1])
    assert [row["source_id"] for row in evidence] == ["official"]
    assert len(json.dumps(evidence)) < 16_000
    assert len(evidence[0]["excerpt"]) <= 3_500
    assert "IRRELEVANT-BEGIN" not in evidence[0]["excerpt"]


def test_live_fundamentals_derives_cfo_pat_only_from_matching_cited_inputs() -> None:
    class DerivationLLM:
        def generate(self, **_kwargs: object) -> FundamentalsExtraction:
            return FundamentalsExtraction(
                observations=[
                    observation("roe", 18, "percent", source_id="cash-source"),
                    observation("roce", 20, "percent", source_id="cash-source"),
                    observation(
                        "revenue_growth",
                        15,
                        "percent",
                        source_id="cash-source",
                        period_type="multi_year",
                    ),
                    observation(
                        "profit_growth",
                        20,
                        "percent",
                        source_id="cash-source",
                        period_type="multi_year",
                    ),
                    observation("debt_equity", 0.5, "ratio", source_id="cash-source"),
                    observation("current_ratio", 1.5, "ratio", source_id="cash-source"),
                    observation(
                        "operating_cash_flow",
                        120,
                        "INR_crore",
                        source_id="cash-source",
                    ),
                    observation("pat", 100, "INR_crore", source_id="profit-source"),
                ]
            )

    sources = [
        evidence_item(
            "cash-source",
            "ROE ROCE revenue growth debt/equity current ratio operating cash flow 120 crore",
        ),
        evidence_item("profit-source", "PAT profit after tax 100 crore"),
    ]
    runtime = RuntimeServices(
        settings=Settings(mode="live"),
        book_agent=object(),  # type: ignore[arg-type]
        llm=DerivationLLM(),  # type: ignore[arg-type]
    )

    block = analyze_fundamentals(runtime, settings_target(), sources)

    assert block.status == "complete"
    conversion = next(metric for metric in block.metrics if metric.code == "cfo_pat")
    assert conversion.value == 1.2
    assert conversion.measurement_type == "derived"
    assert conversion.formula == "operating_cash_flow / pat"
    assert conversion.input_metric_codes == ["operating_cash_flow", "pat"]
    assert conversion.source_ids == ["cash-source", "profit-source"]
    assert all(metric.code not in {"operating_cash_flow", "pat"} for metric in block.metrics)


def test_live_fundamentals_uses_one_targeted_gap_repair() -> None:
    calls: list[str] = []

    class GapRepairLLM:
        def generate(self, **kwargs: object) -> FundamentalsExtraction:
            calls.append(str(kwargs["context"]))
            if len(calls) == 1:
                return FundamentalsExtraction(
                    observations=[
                        observation("roe", 18, "percent"),
                        observation("revenue_growth", 15, "percent", period_type="multi_year"),
                        observation("profit_growth", 20, "percent", period_type="multi_year"),
                        observation("interest_coverage", 5, "ratio"),
                    ]
                )
            return FundamentalsExtraction(
                observations=[
                    observation("roce", 20, "percent"),
                    observation("net_debt_equity", 0.5, "ratio"),
                    observation("cfo_pat", 1.1, "ratio", period_type="multi_year"),
                    # A repair response is not allowed to overwrite a code that
                    # was not requested in this second pass.
                    observation("roe", 99, "percent"),
                ]
            )

    source = evidence_item(
        "official",
        "ROE 18%, ROCE 20%, revenue growth 15%, PAT growth 20%, net debt to equity "
        "0.5x, interest coverage 5x, CFO/PAT 1.1x.",
    )
    runtime = RuntimeServices(
        settings=Settings(mode="live"),
        book_agent=object(),  # type: ignore[arg-type]
        llm=GapRepairLLM(),  # type: ignore[arg-type]
    )

    block = analyze_fundamentals(runtime, settings_target(), [source])

    assert block.status == "complete"
    assert len(calls) == 2
    assert calls[1].endswith("gap repair")
    assert next(metric.value for metric in block.metrics if metric.code == "roe") == 18
    assert "net_debt_equity" in {metric.code for metric in block.metrics}


def test_live_fundamentals_rejects_facts_stale_since_2019() -> None:
    """A recently retrieved page must not make an old accounting period current."""

    stale = [
        observation("roe", 18, "percent", as_of_date=date(2019, 3, 31)),
        observation("roce", 20, "percent", as_of_date=date(2019, 3, 31)),
        observation(
            "revenue_growth",
            15,
            "percent",
            period_type="FY",
            as_of_date=date(2019, 3, 31),
        ),
        observation(
            "profit_growth",
            20,
            "percent",
            period_type="FY",
            as_of_date=date(2019, 3, 31),
        ),
        observation("debt_equity", 0.5, "ratio", as_of_date=date(2019, 3, 31)),
        observation("interest_coverage", 5, "ratio", as_of_date=date(2019, 3, 31)),
        observation("cfo_pat", 1.0, "ratio", as_of_date=date(2019, 3, 31)),
    ]

    class StaleLLM:
        def generate(self, **_kwargs: object) -> FundamentalsExtraction:
            return FundamentalsExtraction(observations=stale)

    source = evidence_item(
        "official",
        "ROE 18 ROCE 20 revenue growth 15 profit growth 20 debt/equity 0.5 "
        "interest coverage 5 and CFO/PAT 1.0 for FY2019.",
    )
    runtime = RuntimeServices(
        settings=Settings(mode="live", research_as_of=date(2026, 9, 4)),
        book_agent=object(),  # type: ignore[arg-type]
        llm=StaleLLM(),  # type: ignore[arg-type]
    )

    block = analyze_fundamentals(runtime, settings_target(), [source])

    assert block.status == "insufficient_evidence"
    assert block.score is None
    assert block.growth_score is None
    assert block.metrics == []


def test_live_fundamentals_does_not_score_incompatible_growth_periods() -> None:
    """Quarterly revenue growth and TTM profit growth are not one comparable pair."""

    incompatible = FundamentalsExtraction(
        observations=[
            observation("roe", 18, "percent"),
            observation("roce", 20, "percent"),
            observation(
                "revenue_growth",
                15,
                "percent",
                period_type="quarter",
                as_of_date=date(2026, 6, 30),
            ),
            observation(
                "profit_growth",
                20,
                "percent",
                period_type="TTM",
                as_of_date=date(2026, 6, 30),
            ),
            observation("debt_equity", 0.5, "ratio"),
            observation("interest_coverage", 5, "ratio"),
            observation("cfo_pat", 1.0, "ratio", period_type="multi_year"),
        ]
    )

    class IncompatibleGrowthLLM:
        def generate(self, **_kwargs: object) -> FundamentalsExtraction:
            return incompatible

    source = evidence_item(
        "official",
        "ROE 18 ROCE 20 quarter revenue growth 15 TTM profit growth 20 "
        "debt/equity 0.5 interest coverage 5 and CFO/PAT 1.0.",
    )
    runtime = RuntimeServices(
        settings=Settings(mode="live", research_as_of=date(2026, 9, 4)),
        book_agent=object(),  # type: ignore[arg-type]
        llm=IncompatibleGrowthLLM(),  # type: ignore[arg-type]
    )

    block = analyze_fundamentals(runtime, settings_target(), [source])

    assert block.status == "insufficient_evidence"
    assert block.score is None
    assert block.growth_score is None
    assert "comparable revenue and PAT growth" in block.risks[0]


def test_live_fundamentals_selects_available_compatible_growth_pair() -> None:
    """When duplicates exist, use a matched pair instead of two individually newer values."""

    class CompatibleGrowthLLM:
        def generate(self, **_kwargs: object) -> FundamentalsExtraction:
            return FundamentalsExtraction(
                observations=[
                    observation("roe", 18, "percent"),
                    observation("roce", 20, "percent"),
                    # These two recent facts are intentionally incompatible.
                    observation(
                        "revenue_growth",
                        30,
                        "percent",
                        period_type="quarter",
                        as_of_date=date(2026, 6, 30),
                    ),
                    observation(
                        "profit_growth",
                        35,
                        "percent",
                        period_type="TTM",
                        as_of_date=date(2026, 6, 30),
                    ),
                    # This FY pair is older but comparable and therefore scoreable.
                    observation(
                        "revenue_growth",
                        18,
                        "percent",
                        period_type="FY",
                        as_of_date=date(2026, 3, 31),
                    ),
                    observation(
                        "profit_growth",
                        21,
                        "percent",
                        period_type="FY",
                        as_of_date=date(2026, 3, 31),
                    ),
                    observation("debt_equity", 0.5, "ratio"),
                    observation("interest_coverage", 5, "ratio"),
                    observation("cfo_pat", 1.0, "ratio", period_type="multi_year"),
                ]
            )

    source = evidence_item(
        "official",
        "ROE 18 ROCE 20 revenue growth and profit growth for quarter, TTM and FY; "
        "debt/equity 0.5 interest coverage 5 CFO/PAT 1.0.",
    )
    runtime = RuntimeServices(
        settings=Settings(mode="live", research_as_of=date(2026, 9, 4)),
        book_agent=object(),  # type: ignore[arg-type]
        llm=CompatibleGrowthLLM(),  # type: ignore[arg-type]
    )

    block = analyze_fundamentals(runtime, settings_target(), [source])

    metrics = {metric.code: metric for metric in block.metrics}
    assert block.status == "complete"
    assert metrics["revenue_growth"].value == 18
    assert metrics["profit_growth"].value == 21
    assert metrics["revenue_growth"].period_type == "FY"
    assert metrics["profit_growth"].period_type == "FY"
    assert metrics["revenue_growth"].as_of_date == date(2026, 3, 31)
    assert metrics["profit_growth"].as_of_date == date(2026, 3, 31)


def test_valid_gap_repairs_survive_twenty_invalid_initial_observations() -> None:
    """A full malformed first payload must not slice valid repair facts out of the merge."""

    calls = 0

    class FullInvalidThenValidLLM:
        def generate(self, **_kwargs: object) -> FundamentalsExtraction:
            nonlocal calls
            calls += 1
            if calls == 1:
                return FundamentalsExtraction(
                    observations=[
                        observation(
                            "roe",
                            10 + number,
                            "percent",
                            source_id=f"unknown-source-{number}",
                        )
                        for number in range(20)
                    ]
                )
            return FundamentalsExtraction(
                observations=[
                    observation("roe", 18, "percent"),
                    observation("roce", 20, "percent"),
                    observation("revenue_growth", 15, "percent", period_type="FY"),
                    observation("profit_growth", 20, "percent", period_type="FY"),
                    observation("debt_equity", 0.5, "ratio"),
                    observation("interest_coverage", 5, "ratio"),
                    observation("cfo_pat", 1.0, "ratio", period_type="multi_year"),
                ]
            )

    source = evidence_item(
        "official",
        "ROE 18 ROCE 20 FY revenue growth 15 FY profit growth 20 debt/equity 0.5 "
        "interest coverage 5 and CFO/PAT 1.0.",
    )
    runtime = RuntimeServices(
        settings=Settings(mode="live", research_as_of=date(2026, 9, 4)),
        book_agent=object(),  # type: ignore[arg-type]
        llm=FullInvalidThenValidLLM(),  # type: ignore[arg-type]
    )

    block = analyze_fundamentals(runtime, settings_target(), [source])

    assert calls == 2
    assert block.status == "complete"
    assert block.score is not None
    assert {metric.code for metric in block.metrics} == {
        "cfo_pat",
        "debt_equity",
        "interest_coverage",
        "profit_growth",
        "revenue_growth",
        "roce",
        "roe",
    }


def test_cumulative_cfo_pat_matches_periods_and_normalizes_inr_scale() -> None:
    class MultiYearLLM:
        def generate(self, **_kwargs: object) -> FundamentalsExtraction:
            return FundamentalsExtraction(
                observations=[
                    observation("roe", 18, "percent", source_id="cash"),
                    observation("roce", 20, "percent", source_id="cash"),
                    observation(
                        "revenue_growth", 15, "percent", source_id="cash", period_type="multi_year"
                    ),
                    observation(
                        "profit_growth", 20, "percent", source_id="cash", period_type="multi_year"
                    ),
                    observation("net_debt_equity", 0.5, "ratio", source_id="cash"),
                    observation("interest_coverage", 5, "ratio", source_id="cash"),
                    observation(
                        "operating_cash_flow",
                        1322,
                        "INR_crore",
                        source_id="cash",
                        as_of_date=date(2024, 3, 31),
                    ),
                    observation(
                        "operating_cash_flow",
                        1209,
                        "INR_crore",
                        source_id="cash",
                        as_of_date=date(2025, 3, 31),
                    ),
                    observation(
                        "pat",
                        5963,
                        "INR_million",
                        source_id="profit",
                        as_of_date=date(2024, 3, 31),
                    ),
                    observation(
                        "pat",
                        7142,
                        "INR_million",
                        source_id="profit",
                        as_of_date=date(2025, 3, 31),
                    ),
                ]
            )

    sources = [
        evidence_item(
            "cash",
            "Consolidated cash from operating activity was 1,322 in FY24 and 1,209 in FY25.",
        ),
        evidence_item(
            "profit",
            "Consolidated profit after tax was 5,963 million in FY24 and 7,142 million in FY25.",
        ),
    ]
    runtime = RuntimeServices(
        settings=Settings(mode="live"),
        book_agent=object(),  # type: ignore[arg-type]
        llm=MultiYearLLM(),  # type: ignore[arg-type]
    )

    block = analyze_fundamentals(runtime, settings_target(), sources)

    conversion = next(metric for metric in block.metrics if metric.code == "cfo_pat")
    assert block.status == "complete"
    assert conversion.value == pytest.approx(1.9313)
    assert conversion.period_type == "multi_year"
    assert conversion.formula == "sum(operating_cash_flow) / sum(pat)"
    assert conversion.source_ids == ["cash", "profit"]


def test_cash_from_operating_activity_survives_finance_windowing() -> None:
    excerpt = _finance_keyword_excerpt(
        "Consolidated Cash Flows (in INR crore) FY25 Cash from Operating Activity 1,209."
    )

    assert "Cash from Operating Activity" in excerpt


def test_plural_cash_flow_and_profit_for_period_survive_finance_windowing() -> None:
    excerpt = _finance_keyword_excerpt(
        "Consolidated net cash flows from operating activities were INR 209 crore. "
        "Profit for the period was INR 180 crore."
    )

    assert "cash flows from operating activities" in excerpt
    assert "Profit for the period" in excerpt


def test_finance_window_prefers_numeric_statement_over_contents_entry() -> None:
    excerpt = _finance_keyword_excerpt(
        "Contents: Statement of Cash Flows. "
        + "narrative " * 180
        + "[PDF page 88] Consolidated Statement of Cash Flows: net cash generated from "
        "operating activities INR 1,209 crore; profit for the year INR 714.2 crore.",
        max_chars=700,
    )

    assert "[PDF page 88]" in excerpt
    assert "1,209" in excerpt
    assert "714.2" in excerpt


def test_live_fundamentals_keeps_missing_or_bad_provenance_unrankable() -> None:
    class IncompleteLLM:
        def generate(self, **_kwargs: object) -> FundamentalsExtraction:
            return FundamentalsExtraction(
                observations=[
                    observation("roe", 18, "percent"),
                    observation("roce", 20, "percent", source_id="unknown-source"),
                    observation("revenue_growth", 15, "percent", period_type="multi_year"),
                ]
            )

    source = evidence_item("official", "ROE ROCE and revenue growth from FY 2026")
    runtime = RuntimeServices(
        settings=Settings(mode="live"),
        book_agent=object(),  # type: ignore[arg-type]
        llm=IncompleteLLM(),  # type: ignore[arg-type]
    )

    block = analyze_fundamentals(runtime, settings_target(), [source])

    assert block.status == "insufficient_evidence"
    assert block.score is None
    assert block.growth_score is None
    assert {metric.code for metric in block.metrics} == {"roe", "revenue_growth"}
    assert "Missing essential coverage" in block.risks[0]
    assert "Discarded 1 observation" in block.risks[1]


def test_demo_fundamentals_contract_is_unchanged() -> None:
    runtime = RuntimeServices(
        settings=Settings(mode="demo"),
        book_agent=object(),  # type: ignore[arg-type]
    )

    block = analyze_fundamentals(runtime, settings_target(), [])

    assert block.status == "complete"
    assert len(block.metrics) == 19
    assert block.score is not None
    assert block.growth_score is not None


def test_business_and_management_prompts_are_bounded_and_outputs_are_small() -> None:
    calls: list[dict[str, object]] = []

    class RecordingLLM:
        def generate(self, **kwargs: object) -> AnalysisBlock:
            calls.append(kwargs)
            agent = (
                "Business Agent"
                if "Business Agent" in str(kwargs["context"])
                else "Management Agent"
            )
            metrics = []
            if agent == "Management Agent":
                metrics = [
                    Metric(
                        code="credit_rating",
                        label="Credit rating",
                        value="ICRA A+ Stable",
                        as_of_date=date(2026, 8, 1),
                        period_type="point_in_time",
                        accounting_basis="not_applicable",
                        source_ids=["official"],
                    ),
                    Metric(code="promoter_holding", label="Promoter holding", value=60),
                ]
            return AnalysisBlock(
                agent=agent,
                score=70,
                summary="Evidence-backed test.",
                strengths=["one", "two", "three", "four", "five"],
                risks=[],
                metrics=metrics,
                source_ids=["official", "invented-id"],
                confidence=0.7,
                growth_score=80,
            )

    source = evidence_item(
        "official",
        "Business positioning, management governance and credit rating evidence. " + "X" * 20_000,
    )
    runtime = RuntimeServices(
        settings=Settings(mode="live"),
        book_agent=object(),  # type: ignore[arg-type]
        llm=RecordingLLM(),  # type: ignore[arg-type]
    )

    business = analyze_business(runtime, settings_target(), [source])
    management = analyze_management(runtime, settings_target(), [source])

    assert business.metrics == []
    assert business.growth_score is None
    assert len(business.strengths) == 4
    assert business.source_ids == ["official"]
    assert [metric.code for metric in management.metrics] == ["credit_rating"]
    assert management.growth_score is None
    assert management.source_ids == ["official"]
    assert all(len(str(call["prompt"])) < 12_600 for call in calls)
    assert "exact code credit_rating" in str(calls[1]["instructions"])


def test_one_shared_dossier_feeds_three_agents_and_deterministic_valuation() -> None:
    calls: list[dict[str, object]] = []

    class DossierLLM:
        def generate(self, **kwargs: object) -> CompanyDossierExtraction:
            calls.append(kwargs)
            cited = CitedClaim(text="Evidence-backed claim.", source_ids=["official"])
            qualitative = QualitativeSlice(
                score=72,
                summary="Evidence-backed assessment.",
                strengths=[cited],
                risks=[],
                source_ids=["official"],
                confidence=0.75,
            )
            return CompanyDossierExtraction(
                ticker="PNGJL",
                business=qualitative,
                management=qualitative.model_copy(update={"score": 70}),
                fundamentals=FundamentalsExtraction(
                    observations=[
                        observation("roe", 18, "percent"),
                        observation("roce", 20, "percent"),
                        observation("revenue_growth", 15, "percent", period_type="FY"),
                        observation("profit_growth", 20, "percent", period_type="FY"),
                        observation("debt_equity", 0.5, "ratio"),
                        observation("interest_coverage", 5, "ratio"),
                        observation("cfo_pat", 1.0, "ratio", period_type="multi_year"),
                    ]
                ),
                valuation=[
                    ValuationObservation(
                        code="pe_ttm",
                        value=25,
                        unit="ratio",
                        as_of_date=date(2026, 9, 1),
                        period_type="TTM",
                        accounting_basis="consolidated",
                        source_id="official",
                    )
                ],
            )

    source = evidence_item(
        "official",
        "Business management ROE ROCE FY revenue growth PAT growth debt/equity "
        "interest coverage CFO/PAT and TTM P/E 25.",
    )
    runtime = RuntimeServices(
        settings=Settings(mode="live", research_as_of=date(2026, 9, 4)),
        book_agent=object(),  # type: ignore[arg-type]
        llm=DossierLLM(),  # type: ignore[arg-type]
    )

    dossier = extract_company_dossier(runtime, settings_target(), [source])
    business = analyze_business(runtime, settings_target(), [source], dossier)
    fundamentals = analyze_fundamentals(runtime, settings_target(), [source], dossier)
    management = analyze_management(runtime, settings_target(), [source], dossier)
    technicals = AnalysisBlock(
        agent="Technicals Agent",
        score=60,
        summary="Local technicals.",
        source_ids=["official"],
        confidence=0.8,
    )
    valuation = analyze_valuation(
        runtime,
        settings_target(),
        [source],
        business,
        fundamentals,
        management,
        technicals,
        dossier,
    )

    assert len(calls) == 1
    assert calls[0]["schema"] is CompanyDossierExtraction
    assert calls[0]["max_tokens"] == 6_144
    assert business.status == management.status == fundamentals.status == "complete"
    assert valuation.status == "complete"
    assert valuation.metrics[0].code == "pe_ttm"
    assert valuation.score == pytest.approx(76.38)


def test_valuation_recovers_explicit_ttm_pe_omitted_by_shared_dossier() -> None:
    """A quote-card fact must survive a harmless Kimi extraction omission."""

    source = EvidenceItem(
        source_id="thangamayil-yahoo",
        company_ticker="THANGAMAYL",
        title="Thangamayil Jewellery Limited stock price",
        url="https://finance.yahoo.com/quote/THANGAMAYL.NS/",
        source_type="web_search",
        published_at="2026-07-29T06:45:03",
        accessed_at=datetime(2026, 9, 4, tzinfo=UTC),
        excerpt="Market Cap 168.806B · PE Ratio (TTM) 43.12 · EPS (TTM) 125.94",
        tags=["you_primary", "valuation"],
        result_rank=1,
        authority="secondary",
    )
    unavailable = QualitativeSlice(
        status="insufficient_evidence",
        score=None,
        summary="Not used by the deterministic valuation layer.",
        confidence=0,
    )
    dossier = CompanyDossierExtraction(
        ticker="PNGJL",
        business=unavailable,
        management=unavailable,
        fundamentals=FundamentalsExtraction(observations=[]),
        valuation=[],
    )
    runtime = RuntimeServices(
        settings=Settings(mode="live", research_as_of=date(2026, 9, 4)),
        book_agent=object(),  # type: ignore[arg-type]
    )
    complete = AnalysisBlock(agent="Upstream", score=70, summary="Complete")

    result = analyze_valuation(
        runtime,
        settings_target(),
        [source],
        complete,
        complete.model_copy(update={"growth_score": 70}),
        complete,
        complete,
        dossier,
    )

    assert result.status == "complete"
    assert [(metric.code, metric.value) for metric in result.metrics] == [("pe_ttm", 43.12)]
    assert result.metrics[0].as_of_date == date(2026, 7, 29)
    assert result.metrics[0].accounting_basis == "not_applicable"
    assert result.metrics[0].source_ids == ["thangamayil-yahoo"]


def test_valuation_uses_live_pe_with_adjacent_ttm_eps_and_rejects_ipo_pe() -> None:
    """A newly indexed IPO page must not outrank a current quote card."""

    common = {
        "company_ticker": "SENCO",
        "source_type": "web_search",
        "accessed_at": datetime(2026, 9, 4, tzinfo=UTC),
        "tags": ["you_primary", "valuation"],
        "authority": "secondary",
    }
    ipo = EvidenceItem(
        source_id="senco-ipo",
        title="Senco Gold Limited IPO valuation and PE ratio",
        url="https://example.test/ipo/senco-gold",
        published_at="2026-06-12",
        excerpt="IPO P/E Ratio: 15.53 · EPS (TTM): 22.93",
        result_rank=1,
        **common,
    )
    live_quote = EvidenceItem(
        source_id="senco-live",
        title="Senco Gold Ltd live share price today",
        url="https://example.test/stocks/senco",
        published_at="2026-06-02",
        excerpt=(
            "Market Capitalisation 5,578 Cr. · P/E Ratio: · 9.60 · P/B Ratio 2.20 · "
            "Sector P/E 45.96 · EPS (TTM): · 35.46 · Dividend Yield 0.51"
        ),
        result_rank=2,
        **common,
    )
    unavailable = QualitativeSlice(
        status="insufficient_evidence",
        score=None,
        summary="Not used by the deterministic valuation layer.",
        confidence=0,
    )
    dossier = CompanyDossierExtraction(
        ticker="PNGJL",
        business=unavailable,
        management=unavailable,
        fundamentals=FundamentalsExtraction(observations=[]),
        valuation=[],
    )
    runtime = RuntimeServices(
        settings=Settings(mode="live", research_as_of=date(2026, 9, 4)),
        book_agent=object(),  # type: ignore[arg-type]
    )
    complete = AnalysisBlock(agent="Upstream", score=70, summary="Complete")

    result = analyze_valuation(
        runtime,
        settings_target(),
        [ipo, live_quote],
        complete,
        complete.model_copy(update={"growth_score": 70}),
        complete,
        complete,
        dossier,
    )

    assert result.status == "complete"
    assert result.metrics[0].value == 9.6
    assert result.metrics[0].source_ids == ["senco-live"]


def test_valuation_does_not_promote_ambiguous_generic_pe_to_ttm() -> None:
    source = EvidenceItem(
        source_id="generic-pe",
        company_ticker="PNGJL",
        title="Generic valuation article",
        url="https://example.test/valuation",
        source_type="web_search",
        published_at="2026-09-01",
        accessed_at=datetime(2026, 9, 4, tzinfo=UTC),
        excerpt="The company P/E Ratio is 18.5, compared with several peers.",
        tags=["you_primary", "valuation"],
        authority="secondary",
    )
    unavailable = QualitativeSlice(
        status="insufficient_evidence",
        score=None,
        summary="Not used by the deterministic valuation layer.",
        confidence=0,
    )
    dossier = CompanyDossierExtraction(
        ticker="PNGJL",
        business=unavailable,
        management=unavailable,
        fundamentals=FundamentalsExtraction(observations=[]),
        valuation=[],
    )
    runtime = RuntimeServices(
        settings=Settings(mode="live", research_as_of=date(2026, 9, 4)),
        book_agent=object(),  # type: ignore[arg-type]
    )
    complete = AnalysisBlock(agent="Upstream", score=70, summary="Complete")

    result = analyze_valuation(
        runtime,
        settings_target(),
        [source],
        complete,
        complete,
        complete,
        complete,
        dossier,
    )

    assert result.status == "insufficient_evidence"
    assert result.metrics == []


def test_valuation_uses_compact_upstream_and_normalizes_required_pe() -> None:
    captured: dict[str, object] = {}

    class RecordingLLM:
        def generate(self, **kwargs: object) -> AnalysisBlock:
            captured.update(kwargs)
            return AnalysisBlock(
                agent="Valuation Agent",
                score=65,
                summary="Evidence-backed valuation.",
                strengths=["Reasonable normalized earnings multiple."],
                risks=[],
                metrics=[
                    Metric(
                        code="pe_ratio",
                        label="P/E",
                        value=22.5,
                        unit="x",
                        as_of_date=date(2026, 9, 1),
                        period_type="TTM",
                        accounting_basis="consolidated",
                        source_ids=["official"],
                    ),
                    Metric(
                        code="ev_ebitda",
                        label="EV/EBITDA",
                        value=18,
                        unit="x",
                        as_of_date=date(2026, 9, 1),
                        period_type="TTM",
                        accounting_basis="consolidated",
                        source_ids=["official"],
                    ),
                ],
                source_ids=["official"],
                confidence=0.7,
                growth_score=75,
            )

    source = evidence_item("official", "TTM P/E 22.5 and valuation evidence. " + "X" * 20_000)
    runtime = RuntimeServices(
        settings=Settings(mode="live"),
        book_agent=object(),  # type: ignore[arg-type]
        llm=RecordingLLM(),  # type: ignore[arg-type]
    )
    business = AnalysisBlock(
        agent="Business Agent", score=70, summary="B" * 2_000, source_ids=["official"]
    )
    fundamentals = AnalysisBlock(
        agent="Fundamentals Agent",
        score=75,
        growth_score=70,
        summary="F" * 2_000,
        source_ids=["official"],
    )
    management = AnalysisBlock(
        agent="Management Agent", score=70, summary="M" * 2_000, source_ids=["official"]
    )
    technicals = AnalysisBlock(
        agent="Technicals Agent", score=60, summary="T" * 2_000, source_ids=["official"]
    )

    result = analyze_valuation(
        runtime,
        settings_target(),
        [source],
        business,
        fundamentals,
        management,
        technicals,
    )

    assert result.status == "complete"
    assert [(metric.code, metric.value) for metric in result.metrics] == [("pe_ttm", 22.5)]
    assert result.growth_score is None
    prompt = str(captured["prompt"])
    upstream_text = prompt.split("Compact upstream analyses: ", 1)[1].split("\nEvidence: ", 1)[0]
    assert len(upstream_text) <= 5_000
    assert len(prompt.split("\nEvidence: ", 1)[1]) <= 12_000
    assert "pe_ttm, peg, price_sales, and gsec_10y_yield" in str(captured["instructions"])


def test_valuation_without_positive_pe_is_explicitly_unrankable() -> None:
    class MissingPeLLM:
        def generate(self, **_kwargs: object) -> AnalysisBlock:
            return AnalysisBlock(
                agent="Valuation Agent",
                score=60,
                summary="No normalized earnings multiple found.",
                source_ids=["official"],
                confidence=0.7,
            )

    source = evidence_item("official", "Valuation discussion without a normalized P/E.")
    runtime = RuntimeServices(
        settings=Settings(mode="live"),
        book_agent=object(),  # type: ignore[arg-type]
        llm=MissingPeLLM(),  # type: ignore[arg-type]
    )
    complete = AnalysisBlock(agent="Upstream", score=70, summary="Complete")

    result = analyze_valuation(
        runtime,
        settings_target(),
        [source],
        complete,
        complete.model_copy(update={"growth_score": 70}),
        complete,
        complete,
    )

    assert result.status == "insufficient_evidence"
    assert result.score is None
    assert result.confidence < 0.5
    assert "positive, cited pe_ttm" in result.risks[-1]


def test_valuation_runs_after_join_even_when_fundamentals_are_partial() -> None:
    calls = 0

    class ValuationLLM:
        def generate(self, **_kwargs: object) -> AnalysisBlock:
            nonlocal calls
            calls += 1
            return AnalysisBlock(
                agent="Valuation Agent",
                score=60,
                summary="Cited TTM valuation.",
                metrics=[
                    Metric(
                        code="pe_ttm",
                        label="TTM P/E",
                        value=25,
                        unit="x",
                        as_of_date=date(2026, 9, 1),
                        period_type="TTM",
                        accounting_basis="consolidated",
                        source_ids=["official"],
                    )
                ],
                source_ids=["official"],
                confidence=0.7,
            )

    source = evidence_item("official", "TTM P/E 25 as of September 2026.")
    runtime = RuntimeServices(
        settings=Settings(mode="live"),
        book_agent=object(),  # type: ignore[arg-type]
        llm=ValuationLLM(),  # type: ignore[arg-type]
    )
    complete = AnalysisBlock(agent="Upstream", score=70, summary="Complete")
    partial = AnalysisBlock(
        agent="Fundamentals Agent",
        status="insufficient_evidence",
        score=None,
        summary="Missing cash conversion.",
    )

    result = analyze_valuation(
        runtime,
        settings_target(),
        [source],
        complete,
        partial,
        complete,
        complete,
    )

    assert calls == 1
    assert result.status == "complete"
    assert result.metrics[0].code == "pe_ttm"


def test_live_source_agent_calls_you_before_indian_verification_sources() -> None:
    events: list[str] = []
    trace = SourceTrace(
        provider="you.com",
        endpoint="https://ydc-index.io/v1/search",
        query="test query",
        search_uuid="search-1",
        latency_seconds=0.1,
        retrieved_at="2026-09-04T10:00:00+00:00",
    )
    result = YouSearchResult(
        source_type="web",
        rank=1,
        url="https://example.com/pngjl",
        title="Current PNGJL research",
        description=None,
        snippets=(),
        page_age="2026-09-01",
        thumbnail_url=None,
        favicon_url=None,
        contents=SearchContents(highlights=("Current company evidence",)),
        trace=trace,
    )

    class FakeYou:
        def search(self, *_args: object, **_kwargs: object) -> YouSearchResponse:
            events.append("you")
            return YouSearchResponse(web=(result,), news=(), trace=trace)

    class FakeCollector:
        def collect(self, *_args: object, **_kwargs: object) -> FreeSourceCollection:
            events.append("indian_sources")
            official = record(retrieved_at="2026-09-04T10:01:00+00:00")
            return FreeSourceCollection(records=(official,), warnings=())

    settings = Settings(mode="live")
    runtime = RuntimeServices(
        settings=settings,
        book_agent=object(),  # type: ignore[arg-type]
        you_search=FakeYou(),  # type: ignore[arg-type]
        source_collector=FakeCollector(),  # type: ignore[arg-type]
    )

    evidence = gather_sources(runtime, company=settings_target())

    assert events == ["you", "you", "you", "you", "you", "you", "you", "indian_sources"]
    assert evidence[0].source_type == "you_web"
    assert evidence[-1].authority == "primary_regulatory"


def settings_target():
    """Return the same typed target identity used by the graph intake node."""

    from competitive_scoring.models import CompanyIdentity

    return CompanyIdentity(name="P N Gadgil Jewellers Limited", ticker="PNGJL")
