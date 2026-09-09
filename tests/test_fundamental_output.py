"""Regression coverage for the production agent's seven-field financial output."""

from datetime import UTC, date, datetime

import pytest

from competitive_scoring.agents import (
    RuntimeServices,
    _build_fundamentals_block,
    _finance_keyword_excerpt,
    _finish_live_fundamentals_analysis,
    _requested_fundamental_metrics,
    analyze_fundamentals,
    extract_company_dossier,
)
from competitive_scoring.config import Settings
from competitive_scoring.models import (
    CompanyIdentity,
    EvidenceItem,
    FundamentalObservation,
    FundamentalUnavailable,
    FundamentalsExtraction,
    FundamentalsRequest,
    CompanyDossierExtraction,
    QualitativeSlice,
)

CUTOFF = date(2026, 9, 7)


def source(source_id="report", excerpt="Consolidated financial statements FY2026. Reported ROE: 18 percent; net operating cash flow: 150 INR million.", **updates):
    return EvidenceItem(
        source_id=source_id, company_ticker="EXAMPLE", title="Annual report",
        url=f"https://example.invalid/{source_id}", source_type="official_document",
        accessed_at=datetime(2026, 9, 7, tzinfo=UTC), excerpt=excerpt,
        tags=["official", "fundamentals"], authority="primary_company",
    ).model_copy(update=updates)


def fact(code, value, unit="INR_million", **updates):
    balance = code in {"shareholders_equity", "borrowings", "current_borrowings", "non_current_borrowings"}
    return FundamentalObservation.model_validate({
        "code": code, "value": value, "unit": unit, "as_of_date": "2026-03-31",
        "period_type": "point_in_time" if balance else "FY",
        "accounting_basis": "consolidated", "source_id": "report", **updates,
    })


def build(facts, sources=None):
    return _build_fundamentals_block(facts, sources=sources or [source()], research_as_of=CUTOFF)


def scoring_facts():
    return [
        fact("roe", 18, "percent"), fact("roce", 20, "percent"),
        fact("revenue_growth", 15, "percent"), fact("profit_growth", 20, "percent"),
        fact("debt_equity", 0.5, "ratio"), fact("interest_coverage", 5, "ratio"),
        fact("cfo_pat", 0.9, "ratio"),
    ]


def test_seven_fields_survive_production_output_with_normalized_units_and_provenance():
    block = build([
        fact("roe", 0.21, "ratio"), fact("roce", 0.30, "ratio"),
        fact("revenue", 107390.97), fact("pat", 4098.20),
        fact("shareholders_equity", 19627.26), fact("current_borrowings", 15692.18),
        fact("non_current_borrowings", 103.65), fact("operating_cash_flow", -7168.98),
    ])
    metrics = {m.code: m for m in block.metrics}
    expected = {"roe": 21, "roce": 30, "revenue": 10739.097, "pat": 409.82,
                "shareholders_equity": 1962.726, "borrowings": 1579.583,
                "operating_cash_flow": -716.898}
    for code, value in expected.items():
        metric = metrics[code]
        assert metric.value == pytest.approx(value)
        assert metric.unit == ("%" if code in {"roe", "roce"} else "INR crore")
        assert metric.as_of_date == date(2026, 3, 31)
        assert metric.accounting_basis == "consolidated"
        assert metric.source_ids == ["report"]
        assert metric.period_type == ("point_in_time" if code in {"shareholders_equity", "borrowings"} else "FY")
    assert "0.21 ratio" in metrics["roe"].definition
    assert "107390.97 INR_million" in metrics["revenue"].definition
    assert "15692.18 INR_million" in metrics["borrowings"].definition
    assert metrics["revenue"].value == 10739.097
    assert metrics["shareholders_equity"].value == 1962.726
    assert metrics["borrowings"].measurement_type == "derived"
    assert metrics["borrowings"].formula == "current_borrowings + non_current_borrowings"
    # Returning facts does not waive the independent investment-score gate.
    assert block.status == "insufficient_evidence"
    assert block.score is None


