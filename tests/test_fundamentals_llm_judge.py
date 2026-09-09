"""Guard judge blinding, complete coverage and valid evidence references."""
from copy import deepcopy

import pytest

from competitive_scoring.evaluation.llm_judge import JudgeResult, judge_packet, validate_judgments


def fixture():
    identity = {"field": "roe", "company_ticker": "EXAMPLE", "period_end": "2026-03-31",
                "period_type": "FY", "accounting_basis": "consolidated"}
    inp = {"case_id": "case", "question": "Report annual ROE.",
           "evidence": [{"source_id": "report", "content": "Reported ROE is 20 percent."}]}
    ref = {"expected_facts": [{**identity, "expected_value": 20, "available": True,
                                "unit": "percent", "human_comments": "private answer-key note"}]}
    actual = {"facts": [{**identity, "value": 20, "unit": "percent", "available": True,
                           "source_ids": ["report"]}]}
    result = {"case_id": "case", "judgments": [{"field": "roe", "period_end": "2026-03-31",
        "verdict": "pass", "issue": "none", "numeric_within_tolerance": True,
        "unit_correct": True, "identity_correct": True, "source_support": "supported",
        "evidence_source_ids": ["report"], "reason": "The cited source supports the stated value."}],
        "extra_assertion_issues": []}
    return inp, ref, actual, result


def test_judge_cannot_see_answer_key_values_availability_or_review_notes():
    inp, ref, actual, _ = fixture()
    packet = judge_packet(inp, ref, actual)
    altered = deepcopy(ref)
    altered["expected_facts"][0].update(expected_value=999, available=False, human_comments="FAIL")
    assert packet == judge_packet(inp, altered, actual)
    assert "expected_value" not in str(packet)
    assert "human_comments" not in str(packet)
    assert packet["candidate_facts"] == actual["facts"]


def test_valid_judge_response_covers_the_requested_slot():
    inp, ref, actual, result = fixture()
    validate_judgments(JudgeResult.model_validate(result), judge_packet(inp, ref, actual))


@pytest.mark.parametrize("change", ["duplicate", "wrong_date", "wrong_case", "invented_source", "contradiction"])
def test_invalid_judge_outputs_are_rejected_without_silently_relabeling(change):
    inp, ref, actual, result = fixture()
    row = result["judgments"][0]
    if change == "duplicate":
        result["judgments"].append(deepcopy(row))
    elif change == "wrong_date":
        row["period_end"] = "2025-03-31"
    elif change == "wrong_case":
        result["case_id"] = "another-case"
    elif change == "invented_source":
        row["evidence_source_ids"] = ["fabricated-report"]
    else:
        row["issue"] = "incorrect_value"
    with pytest.raises(ValueError):
        validate_judgments(JudgeResult.model_validate(result), judge_packet(inp, ref, actual))
