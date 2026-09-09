"""Independent checks of bucket denominators and error/abstention handling."""
from copy import deepcopy

import pytest

from competitive_scoring.evaluation.analysis import analyze
from competitive_scoring.evaluation.fundamentals import fingerprint


def example():
    common = {"company_ticker": "EXAMPLE", "period_end": "2026-03-31",
              "period_type": "FY", "accounting_basis": "consolidated"}
    facts = [
        {**common, "field": "roe", "expected_value": 20, "unit": "percent", "available": True},
        {**common, "field": "revenue", "expected_value": 100, "unit": "INR_crore", "available": True},
        {**common, "field": "operating_cash_flow", "expected_value": None, "unit": "INR_crore", "available": False},
    ]
    predictions = []
    for gold in facts[:2]:
        fact = {**gold, "value": gold["expected_value"], "source_ids": ["source"]}
        del fact["expected_value"]
        predictions.append(fact)
    return {
        "input": {"case_id": "test", "evidence": [{"source_id": "source"}]},
        "reference": {"case_id": "test", "scenario": "missing_fields", "expected_facts": facts},
        "actual": {"case_id": "test", "facts": predictions}, "diagnostics": [],
    }


def reviewed(case, verdicts=None):
    case = deepcopy(case)
    case["evidence_review"] = {
        "input_sha256": fingerprint(case["input"]), "actual_sha256": fingerprint(case["actual"]),
        "claims": {str(i): {"verdict": (verdicts or {}).get(i, "supported"), "reason": "Test fixture review."}
                   for i, f in enumerate(case["actual"]["facts"]) if f["value"] is not None},
    }
    return case


def test_omitted_abstention_fails_accuracy_without_becoming_a_financial_false_negative():
    report = analyze([reviewed(example())])
    overall = report["overall"]
    assert overall["accuracy"] == 2 / 3
    assert overall["precision"] == overall["recall"] == overall["f1"] == 1
    assert overall["unavailable_accuracy"] == 0
    assert overall["false_negatives"] == 0
    assert report["rows"][-1]["result_category"] == "missing_unavailable_response"
    cfo = next(g for g in report["by_field"] if g["category"] == "operating_cash_flow")
    assert cfo["precision"] is cfo["recall"] is cfo["f1"] is None
    assert cfo["accuracy"] == 0


def test_wrong_number_counts_as_both_false_positive_and_false_negative_in_its_category():
    case = example()
    case["actual"]["facts"][0]["value"] = 30
    report = analyze([reviewed(case)])
    score = report["overall"]
    assert (score["true_positives"], score["false_positives"], score["false_negatives"]) == (1, 1, 1)
    assert score["precision"] == score["recall"] == score["f1"] == 0.5
    assert score["accuracy"] == 1 / 3
    assert report["rows"][0]["result_category"] == "incorrect_value"
    ratio = report["by_metric_category"][0]
    assert ratio["precision"] == ratio["recall"] == ratio["accuracy"] == 0


def test_wrong_unit_keeps_numeric_accuracy_separate_from_correct_fact_metrics():
    case = example()
    case["actual"]["facts"][1]["unit"] = "INR_million"
    report = analyze([reviewed(case)])
    assert report["overall"]["numeric_accuracy"] == 1
    assert report["overall"]["unit_accuracy"] == report["overall"]["unit_coverage"] == 0.5
    assert report["overall"]["precision"] == 0.5
    assert report["rows"][1]["result_category"] == "incorrect_unit"


def test_duplicate_assertion_reduces_precision_but_never_inflates_expected_slot_accuracy():
    case = example()
    case["actual"]["facts"].append(deepcopy(case["actual"]["facts"][0]))
    report = analyze([reviewed(case)])
    assert report["overall"]["assertions"] == 3
    assert report["overall"]["precision"] == 2 / 3
    assert report["overall"]["recall"] == 1
    assert report["overall"]["accuracy"] == 2 / 3
    assert report["overall"]["unit_covered"] == report["overall"]["provided_unit_checks"] == 2


def test_missing_period_is_not_mapped_to_a_different_year_prediction():
    case = example()
    prior = {**case["reference"]["expected_facts"][0], "period_end": "2025-03-31"}
    case["reference"]["expected_facts"].append(prior)
    case["reference"]["scenario"] = "two_years"
    report = analyze([reviewed(case)])
    assert report["rows"][-1]["result_category"] == "missing_requested_period"
    assert report["rows"][-1]["prediction_index"] is None
    assert report["overall"]["recall"] == 2 / 3


def test_unsupported_source_claim_reduces_faithfulness_and_pass_rate_not_numeric_precision():
    report = analyze([reviewed(example(), {0: "unsupported"})])
    assert report["overall"]["faithfulness"] == 0.5
    assert report["overall"]["precision"] == 1
    assert report["overall"]["accuracy"] == 1 / 3
    assert report["rows"][0]["result_category"] == "faithfulness_issue"


def test_reviews_are_hash_bound_and_cannot_be_reused_after_prediction_change():
    case = reviewed(example())
    case["actual"]["facts"][0]["value"] = 19
    with pytest.raises(ValueError, match="exact agent output"):
        analyze([case])


def test_provider_error_is_excluded_from_quality_denominators():
    case = example()
    case["diagnostics"] = [{"code": "FUNDAMENTALS_MODEL_FAILURE"}]
    report = analyze([case])
    assert report["overall"]["accuracy"] is report["overall"]["precision"] is None
    assert report["overall"]["expected_slots"] == 0
    assert report["overall"]["error_slots"] == 3
    assert all(r["result"] == "ERROR" for r in report["rows"])


def test_explicit_correct_abstention_passes_without_increasing_financial_precision_denominator():
    case = example()
    gold = case["reference"]["expected_facts"][-1]
    case["actual"]["facts"].append({k: v for k, v in {**gold, "value": None, "source_ids": []}.items() if k != "expected_value"})
    report = analyze([reviewed(case)])
    assert report["overall"]["accuracy"] == report["overall"]["unavailable_accuracy"] == 1
    assert report["overall"]["assertions"] == 2
    assert report["rows"][-1]["result_category"] == "correct_unavailable"


def test_macro_accuracy_does_not_silently_weight_larger_scenarios_twice():
    a = reviewed(example())  # 2/3 slots pass.
    b = example()
    b["reference"]["expected_facts"] = b["reference"]["expected_facts"][:1]
    b["actual"]["facts"] = b["actual"]["facts"][:1]
    b["reference"]["scenario"] = "all_values_available"  # 1/1 passes.
    for name in ("input", "reference", "actual"):
        b[name]["case_id"] = "second"
    b = reviewed(b)
    report = analyze([a, b])
    assert report["overall"]["accuracy"] == 3 / 4
    assert report["scenario_macro_accuracy"] == pytest.approx((2 / 3 + 1) / 2)