@pytest.mark.parametrize("raw,unit", [(10000, "INR_lakh"), (1000, "INR_million"), (100, "INR_crore"), (1, "INR_billion")])
def test_all_declared_inr_scales_represent_the_same_amount(raw, unit):
    metric = build([fact("revenue", raw, unit)]).metrics[0]
    assert metric.value == pytest.approx(100)
    assert metric.unit == "INR crore"


@pytest.mark.parametrize("raw,unit,expected", [(0.21, "ratio", 21), (21, "percent", 21), (0.21, "percent", 0.21), (-0.05, "ratio", -5)])
def test_return_ratio_conversion_uses_declared_unit_without_guessing(raw, unit, expected):
    assert build([fact("roe", raw, unit)]).metrics[0].value == pytest.approx(expected)


@pytest.mark.parametrize("row", [
    fact("roe", 3, "ratio"), fact("roe", 21, "INR_crore"),
    fact("revenue", 100, "percent"), fact("borrowings", -100),
    fact("shareholders_equity", 100, period_type="FY"),
    fact("pat", 100, source_id="unresolved"), fact("pat", float("nan")),
])
def test_invalid_observations_are_not_converted_into_valid_predictions(row):
    assert build([row]).metrics == []


def test_zero_cash_flow_and_negative_profit_and_equity_are_preserved():
    metrics = {m.code: m for m in build([
        fact("operating_cash_flow", 0), fact("pat", -100), fact("shareholders_equity", -200),
    ]).metrics}
    assert metrics["operating_cash_flow"].value == 0
    assert metrics["pat"].value == -10
    assert metrics["shareholders_equity"].value == -20


@pytest.mark.parametrize("mismatch", [
    {"as_of_date": "2025-03-31"}, {"accounting_basis": "standalone"},
    {"source_id": "other"}, {"period_type": "FY"},
])
def test_borrowings_components_cannot_mix_dates_bases_sources_or_periods(mismatch):
    block = build([fact("current_borrowings", 100), fact("non_current_borrowings", 20, **mismatch)],
                  [source(), source("other")])
    assert "borrowings" not in {m.code for m in block.metrics}


def test_borrowings_require_both_components_but_allow_explicit_zero_and_mixed_scales():
    current = fact("current_borrowings", 10000, "INR_lakh")
    assert build([current]).metrics == []
    assert build([current, fact("non_current_borrowings", 0)]).metrics[0].value == 100
    assert build([current, fact("non_current_borrowings", 0.5, "INR_billion")]).metrics[0].value == 150


def test_reported_borrowings_total_wins_over_same_date_components():
    metric = build([fact("borrowings", 110), fact("current_borrowings", 100),
                    fact("non_current_borrowings", 20)]).metrics[0]
    assert metric.value == 11
    assert metric.measurement_type == "reported"


def test_extra_statement_amounts_do_not_change_score_growth_or_confidence():
    original = build(scoring_facts())
    extra = build(scoring_facts() + [
        fact("revenue", 10000, source_id="other"), fact("pat", 100),
        fact("operating_cash_flow", 200), fact("shareholders_equity", 500), fact("borrowings", 200),
    ], [source(), source("other", authority="secondary")])
    assert original.status == extra.status == "complete"
    assert (original.score, original.growth_score, original.confidence) == (extra.score, extra.growth_score, extra.confidence)
    assert len(extra.metrics) == len(original.metrics) + 5


def test_old_cash_history_can_support_conversion_without_being_shown_as_current_profit():
    block = build([fact("pat", 100, as_of_date="2022-03-31"),
                   fact("operating_cash_flow", 120, as_of_date="2022-03-31")])
    assert block.metrics == []


