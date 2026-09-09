"""Analyze saved agent results by scenario, financial category, field and failure cause."""
import argparse
import hashlib
import json
from pathlib import Path

from competitive_scoring.evaluation.analysis import analyze
from competitive_scoring.evaluation.fundamentals import fingerprint, grade_execution

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "evals/fundamentals"


def pct(value):
    return "N/A" if value is None else f"{100 * value:.2f}%"


def table(groups):
    lines = ["| Category | Slots | Pass | Fail | Accuracy | Precision | Recall | F1 |",
             "|---|---:|---:|---:|---:|---:|---:|---:|"]
    for g in groups:
        lines.append(f"| {g['category']} | {g['expected_slots']} | {g['pass']} | {g['fail']} | "
                     + " | ".join(pct(g[k]) for k in ("accuracy", "precision", "recall", "f1")) + " |")
    return "\n".join(lines)


def render(report):
    overall = report["overall"]
    counts = {b["category"]: b["count"] for b in report["outcome_buckets"]}
    missing_abstentions = overall["expected_unavailable"] - overall["unavailable_correct"]
    lines = ["# Fundamentals pass/fail analysis", "",
             f"Saved run: `{report['run_id']}`. No new model calls were made for this analysis.", "",
             f"**{overall['pass']}/{overall['expected_slots']} field checks pass ({pct(overall['accuracy'])}).** "
             f"Complete-case accuracy is {pct(report['case_accuracy'])}. "
             f"There are {overall['true_positives']} correct asserted facts and {overall['false_positives']} false-positive assertions. "
             "The outcome buckets below explain the failed checks.", "",
             "## By scenario", "", table(report["by_scenario"]), "",
             "Each scenario has one case. In the missing-fields scenario, precision and recall cover the four available "
             "financial facts; accuracy also counts required unavailable responses. This allows "
             "precision/recall/F1 to be 100% even when some required responses fail.", "",
             "## By financial category", "", table(report["by_metric_category"]), "",
             "## By financial field", "", table(report["by_field"]), "",
             "## Outcome buckets", "",
             "| Bucket | Result | Count | Share of all slots | Share of failures |",
             "|---|---|---:|---:|---:|"]
    for b in report["outcome_buckets"]:
        lines.append(f"| {b['category']} | {b['result']} | {b['count']} | {pct(b['share_of_slots'])} | {pct(b['share_of_failures'])} |")
    lines += ["", "These buckets are assigned after observing the outcome. Precision/recall are not calculated within "
              "failure-only buckets: doing so conditions on failure and gives misleading or undefined scores. "
              "The scenario and financial-category tables above provide meaningful denominators.", "",
              f"There are {counts.get('missing_requested_period', 0)} missing-period failures and "
              f"{counts.get('missing_unavailable_response', 0)} missing explicit unavailable responses. "
              "In the saved all-cases run, the missing periods are FY2025. The production runner does not "
              "forward that case's question, and final metrics retain the latest value per field. This is a request/output "
              "interface gap, not proof that the model ignored a two-year instruction. The missing-unavailable "
              "failures are ROE, ROCE and CFO in the reduced-evidence case. It avoids invented numbers but omits explicit abstentions.", "",
              "| Bucket | Suggested correction |", "|---|---|"]
    lines += [f"| {b['category']} | {b['suggested_correction']} |" for b in report["outcome_buckets"] if b["suggested_correction"]]
    lines += ["", "## Supporting scores", "",
              "| Category | Numeric accuracy | Unit accuracy, provided facts | Unit coverage | Explicit unavailable accuracy | Citation validity | Faithfulness |",
              "|---|---:|---:|---:|---:|---:|---:|"]
    for g in [{"category": "Overall", **overall}, *report["by_metric_category"]]:
        lines.append("| " + g["category"] + " | " + " | ".join(pct(g[k]) for k in
                     ("numeric_accuracy", "unit_accuracy", "unit_coverage", "unavailable_accuracy", "citation_validity", "faithfulness")) + " |")
    lines += ["", "## Definitions and limits", "",
              "- **Accuracy:** passed requested slots / measured requested slots, including explicit unavailable requirements. "
              "This is field pass rate, not binary classification accuracy using true negatives. Pending reviews and execution errors are excluded.",
              "- **Precision:** correct asserted financial facts / asserted financial facts. Wrong values, units, identities and duplicates reduce it.",
              "- **Recall:** correct asserted financial facts / available expected facts. Unsupported expected fields are assessed separately.",
              f"- **F1:** 2 × TP / (2 × TP + FP + FN). This run has {overall['true_positives']} TP, "
              f"{overall['false_positives']} FP and {overall['false_negatives']} FN; "
              f"the {missing_abstentions} missing/incorrect abstentions are separate.",
              "- **Numeric accuracy:** matching numbers / available expected facts, using ±5% relative tolerance and exact zero.",
              "- **Unit coverage:** available expected facts returned with the exact requested unit / available expected facts. "
              "Unit accuracy on provided facts excludes omitted values, so read both scores together.",
              "- **Faithfulness:** supported asserted requested facts / asserted requested facts. "
              "The existing Codex source checks were verified against their input/output hashes and reused. They are not independent human labels.",
              "- **N/A:** no eligible observations or no completed evidence review; never silently treated as zero or 100%.", "",
              f"Field-weighted accuracy is {pct(overall['accuracy'])}; equal-scenario average accuracy is "
              f"{pct(report['scenario_macro_accuracy'])}. The two-year case has 14 slots, twice the weight of each other "
              "case in the field-weighted result. Related PNGJL examples reuse one report. Five development cases with "
              "one example per scenario do not establish performance on unseen companies or future runs.", "",
              "Precision measures correctness of asserted financial facts and does not establish completeness. "
              "Use the outcome buckets to choose the next correction, and keep source support separate from numerical accuracy.", "",
              "The [CSV](golden_dataset_with_predictions.csv) adds scenario, financial category, result category and suggested correction "
              "columns. Human comments and the original expected/predicted/results columns are preserved. "
              "[Structured metrics](result_analysis.json) include counts, denominators, row membership and source fingerprints.", ""]
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, required=True)
    args = parser.parse_args()
    cases, fingerprints = [], {}
    manifest_path = args.run_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    for item in manifest:
        directory = ROOT / item["directory"]
        saved = {}
        for name in ("input", "reference", "actual", "metadata", "grade", "evidence_review"):
            path = directory / f"{name}.json"
            saved[name] = json.loads(path.read_text()) if path.exists() else None
            if path.exists():
                fingerprints[str(path.relative_to(ROOT))] = hashlib.sha256(path.read_bytes()).hexdigest()
        assert saved["metadata"]["actual_sha256"] == fingerprint(saved["actual"])
        assert saved["metadata"]["input_sha256"] == fingerprint(saved["input"])
        assert saved["metadata"]["reference_sha256"] == fingerprint(saved["reference"])
        case = {k: saved[k] for k in ("input", "reference", "actual", "evidence_review")}
        case["diagnostics"] = saved["metadata"]["diagnostics"]
        assert grade_execution(case["input"], case["reference"], case["actual"], case["diagnostics"], case["evidence_review"]) == saved["grade"]
        cases.append(case)
    report = {"run_id": args.run_dir.name, **analyze(cases), "source_sha256": fingerprints}
    (OUTPUT / "result_analysis.json").write_text(json.dumps(report, indent=2) + "\n")
    (OUTPUT / "result_analysis.md").write_text(render(report))
    print(table(report["by_scenario"]))
    print(table(report["by_metric_category"]))
    print(json.dumps(report["outcome_buckets"], indent=2))


if __name__ == "__main__":
    main()
