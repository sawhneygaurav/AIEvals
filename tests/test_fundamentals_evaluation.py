"""Mutation tests for the evaluation boundary, with no model/network calls."""

import copy
import json
from pathlib import Path

import pytest

from competitive_scoring.evaluation.fundamentals import (
    adapt_production_metrics,
    fingerprint,
    grade_case,
    grade_execution,
    number_matches,
)

FOLDER = Path(__file__).resolve().parents[1] / "evals/fundamentals"
INPUTS = [json.loads(r) for r in (FOLDER / "suite_inputs.jsonl").read_text().splitlines()]
REFERENCES = [json.loads(r) for r in (FOLDER / "suite_references.jsonl").read_text().splitlines()]


def answer(index=0):
    facts = []
    for gold in REFERENCES[index]["expected_facts"]:
        f = copy.deepcopy(gold)
        f["value"] = f.pop("expected_value")
        facts.append(f)
    return {"case_id": REFERENCES[index]["case_id"], "facts": facts}


def grade(actual, index=0, review=None):
    return grade_case(INPUTS[index], REFERENCES[index], actual, review)


@pytest.mark.parametrize(
    "actual,expected,passes",
    [
        (22.05, 21, True),
        (22.05001, 21, False),
        (-752.7429, -716.898, True),
        (-681.0531, -716.898, True),
        (716.898, -716.898, False),
        (0, 0, True),
        (0.000001, 0, False),
        (True, 1, False),
        ("21", 21, False),
        (float("nan"), 21, False),
        (float("inf"), 21, False),
    ],
)
def test_numeric_boundaries(actual, expected, passes):
    assert number_matches(actual, expected) is passes


@pytest.mark.parametrize("index", range(5))
def test_full_reference_does_not_fake_a_faithfulness_score(index):
    result = grade(answer(index), index)
    assert result["precision"] == result["recall"] == result["f1"] == 1
    assert result["faithfulness"] is None
    assert result["status"] == "pending_evidence_review"


def test_wrong_number_is_both_false_positive_and_false_negative():
    actual = answer()
    actual["facts"][0]["value"] = 200
    result = grade(actual)
    assert (result["true_positives"], result["false_positives"], result["false_negatives"]) == (
        6,
        1,
        1,
    )


@pytest.mark.parametrize(
    "key,value",
    [
        ("period_end", "2025-03-31"),
        ("accounting_basis", "standalone"),
        ("company_ticker", "WRONG"),
        ("period_type", "quarter"),
        ("unit", "ratio"),
    ],
)
def test_metadata_and_unit_mismatches_fail_even_with_right_number(key, value):
    actual = answer()
    actual["facts"][0][key] = value
    result = grade(actual)
    assert result["status"] == "fail"
    assert result["false_positives"] == result["false_negatives"] == 1


def test_duplicate_cannot_inflate_recall():
    actual = answer()
    actual["facts"].append(copy.deepcopy(actual["facts"][0]))
    result = grade(actual)
    assert result["true_positives"] == 7
    assert result["false_positives"] == 1
    assert result["precision"] == 7 / 8


def test_missing_values_require_explicit_abstention():
    actual = answer(1)
    assert grade(actual, 1)["unavailable_accuracy"] == 1
    actual["facts"] = [f for f in actual["facts"] if f["available"]]
    assert grade(actual, 1)["unavailable_accuracy"] == 0


def test_invented_missing_value_is_false_positive():
    actual = answer(1)
    actual["facts"][0].update(value=21, available=True)
    result = grade(actual, 1)
    assert result["false_positives"] == 1
    assert result["unavailable_accuracy"] == 2 / 3


def test_missing_citation_fails_case_without_disguising_numeric_correctness():
    actual = answer()
    actual["facts"][0]["source_ids"] = ["invented-source"]
    result = grade(actual)
    assert result["recall"] == 1
    assert result["citation_validity"] == 6 / 7
    assert result["status"] == "fail"


def test_faithfulness_labels_are_bound_to_output_and_evidence():
    actual = answer()
    labels = {
        "actual_sha256": fingerprint(actual),
        "input_sha256": fingerprint(INPUTS[0]),
        "claims": {str(i): {"verdict": "supported", "reason": "Test review."} for i in range(7)},
    }
    assert grade(actual, review=labels)["status"] == "pass"
    labels["claims"]["0"]["verdict"] = "unsupported"
    assert grade(actual, review=labels)["faithfulness"] == 6 / 7
    assert grade(actual, review=labels)["status"] == "fail"
    actual["facts"][0]["value"] = 20
    with pytest.raises(ValueError, match="exact agent output"):
        grade(actual, review=labels)


def test_adapter_never_repairs_units_or_invents_missing_fields():
    metric = {
        "code": "pat",
        "value": 4098.2,
        "unit": "INR_million",
        "as_of_date": "2026-03-31",
        "period_type": "FY",
        "accounting_basis": "consolidated",
        "source_ids": ["s"],
    }
    actual = adapt_production_metrics("test", "PNGJL", [metric])
    assert actual["facts"][0]["value"] == 4098.2
    assert actual["facts"][0]["unit"] == "INR_million"
    assert len(actual["facts"]) == 1


def test_provider_error_is_not_a_zero_quality_result():
    result = grade_execution(
        INPUTS[0], REFERENCES[0], answer(), [{"code": "DOSSIER_MODEL_FAILURE"}]
    )
    assert result["status"] == "execution_error"
    assert result["quality_metrics"] is None
    assert result["excluded_from_quality_aggregates"] is True


def test_empty_successful_response_has_no_precision_or_faithfulness():
    actual = {"case_id": INPUTS[0]["case_id"], "facts": []}
    result = grade(actual)
    assert result["precision"] is None
    assert result["faithfulness"] is None
    assert result["recall"] == result["f1"] == 0
    assert result["false_negatives"] == 7
