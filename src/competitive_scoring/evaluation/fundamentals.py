"""Grade requested financial facts without changing or repairing agent output.

The answer key is evaluator-only. Faithfulness requires a separate, saved review
of actual claims against the supplied evidence; resolving a citation is not proof.
"""

from __future__ import annotations

import hashlib
import json
from decimal import Decimal, InvalidOperation
from typing import Any

FIELDS = frozenset(
    {
        "roe",
        "roce",
        "revenue",
        "pat",
        "shareholders_equity",
        "borrowings",
        "operating_cash_flow",
    }
)
IDENTITY = ("field", "company_ticker", "period_end", "period_type", "accounting_basis")


def fingerprint(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            allow_nan=False,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()


def number_matches(actual: Any, expected: Any, tolerance: Any = 0.05) -> bool:
    """Inclusive relative bounds, exact zero, no booleans/strings/nonfinite values."""
    if not isinstance(actual, (int, float, Decimal)) or isinstance(actual, bool):
        return False
    try:
        value, target, fraction = (Decimal(str(x)) for x in (actual, expected, tolerance))
    except (InvalidOperation, ValueError):
        return False
    return (
        value.is_finite()
        and target.is_finite()
        and fraction.is_finite()
        and fraction >= 0
        and abs(value - target) <= abs(target) * fraction
    )


def _identity(fact: dict) -> tuple:
    return tuple(fact.get(key) for key in IDENTITY)


def _fraction(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def grade_execution(
    case_input: dict,
    reference: dict,
    actual: dict,
    diagnostics: list[dict],
    evidence_review: dict | None = None,
) -> dict:
    """Do not confuse a failed provider/validation boundary with model quality."""
    failures = [
        d
        for d in diagnostics
        if d.get("code")
        in {
            "DOSSIER_MODEL_FAILURE",
            "FUNDAMENTALS_MODEL_FAILURE",
            "FUNDAMENTALS_REPAIR_FAILURE",
            "DOSSIER_TICKER_MISMATCH",
        }
    ]
    if failures:
        return {
            "case_id": actual["case_id"],
            "status": "execution_error",
            "quality_metrics": None,
            "excluded_from_quality_aggregates": True,
            "diagnostics": failures,
        }
    return grade_case(case_input, reference, actual, evidence_review)


def grade_case(
    case_input: dict, reference: dict, actual: dict, evidence_review: dict | None = None
) -> dict:
    """One-to-one exact-identity matching; wrong assertions count as FP and FN.

    Missing supported values count as FN. An explicit unavailable response is
    assessed separately and never earns a true positive for financial facts.
    """
    if not (case_input["case_id"] == reference["case_id"] == actual["case_id"]):
        raise ValueError("Case IDs must match.")
    expected = reference["expected_facts"]
    expected_by_key = {_identity(f): f for f in expected}
    if len(expected_by_key) != len(expected):
        raise ValueError("Duplicate identities in the answer key.")
    predicted = actual["facts"]
    source_ids = {s["source_id"] for s in case_input["evidence"]}
    tolerance = reference.get("relative_tolerance", 0.05)
    consumed: set[tuple] = set()
    correct: set[tuple] = set()
    numeric: set[tuple] = set()
    units: set[tuple] = set()
    unavailable_correct: set[tuple] = set()
    details = []
    tp = fp = assertions = valid_citations = provided_unit_total = 0
    provided_unit_correct = 0
    for index, fact in enumerate(predicted):
        key = _identity(fact)
        gold = expected_by_key.get(key)
        value_present = fact.get("value") is not None
        if value_present:
            assertions += 1
        supplied_ids = fact.get("source_ids")
        citation_ok = (
            isinstance(supplied_ids, list)
            and bool(supplied_ids)
            and all(isinstance(s, str) and s in source_ids for s in supplied_ids)
        )
        valid_citations += int(value_present and citation_ok)
        reasons = []
        if key in consumed:
            reasons.append("duplicate_fact")
        elif gold is None:
            reasons.append("wrong_field_company_period_or_basis")
        else:
            consumed.add(key)
            if gold["available"]:
                numeric_ok = number_matches(fact.get("value"), gold["expected_value"], tolerance)
                unit_ok = fact.get("unit") == gold["unit"]
                if numeric_ok:
                    numeric.add(key)
                if value_present:
                    provided_unit_total += 1
                    provided_unit_correct += int(unit_ok)
                    if unit_ok:
                        units.add(key)
                if not value_present:
                    reasons.append("required_value_missing")
                elif fact.get("available") is not True:
                    reasons.append("inconsistent_availability")
                if value_present and not numeric_ok:
                    reasons.append("outside_numeric_tolerance")
                if not unit_ok:
                    reasons.append("wrong_or_missing_unit")
                if not reasons:
                    correct.add(key)
                    tp += 1
            else:
                if not value_present and fact.get("available") is False:
                    unavailable_correct.add(key)
                else:
                    reasons.append("invented_value_for_unavailable_field")
        if value_present and reasons:
            fp += 1
        if value_present and not citation_ok:
            reasons.append("missing_or_unresolved_citation")
        details.append(
            {
                "index": index,
                "field": fact.get("field"),
                "period_end": fact.get("period_end"),
                "reasons": reasons,
            }
        )
    available_count = sum(f["available"] for f in expected)
    missing_count = len(expected) - available_count
    fn = available_count - tp
    precision = _fraction(tp, tp + fp)
    recall = _fraction(tp, available_count)
    f1 = _fraction(2 * tp, 2 * tp + fp + fn)
    for key, gold in expected_by_key.items():
        if key not in consumed:
            details.append(
                {
                    "index": None,
                    "field": gold["field"],
                    "period_end": gold["period_end"],
                    "reasons": ["omitted_required_field"],
                }
            )

    faithfulness = None
    review_status = "not_reviewed"
    if evidence_review is not None:
        if evidence_review.get("actual_sha256") != fingerprint(actual):
            raise ValueError("Evidence review does not match this exact agent output.")
        if evidence_review.get("input_sha256") != fingerprint(case_input):
            raise ValueError("Evidence review does not match this exact evidence input.")
        labels = evidence_review.get("claims", {})
        indices = {str(i) for i, f in enumerate(predicted) if f.get("value") is not None}
        if set(labels) != indices:
            raise ValueError("Evidence review must cover every asserted fact exactly once.")
        if any(
            v.get("verdict") not in {"supported", "unsupported", "unclear"} or not v.get("reason")
            for v in labels.values()
        ):
            raise ValueError("Every claim needs a verdict and evidence-based reason.")
        faithfulness = _fraction(
            sum(v["verdict"] == "supported" for v in labels.values()),
            assertions,
        )
        review_status = "reviewed"
    structural_pass = (
        tp == available_count
        and fp == 0
        and len(predicted) == len(expected)
        and len(unavailable_correct) == missing_count
        and len(units) == available_count
        and valid_citations == assertions
    )
    status = "fail"
    if structural_pass:
        status = (
            "pending_evidence_review"
            if review_status != "reviewed"
            else ("pass" if faithfulness == 1 or assertions == 0 else "fail")
        )
    return {
        "case_id": reference["case_id"],
        "status": status,
        "expected_available": available_count,
        "expected_unavailable": missing_count,
        "true_positives": tp,
        "false_positives": fp,
        "false_negatives": fn,
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "numeric_accuracy": _fraction(len(numeric), available_count),
        "unit_accuracy_on_provided_fields": _fraction(provided_unit_correct, provided_unit_total),
        "unit_coverage": _fraction(len(units), available_count),
        "unavailable_accuracy": _fraction(len(unavailable_correct), missing_count),
        "required_slot_accuracy": _fraction(tp + len(unavailable_correct), len(expected)),
        "citation_validity": _fraction(valid_citations, assertions),
        "faithfulness": faithfulness,
        "faithfulness_scope": "asserted requested structured facts only",
        "evidence_review_status": review_status,
        "details": details,
    }


def adapt_production_metrics(case_id: str, ticker: str, metrics: list) -> dict:
    """Map display labels only. Never convert amounts, dates or missing values.

    Only the seven requested codes are in scope. Other production scoring metrics
    remain saved in the raw trace, but are outside this pilot's precision denominator.
    """
    aliases = {"%": "percent", "INR crore": "INR_crore", "₹ crore": "INR_crore"}
    facts = []
    for metric in metrics:
        row = metric.model_dump(mode="json") if hasattr(metric, "model_dump") else metric
        if row["code"] not in FIELDS:
            continue
        facts.append(
            {
                "field": row["code"],
                "company_ticker": ticker,
                "value": row["value"],
                "available": row["value"] is not None,
                "unit": aliases.get(row["unit"], row["unit"]),
                "period_end": row["as_of_date"],
                "period_type": row["period_type"],
                "accounting_basis": row["accounting_basis"],
                "source_ids": row["source_ids"],
            }
        )
    return {"case_id": case_id, "facts": facts}