def test_supported_statement_amount_is_repaired_even_when_score_is_complete():
    calls = []

    class RepairLLM:
        def generate(self, **kwargs):
            calls.append(kwargs)
            assert "Missing codes: revenue" in kwargs["prompt"]
            return FundamentalsExtraction(observations=[fact("revenue", 10000)])

    runtime = RuntimeServices(settings=Settings(mode="live", research_as_of=CUTOFF),
                              book_agent=None, llm=RepairLLM())
    result = _finish_live_fundamentals_analysis(
        runtime, CompanyIdentity(ticker="EXAMPLE", name="Example Limited"),
        [source(excerpt="Consolidated FY ended 31 March 2026. Revenue from operations: 10000 INR million.")],
        initial=FundamentalsExtraction(observations=scoring_facts()),
    )
    assert len(calls) == 1
    assert {m.code: m.value for m in result.metrics}["revenue"] == 1000
    assert result.status == "complete"


def test_balance_sheet_labels_survive_compact_evidence_window():
    excerpt = "irrelevant filler " * 1000 + " Equity attributable to owners: 500. " + "filler " * 1000 + " Current borrowings: 150; non-current borrowings: 20."
    compact = _finance_keyword_excerpt(excerpt, max_chars=3000)
    assert "Equity attributable to owners: 500" in compact
    assert "Current borrowings: 150" in compact


def request_for(*rows):
    return FundamentalsRequest(company_ticker="EXAMPLE", question="Return the requested years and fields.",
        slots=[{k: row.model_dump()[k] for k in ("code", "as_of_date", "period_type", "accounting_basis")}
               for row in rows])


def test_requested_history_survives_without_replacing_current_score_metrics():
    current = fact("roe", 18, "percent")
    historical = fact("roe", 0.12, "ratio", as_of_date="2022-03-31")
    rows = [*scoring_facts(), historical]
    runtime = RuntimeServices(settings=Settings(mode="live", research_as_of=CUTOFF), book_agent=None)
    result = _finish_live_fundamentals_analysis(runtime,
        CompanyIdentity(ticker="EXAMPLE", name="Example"), [source()],
        initial=FundamentalsExtraction(observations=rows), request=request_for(current, historical))
    assert [(m.as_of_date.year, m.value) for m in result.requested_facts] == [(2026, 18), (2022, 12)]
    ordinary = build(rows)
    assert result.metrics == ordinary.metrics
    assert (result.score, result.growth_score, result.confidence) == (ordinary.score, ordinary.growth_score, ordinary.confidence)


def test_omission_stays_missing_but_explicit_unavailable_survives_with_reason():
    roe = fact("roe", 20, "percent")
    request = request_for(roe)
    assert _requested_fundamental_metrics([], [], request, [source()], CUTOFF) == []
    absent = FundamentalUnavailable(**request.slots[0].model_dump(),
        reason="No reported ROE in the supplied balance sheet.", inspected_source_ids=["report"])
    result = _requested_fundamental_metrics([], [absent], request, [source()], CUTOFF)
    assert len(result) == 1 and result[0].value is None
    assert result[0].unit == "%" and result[0].source_ids == []
    assert result[0].definition == absent.reason
    assert "explicit_unavailable" in result[0].flags


def test_supported_zero_takes_priority_over_a_contradictory_unavailable_entry():
    zero = fact("operating_cash_flow", 0)
    request = request_for(zero)
    absent = FundamentalUnavailable(**request.slots[0].model_dump(), reason="Missing")
    result = _requested_fundamental_metrics([zero], [absent], request, [source()], CUTOFF)
    assert result[0].value == 0 and result[0].source_ids == ["report"]


@pytest.mark.parametrize("change", [
    {"accounting_basis": "standalone"}, {"as_of_date": "2025-03-31"},
    {"source_id": "other-company"}, {"unit": "INR_crore"},
])
def test_requested_selection_never_repairs_a_wrong_identity_or_unit(change):
    roe = fact("roe", 20, "percent")
    changed = roe.model_copy(update=change)
    if "as_of_date" in change:
        changed.as_of_date = date.fromisoformat(change["as_of_date"])
    sources = [source(), source("other-company", company_ticker="OTHER")]
    assert _requested_fundamental_metrics([changed], [], request_for(roe), sources, CUTOFF) == []


