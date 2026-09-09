"""Describe evaluation coverage separately from observed model performance."""

from __future__ import annotations

import csv
import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "evals/fundamentals"


def load_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def distribution(values) -> dict:
    counts = Counter(values)
    total = sum(counts.values())
    return {
        "total": total,
        "categories": [
            {"label": label, "count": count, "share": count / total}
            for label, count in counts.items()
        ],
    }


def analyze(references: list[dict], predictions: list[dict]) -> dict:
    """Report both case counts and field counts; retain unrun and missing states."""
    facts = [fact for case in references for fact in case["expected_facts"]]
    expected_keys = {
        (case["case_id"], f["field"], f["period_end"], f["accounting_basis"])
        for case in references
        for f in case["expected_facts"]
    }
    observed_keys = {
        (r["case_id"], r["field"], r["period_end"], r["accounting_basis"]) for r in predictions
    }
    if expected_keys != observed_keys or len(predictions) != len(expected_keys):
        raise ValueError("Prediction snapshot does not match the current dataset's field slots.")
    available = [f for f in facts if f["available"]]
    if any((f["expected_value"] is not None) != f["available"] for f in facts):
        raise ValueError("Availability flags must agree with expected values.")
    real_cases = [c for c in references if c["data_kind"] == "real_report_excerpt"]
    real_available = [f for c in real_cases for f in c["expected_facts"] if f["available"]]
    value_keys = {
        tuple(
            f[k]
            for k in (
                "company_ticker",
                "field",
                "period_end",
                "period_type",
                "accounting_basis",
                "unit",
                "expected_value",
            )
        )
        for f in real_available
    }
    by_field = defaultdict(list)
    for fact in facts:
        by_field[fact["field"]].append(fact)
    field_coverage = []
    for field, items in by_field.items():
        known = [f for f in items if f["available"]]
        field_coverage.append(
            {
                "field": field,
                "total": len(items),
                "available": len(known),
                "unavailable": len(items) - len(known),
                "negative": sum(f["expected_value"] < 0 for f in known),
                "zero": sum(f["expected_value"] == 0 for f in known),
            }
        )
    approved = [c for c in references if c["review_status"] == "approved_for_pilot"]
    return {
        "scope": "Entire proposed Fundamentals development suite, including review drafts",
        "case_count": len(references),
        "field_slot_count": len(facts),
        "case_scenarios": distribution(c["scenario"] for c in references),
        "field_scenarios": distribution(
            c["scenario"] for c in references for _ in c["expected_facts"]
        ),
        "case_data_types": distribution(c["data_kind"] for c in references),
        "case_families": distribution(c["family_id"] for c in references),
        "review_status": distribution(c["review_status"] for c in references),
        "splits": distribution(c["split"] for c in references),
        "expected_availability": distribution(
            "available" if f["available"] else "unavailable" for f in facts
        ),
        "target_basis": distribution(f["accounting_basis"] for f in facts),
        "target_period_ends": distribution(f["period_end"] for f in facts),
        "target_period_types": distribution(f["period_type"] for f in facts),
        "expected_units": distribution(f["unit"] for f in facts),
        "field_coverage": field_coverage,
        "real_source_reuse": {
            "case_count": len(real_cases),
            "companies": sorted(
                {f["company_ticker"] for c in real_cases for f in c["expected_facts"]}
            ),
            "source_manifests": sorted({c["source_manifest"] for c in real_cases}),
            "available_fact_entries": len(real_available),
            "distinct_available_fact_values": len(value_keys),
            "repeated_fact_entries": len(real_available) - len(value_keys),
            "interpretation": "Repeated facts across controlled variants, not duplicate cases or independent reports.",
        },
        "approved_subset": {
            "case_count": len(approved),
            "field_count": sum(len(c["expected_facts"]) for c in approved),
            "available": sum(f["available"] for c in approved for f in c["expected_facts"]),
        },
        "always_available_baseline_accuracy": len(available) / len(facts) if facts else None,
        "prediction_status_counts": dict(Counter(r["result"] for r in predictions)),
        "measured_pass_rate": (
            sum(r["result"] == "PASS" for r in predictions)
            / sum(r["result"] in {"PASS", "FAIL"} for r in predictions)
            if any(r["result"] in {"PASS", "FAIL"} for r in predictions)
            else None
        ),
    }


def main() -> None:
    references = load_jsonl(DATA / "suite_references.jsonl")
    with (DATA / "golden_dataset_with_predictions.csv").open(newline="") as stream:
        predictions = list(csv.DictReader(stream))
    profile = analyze(references, predictions)
    profile["input_sha256"] = {
        name: hashlib.sha256((DATA / name).read_bytes()).hexdigest()
        for name in (
            "suite_references.jsonl",
            "suite_inputs.jsonl",
            "golden_dataset_with_predictions.csv",
        )
    }
    (DATA / "dataset_analysis.json").write_text(json.dumps(profile, indent=2) + "\n")
    print(
        json.dumps(
            {
                key: profile[key]
                for key in (
                    "case_count",
                    "field_slot_count",
                    "expected_availability",
                    "real_source_reuse",
                )
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
