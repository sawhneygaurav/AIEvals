"""Bucket saved evaluation results without changing predictions or grading rules."""

from collections import Counter
from copy import deepcopy

from .fundamentals import IDENTITY, grade_case, grade_execution

CATEGORIES = {
    "Return ratios": {"roe", "roce"},
    "Profit and loss": {"revenue", "pat"},
    "Balance sheet": {"shareholders_equity", "borrowings"},
    "Operating cash flow": {"operating_cash_flow"},
}
SCENARIOS = {
    "all_values_available": "All values available",
    "missing_fields": "Missing fields",
    "two_years": "Two reporting years",
    "accounting_basis": "Consolidated vs standalone",
    "input_units": "Mixed input units",
}
CORRECTIONS = {
    "missing_requested_period": "Pass requested years into the agent and retain facts by field, date and accounting basis.",
    "missing_unavailable_response": "Return an explicit unavailable state for each requested unsupported field, with a reason.",
    "missing_value": "Inspect retrieval, extraction and output filtering for the omitted supported field.",
    "incorrect_value": "Check the cited financial concept, sign and scale against the source.",
    "incorrect_unit": "Normalize declared source units before returning the requested output unit.",
    "unsupported_value": "Do not provide an amount when the supplied evidence cannot support it.",
    "citation_issue": "Return a source ID that resolves to evidence supporting this exact fact.",
    "faithfulness_issue": "Correct or omit the claim after checking the cited evidence.",
    "availability_issue": "Make the availability flag consistent with the returned value.",
}


def fraction(numerator, denominator):
    return numerator / denominator if denominator else None


def identity(fact):
    return tuple(fact.get(key) for key in IDENTITY)


def classify(gold, fact, detail, review, prediction_index, predictions, scenario):
    """One diagnostic bucket per expected slot; scores use the existing grader."""
    if fact is None:
        if not gold["available"]:
            return "FAIL", "missing_unavailable_response"
        other_period = any(
            all(p.get(k) == gold[k] for k in ("field", "company_ticker", "accounting_basis"))
            and p.get("period_end") != gold["period_end"] for p in predictions
        )
        return "FAIL", ("missing_requested_period" if other_period and scenario == "two_years" else "missing_value")
    reasons = set(detail["reasons"])
    if not gold["available"]:
        return ("FAIL", "unsupported_value") if reasons else ("PASS", "correct_unavailable")
    if fact.get("value") is None:
        return "FAIL", "missing_value"
    for reason, bucket in (
        ("outside_numeric_tolerance", "incorrect_value"),
        ("wrong_or_missing_unit", "incorrect_unit"),
        ("inconsistent_availability", "availability_issue"),
        ("missing_or_unresolved_citation", "citation_issue"),
    ):
        if reason in reasons:
            return "FAIL", bucket
    if reasons:
        raise ValueError(f"Unclassified grader reasons: {sorted(reasons)}")
    if review is None:
        return "PENDING_REVIEW", "evidence_not_reviewed"
    if review["claims"][str(prediction_index)]["verdict"] != "supported":
        return "FAIL", "faithfulness_issue"
    return "PASS", "correct_value"


def prepare(cases):
    """Validate saved reviews and retain every expected slot and extra assertion."""
    prepared = []
    seen_cases = set()
    for original in cases:
        case = deepcopy(original)
        case_id = case["reference"]["case_id"]
        if case_id in seen_cases:
            raise ValueError(f"Duplicate case ID in one run: {case_id}")
        seen_cases.add(case_id)
        grade = grade_execution(case["input"], case["reference"], case["actual"],
                                case.get("diagnostics", []), case.get("evidence_review"))
        case["grade"] = grade
        ref, actual = case["reference"], case["actual"]
        first = {}
        for i, fact in enumerate(actual["facts"]):
            first.setdefault(identity(fact), i)
        details = {d["index"]: d for d in grade.get("details", []) if d["index"] is not None}
        rows = []
        for gold in ref["expected_facts"]:
            i = first.get(identity(gold))
            fact = actual["facts"][i] if i is not None else None
            if grade["status"] == "execution_error":
                result, bucket = "ERROR", "execution_error"
            else:
                result, bucket = classify(gold, fact, details.get(i), case.get("evidence_review"),
                                          i, actual["facts"], ref["scenario"])
            rows.append({
                "case_id": ref["case_id"], **{k: gold[k] for k in IDENTITY},
                "scenario": ref["scenario"],
                "metric_category": next((name for name, fields in CATEGORIES.items() if gold["field"] in fields), "Other"),
                "result": result, "result_category": bucket,
                "suggested_correction": CORRECTIONS.get(bucket, ""),
                "expected_available": gold["available"],
                "prediction_index": i,
            })
        case["rows"] = rows
        prepared.append(case)
    return prepared