def test_borrowings_are_aggregated_independently_for_each_requested_year():
    rows = [fact("current_borrowings", 100, as_of_date="2026-03-31"),
            fact("non_current_borrowings", 20, as_of_date="2026-03-31"),
            fact("current_borrowings", 50, as_of_date="2022-03-31"),
            fact("non_current_borrowings", 0, as_of_date="2022-03-31")]
    request = request_for(fact("borrowings", 120), fact("borrowings", 50, as_of_date="2022-03-31"))
    result = _requested_fundamental_metrics(rows, [], request, [source()], CUTOFF)
    assert [m.value for m in result] == [12, 5]
    # Missing historical component must not use the current year's component.
    assert len(_requested_fundamental_metrics(rows[:-1], [], request, [source()], CUTOFF)) == 1


def test_requested_repair_receives_only_missing_identities_and_cannot_replace_a_pass():
    roe, cash = fact("roe", 20, "percent"), fact("operating_cash_flow", 150)
    request = request_for(roe, cash)
    calls = []

    class Repair:
        def generate(self, **kwargs):
            calls.append(kwargs)
            assert '"code":"operating_cash_flow"' in kwargs["prompt"]
            assert '"code":"roe"' not in kwargs["prompt"]
            return FundamentalsExtraction(observations=[fact("roe", 30, "percent"), cash])

    runtime = RuntimeServices(settings=Settings(mode="live", research_as_of=CUTOFF), book_agent=None, llm=Repair())
    result = _finish_live_fundamentals_analysis(runtime,
        CompanyIdentity(ticker="EXAMPLE", name="Example"), [source()],
        initial=FundamentalsExtraction(observations=[roe]), request=request)
    assert len(calls) == 1
    assert [m.value for m in result.requested_facts] == [20, 15]


@pytest.mark.parametrize("use_dossier", [True, False])
def test_original_question_and_requested_dates_reach_both_live_entry_points(use_dossier):
    roe = fact("roe", 20, "percent")
    request = request_for(roe)
    calls = []
    company = CompanyIdentity(ticker="EXAMPLE", name="Example")

    class Actor:
        def generate(self, **kwargs):
            calls.append(kwargs)
            assert request.question in kwargs["prompt"]
            assert '"as_of_date":"2026-03-31"' in kwargs["prompt"]
            assert "every requested year separately" in kwargs["instructions"]
            extraction = FundamentalsExtraction(observations=[roe])
            if kwargs["schema"] is FundamentalsExtraction:
                return extraction
            empty = QualitativeSlice(status="insufficient_evidence", score=None,
                                     summary="No qualitative evidence", confidence=0)
            return CompanyDossierExtraction(ticker="EXAMPLE", business=empty, management=empty,
                                            fundamentals=extraction)

    runtime = RuntimeServices(settings=Settings(mode="live", research_as_of=CUTOFF), book_agent=None, llm=Actor())
    dossier = extract_company_dossier(runtime, company, [source()], fundamentals_request=request) if use_dossier else None
    result = analyze_fundamentals(runtime, company, [source()], dossier=dossier, request=request)
    assert len(calls) == 1 and result.requested_facts[0].value == 20


def test_request_rejects_answer_key_fields_and_duplicate_or_wrong_company_scope():
    roe = fact("roe", 20, "percent")
    request = request_for(roe)
    with pytest.raises(ValueError):
        FundamentalsRequest(**{**request.model_dump(), "expected_value": 20})
    with pytest.raises(ValueError):
        request_for(roe, roe)
    with pytest.raises(ValueError, match="ticker"):
        analyze_fundamentals(RuntimeServices(settings=Settings(mode="live"), book_agent=None),
                             CompanyIdentity(ticker="OTHER", name="Other"), [source()], request=request)