def score_group(cases, fields=None):
    """Pool counts, not percentages. Precision/recall follow the existing grader.

    Accuracy is PASS / measured requested slots, including required abstentions.
    Extra/duplicate assertions remain in precision and the overall case result.
    """
    count = Counter()
    case_ids = set()
    for case in cases:
        selected = lambda f: fields is None or f["field"] in fields
        expected = [f for f in case["reference"]["expected_facts"] if selected(f)]
        indexed = [(i, f) for i, f in enumerate(case["actual"]["facts"]) if selected(f)]
        if not expected and not indexed:
            continue
        case_ids.add(case["reference"]["case_id"])
        if case["grade"]["status"] == "execution_error":
            count["error_slots"] += len(expected)
            count["execution_errors"] += 1
            continue
        count["measured_cases"] += 1
        grade = grade_case(case["input"], {**case["reference"], "expected_facts": expected},
                           {**case["actual"], "facts": [f for _, f in indexed]})
        for key in ("true_positives", "false_positives", "false_negatives", "expected_available", "expected_unavailable"):
            count[key] += grade[key]
        count["expected_slots"] += len(expected)
        available, unavailable = grade["expected_available"], grade["expected_unavailable"]
        count["numeric_correct"] += round((grade["numeric_accuracy"] or 0) * available)
        count["unit_covered"] += round((grade["unit_coverage"] or 0) * available)
        count["unavailable_correct"] += round((grade["unavailable_accuracy"] or 0) * unavailable)
        for row in case["rows"]:
            if selected(row):
                count[{"PASS": "pass", "FAIL": "fail", "PENDING_REVIEW": "pending"}[row["result"]]] += 1
        expected_by_key = {identity(f): f for f in expected}
        review = case.get("evidence_review")
        for j, (original_index, fact) in enumerate(indexed):
            if fact.get("value") is None:
                continue
            count["assertions"] += 1
            if review is not None:
                count["reviewed_assertions"] += 1
                count["supported_assertions"] += review["claims"][str(original_index)]["verdict"] == "supported"
            detail = next(d for d in grade["details"] if d["index"] == j)
            if "missing_or_unresolved_citation" not in detail["reasons"]:
                count["valid_citations"] += 1
            gold = expected_by_key.get(identity(fact))
            if gold and gold["available"] and "duplicate_fact" not in detail["reasons"]:
                count["provided_unit_checks"] += 1
                count["correct_provided_units"] += fact.get("unit") == gold["unit"]
    tp, fp, fn = [count[k] for k in ("true_positives", "false_positives", "false_negatives")]
    keys = ("expected_slots", "expected_available", "expected_unavailable", "pass", "fail", "pending",
            "true_positives", "false_positives", "false_negatives", "assertions", "numeric_correct",
            "unit_covered", "provided_unit_checks", "correct_provided_units", "unavailable_correct",
            "supported_assertions", "reviewed_assertions", "valid_citations", "error_slots", "execution_errors")
    return {
        "case_count": len(case_ids), **{k: count[k] for k in keys},
        "accuracy": fraction(count["pass"], count["pass"] + count["fail"]),
        "precision": fraction(tp, tp + fp), "recall": fraction(tp, tp + fn),
        "f1": fraction(2 * tp, 2 * tp + fp + fn),
        "numeric_accuracy": fraction(count["numeric_correct"], count["expected_available"]),
        "unit_accuracy": fraction(count["correct_provided_units"], count["provided_unit_checks"]),
        "unit_coverage": fraction(count["unit_covered"], count["expected_available"]),
        "unavailable_accuracy": fraction(count["unavailable_correct"], count["expected_unavailable"]),
        "citation_validity": fraction(count["valid_citations"], count["assertions"]),
        "faithfulness": fraction(count["supported_assertions"], count["assertions"])
        if count["reviewed_assertions"] == count["assertions"] else None,
    }


def analyze(cases):
    cases = prepare(cases)
    rows = [r for c in cases for r in c["rows"]]
    scenarios = list(dict.fromkeys(c["reference"]["scenario"] for c in cases))
    fields = sorted({f["field"] for c in cases for f in c["reference"]["expected_facts"]})
    by_scenario = [{"category": SCENARIOS.get(s, s), "scenario": s,
                    **score_group([c for c in cases if c["reference"]["scenario"] == s])} for s in scenarios]
    buckets = []
    failed = sum(r["result"] == "FAIL" for r in rows)
    for bucket in dict.fromkeys(r["result_category"] for r in rows):
        matched = [r for r in rows if r["result_category"] == bucket]
        buckets.append({"category": bucket, "result": matched[0]["result"], "count": len(matched),
                        "share_of_slots": fraction(len(matched), len(rows)),
                        "share_of_failures": fraction(len(matched), failed) if matched[0]["result"] == "FAIL" else None,
                        "suggested_correction": CORRECTIONS.get(bucket, "")})
    case_counts = Counter(c["grade"]["status"] for c in cases)
    scenario_accuracies = [g["accuracy"] for g in by_scenario if g["accuracy"] is not None]
    return {
        "overall": score_group(cases), "case_status_counts": dict(case_counts),
        "case_accuracy": fraction(case_counts["pass"], case_counts["pass"] + case_counts["fail"]),
        "scenario_macro_accuracy": sum(scenario_accuracies) / len(scenario_accuracies) if scenario_accuracies else None,
        "by_scenario": by_scenario,
        "by_metric_category": [{"category": label, **score_group(cases, codes)} for label, codes in CATEGORIES.items()],
        "by_field": [{"category": field, **score_group(cases, {field})} for field in fields],
        "outcome_buckets": buckets, "rows": rows,
    }
