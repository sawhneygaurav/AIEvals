"""Run frozen remaining-agent cases, then summarize saved deterministic and judge results."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import time
from collections import Counter, defaultdict
from dataclasses import replace
from datetime import UTC, date, datetime
from pathlib import Path

from competitive_scoring.agents import RuntimeServices
from competitive_scoring.config import Settings
from competitive_scoring.evaluation.agent_suite import (
    JUDGE_INSTRUCTIONS,
    MODEL_AGENTS,
    AgentJudgment,
    grade_checks,
    make_judge_packet,
    run_deterministic_case,
    run_model_case,
    validate_judge,
)
from competitive_scoring.evaluation.fundamentals import fingerprint
from competitive_scoring.llm import StructuredLLM

ROOT = Path(__file__).resolve().parents[1]
SUITE = ROOT / "evals/agents"

# Reviewed against the frozen evidence and traces in evals/agents/findings.md.
# Unreviewed failures stay explicit rather than inheriting a guessed cause.
FAILURE_BUCKETS = {
    ("BUSINESS-003", "LLM criterion", "C1"): "Source prioritization",
    ("VALUATION-004", "LLM criterion", "C2"): "Missing conflict disclosure",
}


def save(path, value):
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n")


def read(path):
    return json.loads(path.read_text())


def rows(path):
    data = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    if len({r["case_id"] for r in data}) != len(data):
        raise ValueError("Duplicate case IDs")
    return {r["case_id"]: r for r in data}


class Recorder:
    def __init__(self, llm, directory):
        self.llm, self.directory, self.calls = llm, directory, []

    def generate(self, **kwargs):
        record = {k: v for k, v in kwargs.items() if k != "schema"}
        record["schema"] = kwargs["schema"].model_json_schema()
        started = time.monotonic()
        try:
            result = self.llm.generate(**kwargs)
            record["response"] = result.model_dump(mode="json")
            return result
        except Exception as exc:
            record["error_type"] = type(exc).__name__
            raise
        finally:
            record["elapsed_seconds"] = time.monotonic() - started
            self.calls.append(record)
            save(self.directory / "calls.json", self.calls)


def llm_from(settings):
    return StructuredLLM(
        api_key=settings.llm_api_key,
        model=settings.llm_model,
        provider=settings.llm_provider,
        base_url=settings.llm_base_url,
        nebius_api_style=settings.llm_api_style,
        max_attempts=1,
        max_concurrent_requests=1,
        request_timeout_seconds=settings.llm_request_timeout_seconds,
        max_tokens=settings.llm_max_tokens,
        reasoning_effort=settings.llm_reasoning_effort,
    )


def run(args):
    inputs, references = rows(SUITE / "inputs.jsonl"), rows(SUITE / "references.jsonl")
    selected = [
        case
        for case in inputs.values()
        if (case["agent"] in MODEL_AGENTS) == (args.mode == "live")
        and (not args.case_id or case["case_id"] in args.case_id)
    ]
    if not selected:
        raise ValueError("No cases selected")
    if args.case_id and set(args.case_id) != {case["case_id"] for case in selected}:
        raise ValueError("Case IDs do not match the selected mode")
    for case in selected:
        if references[case["case_id"]]["review_status"] != "codex_reviewed_development":
            raise ValueError("An answer key lacks the recorded development review")
    settings = Settings.from_env(mode="live") if args.mode == "live" else None
    if settings and (not settings.llm_api_key or not settings.llm_model):
        raise ValueError("A configured model and API credential are required")
    args.output_dir.mkdir(parents=True, exist_ok=False)
    production = {
        str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in (ROOT / "src/competitive_scoring").rglob("*.py")
    }
    manifest = []
    for case in selected:
        cid = case["case_id"]
        directory = args.output_dir / cid
        directory.mkdir()
        ref = references[cid]
        save(directory / "input.json", case)
        save(directory / "reference.json", ref)
        meta = {
            "case_id": cid,
            "agent": case["agent"],
            "scenario": case["scenario"],
            "started_at": datetime.now(UTC).isoformat(),
            "input_sha256": fingerprint(case),
            "reference_sha256": fingerprint(ref),
            "production_source_sha256": production,
            "actor_question_delivered": False,
            "expected_values_delivered": False,
            "data_kind": "synthetic_development",
            "status": "running",
        }
        save(directory / "metadata.json", meta)
        print(f"Running {cid} ({case['scenario']})", flush=True)
        started = time.monotonic()
        actual, grades, judge = None, [], None
        try:
            if settings:
                scoped = replace(
                    settings, research_as_of=date.fromisoformat(case["research_cutoff"])
                )
                recorder = Recorder(llm_from(scoped), directory)
                runtime = RuntimeServices(settings=scoped, book_agent=None, llm=recorder)
                meta.update(
                    provider=scoped.llm_provider,
                    model=scoped.llm_model,
                    api_style=scoped.llm_api_style,
                    reasoning_effort=scoped.llm_reasoning_effort,
                    max_attempts=1,
                    max_tokens=scoped.llm_max_tokens,
                )
                actual = run_model_case(case, runtime)
                diagnostics = [
                    {"code": d.code, "ticker": d.ticker} for d in runtime.diagnostics_snapshot()
                ]
                meta["diagnostics"] = diagnostics
                save(directory / "actual.json", actual)
                meta["actual_sha256"] = fingerprint(actual)
                if diagnostics:
                    meta["status"] = "actor_execution_error"
                else:
                    grades = grade_checks(actual, ref)
                    judge_dir = directory / "judge"
                    judge_dir.mkdir()
                    packet = make_judge_packet(case, actual)
                    save(judge_dir / "packet.json", packet)
                    meta["judge_packet_sha256"] = fingerprint(packet)
                    judge_recorder = Recorder(llm_from(scoped), judge_dir)
                    try:
                        response = judge_recorder.generate(
                            schema=AgentJudgment,
                            instructions=JUDGE_INSTRUCTIONS,
                            prompt=json.dumps(packet, ensure_ascii=False),
                            context=f"Judge {cid}",
                        )
                        save(judge_dir / "response.json", response.model_dump(mode="json"))
                        validate_judge(response, packet)
                        judge = response.model_dump(mode="json")
                        meta["judge_response_sha256"] = fingerprint(judge)
                        meta["status"] = "completed"
                    except Exception as exc:  # noqa: BLE001 - preserve judge failure separately
                        meta["status"] = "judge_execution_error"
                        meta["judge_error_type"] = type(exc).__name__
            else:
                actual = run_deterministic_case(case)
                save(directory / "actual.json", actual)
                meta["actual_sha256"] = fingerprint(actual)
                grades = grade_checks(actual, ref)
                meta["status"] = "completed"
        except Exception as exc:  # noqa: BLE001 - batch boundary records and isolates failures
            meta["status"] = "actor_execution_error"
            meta["error_type"] = type(exc).__name__
            # Stack locations aid local diagnosis without printing exception text/credentials.
            import traceback

            meta["error_locations"] = [
                {"file": Path(f.filename).name, "line": f.lineno, "function": f.name}
                for f in traceback.extract_tb(exc.__traceback__)
            ]
        finally:
            meta["elapsed_seconds"] = time.monotonic() - started
            save(directory / "metadata.json", meta)
            save(directory / "grade.json", {"checks": grades, "judge": judge})
            manifest.append(
                {"case_id": cid, "directory": str(directory.resolve()), "status": meta["status"]}
            )
            save(args.output_dir / "manifest.json", manifest)
        failed = sum(g["verdict"] == "fail" for g in grades)
        print(f"{cid}: {meta['status']}; deterministic failures {failed}/{len(grades)}", flush=True)
        # Avoid repeatedly spending requests when a provider is unavailable.
        if settings and meta["status"] == "actor_execution_error":
            print("Stopped after actor execution error; saved cases remain intact.", flush=True)
            break


def report(args):
    inputs = rows(SUITE / "inputs.jsonl")
    saved = {}
    for run_dir in args.source_runs:
        for item in read(run_dir / "manifest.json"):
            cid = item["case_id"]
            if cid in saved:
                raise ValueError(f"Duplicate result for {cid}; choose one explicit run per case")
            directory = Path(item["directory"])
            meta, inp, ref, actual = [
                read(directory / f"{name}.json") if (directory / f"{name}.json").exists() else None
                for name in ("metadata", "input", "reference", "actual")
            ]
            if (
                fingerprint(inp) != meta["input_sha256"]
                or fingerprint(ref) != meta["reference_sha256"]
            ):
                raise ValueError("Saved input/reference fingerprint changed")
            if actual is not None and fingerprint(actual) != meta.get("actual_sha256"):
                raise ValueError("Saved actual fingerprint changed")
            grade = read(directory / "grade.json")
            if grade["judge"] is not None:
                packet = read(directory / "judge/packet.json")
                if fingerprint(packet) != meta["judge_packet_sha256"]:
                    raise ValueError("Judge packet fingerprint changed")
                if fingerprint(grade["judge"]) != meta["judge_response_sha256"]:
                    raise ValueError("Judge response fingerprint changed")
                validate_judge(AgentJudgment.model_validate(grade["judge"]), packet)
            if (
                actual is not None
                and grade["checks"] != grade_checks(actual, ref)
                and meta["status"] != "actor_execution_error"
            ):
                raise ValueError("Saved checks differ from deterministic regrade")
            saved[cid] = {
                "directory": str(directory),
                "metadata": meta,
                "grade": grade,
                "input": inp,
                "reference": ref,
                "actual": actual,
            }
    groups = defaultdict(Counter)
    comparison, case_results = [], []
    for cid, case in inputs.items():
        group = groups[case["agent"]]
        group["cases"] += 1
        if cid not in saved:
            group["unrun"] += 1
            case_results.append({"case_id": cid, "status": "unrun"})
            continue
        row = saved[cid]
        meta, grade = row["metadata"], row["grade"]
        state = meta["status"]
        group[state] += 1
        if state == "actor_execution_error":
            case_results.append({"case_id": cid, "status": state})
            continue
        for check in grade["checks"]:
            group["checks"] += 1
            group[f"check_{check['verdict']}"] += 1
            comparison.append(
                {
                    "case_id": cid,
                    "agent": case["agent"],
                    "scenario": case["scenario"],
                    "kind": "deterministic",
                    "check_id": check["check_id"],
                    "expected": json.dumps(check["expected"]),
                    "predicted": json.dumps(check["predicted"]),
                    "verdict": check["verdict"],
                    "reason": f"{check['op']} at {check['path']}",
                    "human_comments": "",
                }
            )
        judge = grade["judge"]
        if judge:
            for j in judge["criteria"]:
                group["judge_criteria"] += 1
                group[f"judge_{j['verdict']}"] += 1
                requirement = next(
                    c["requirement"]
                    for c in case["criteria"]
                    if c["criterion_id"] == j["criterion_id"]
                )
                comparison.append(
                    {
                        "case_id": cid,
                        "agent": case["agent"],
                        "scenario": case["scenario"],
                        "kind": "LLM criterion",
                        "check_id": j["criterion_id"],
                        "expected": requirement,
                        "predicted": json.dumps(row["actual"]["block"], ensure_ascii=False),
                        "verdict": j["verdict"],
                        "reason": j["reason"],
                        "human_comments": "",
                    }
                )
            for claim in judge["claims"]:
                group[f"claim_{claim['support']}"] += 1
                claim_input = next(
                    c
                    for c in make_judge_packet(row["input"], row["actual"])["claims"]
                    if c["claim_id"] == claim["claim_id"]
                )
                comparison.append(
                    {
                        "case_id": cid,
                        "agent": case["agent"],
                        "scenario": case["scenario"],
                        "kind": "LLM claim support",
                        "check_id": claim["claim_id"],
                        "expected": "All material factual assertions supported by supplied evidence",
                        "predicted": json.dumps(claim_input, ensure_ascii=False),
                        "verdict": claim["support"],
                        "reason": claim["reason"],
                        "human_comments": "",
                    }
                )
        passes = all(c["verdict"] == "pass" for c in grade["checks"])
        if case["agent"] in MODEL_AGENTS:
            passes = (
                passes
                and judge is not None
                and all(j["verdict"] == "pass" for j in judge["criteria"])
            )
            passes = (
                passes
                and all(c["support"] in {"supported", "not_applicable"} for c in judge["claims"])
                if judge
                else False
            )
        outcome = "pass" if passes else "fail" if state == "completed" else state
        group[f"case_{outcome}"] += 1
        case_results.append(
            {
                "case_id": cid,
                "status": outcome,
                "scenario": case["scenario"],
                "artifact_directory": row["directory"],
            }
        )
    for row in comparison:
        key = (row["case_id"], row["kind"], row["check_id"])
        if row["verdict"] in {"fail", "unsupported"}:
            row["failure_bucket"] = FAILURE_BUCKETS.get(key, "Unclassified — review needed")
        elif row["verdict"] in {"uncertain", "unclear"}:
            row["failure_bucket"] = "Uncertain — review needed"
        else:
            row["failure_bucket"] = ""
    bucket_counts = Counter(row["failure_bucket"] for row in comparison if row["failure_bucket"])
    args.output_dir.mkdir(parents=True, exist_ok=True)
    csv_path = args.output_dir / "expected_vs_predicted.csv"
    if csv_path.exists():
        previous = {
            (r["case_id"], r["kind"], r["check_id"]): r["human_comments"]
            for r in csv.DictReader(csv_path.open())
        }
        for row in comparison:
            row["human_comments"] = previous.get((row["case_id"], row["kind"], row["check_id"]), "")
    with csv_path.open("w", newline="") as file:
        writer = csv.DictWriter(
            file,
            fieldnames=[
                "case_id",
                "agent",
                "scenario",
                "kind",
                "check_id",
                "expected",
                "predicted",
                "verdict",
                "failure_bucket",
                "reason",
                "human_comments",
            ],
        )
        writer.writeheader()
        writer.writerows(comparison)
    summary = {
        "groups": groups,
        "cases": case_results,
        "failure_buckets": dict(bucket_counts),
        "source_runs": [str(p.resolve()) for p in args.source_runs],
    }

    def rate(numerator, denominator):
        return numerator / denominator if denominator else None

    summary["metrics"] = {
        agent: {
            "case_pass_rate": rate(g["case_pass"], g["completed"]),
            "rule_check_pass_rate": rate(g["check_pass"], g["checks"]),
            "judge_criterion_pass_rate": rate(g["judge_pass"], g["judge_criteria"]),
            "judge_claim_span_faithfulness": rate(
                g["claim_supported"],
                sum(g[f"claim_{k}"] for k in ("supported", "unsupported", "unclear")),
            ),
        }
        for agent, g in groups.items()
    }
    save(args.output_dir / "results.json", summary)
    lines = [
        "# Remaining-agent baseline",
        "",
        (
            "All 44 cases use fictional development fixtures. "
            "Language agents use the configured live model; technical, source, book and graph inputs "
            "use frozen adapters. No live search or private-book semantic retrieval is measured."
        ),
        "",
        "| Agent | Cases pass / completed | Rule checks pass / checked | Judge criteria pass / checked | Supported / factual claim spans | Errors | Unrun |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for agent, g in groups.items():
        claims = sum(g[f"claim_{k}"] for k in ("supported", "unsupported", "unclear"))
        lines.append(
            f"| {agent} | {g['case_pass']}/{g['completed']} | {g['check_pass']}/{g['checks']} | "
            f"{g['judge_pass']}/{g['judge_criteria']} | {g['claim_supported']}/{claims} | "
            f"{g['actor_execution_error'] + g['judge_execution_error']} | {g['unrun']} |"
        )
    lines += [
        "",
        (
            "A case passes only when every deterministic check and judge criterion passes, "
            "and all judged factual spans are supported. Judge uncertainty prevents a pass. Citation "
            "resolution is a structural check, not proof of faithfulness. Pure abstentions/process "
            "statements are excluded from factual support; omissions cannot earn faithfulness credit."
        ),
        "",
        (
            "Numbers in the judge columns are same-model assessments, not independent human validation. "
            "Human calibration is pending. Free-form precision, recall and F1 are not reported: a "
            "complete independently annotated atomic-claim inventory does not yet exist. Rule checks "
            "include identities, units and gates; they are not interchangeable with fact counts."
        ),
        "",
        (
            "Business, Management and Valuation receive their existing production instructions and "
            "evidence. Their interfaces have no per-case question argument. Any missing requested "
            "content must be interpreted with that constraint. Actor and judge errors remain separate "
            "from quality failures."
        ),
        "",
        "## Non-passing cases",
        "",
    ]
    for row in case_results:
        if row["status"] != "pass":
            lines.append(f"- {row['case_id']}: {row['status']}")
    if bucket_counts:
        lines += ["", "## Failure buckets", "", "| Bucket | Checks |", "|---|---:|"]
        lines += [f"| {bucket} | {count} |" for bucket, count in sorted(bucket_counts.items())]
    lines += [
        "",
        (
            "See [all expectations and predictions](expected_vs_predicted.csv), "
            "[reviewable cases](suite_review.md), and [structured results](results.json)."
        ),
        "",
    ]
    (args.output_dir / "results.md").write_text("\n".join(lines))
    counts = Counter(case["agent"] for case in inputs.values())
    balance = [
        "# Dataset balance",
        "",
        (
            "44 synthetic development cases; zero real-company cases, "
            "zero holdout cases and zero human-approved cases. One fictional issuer is used for "
            "model cases; deterministic graph fixtures reuse the application's invented four-company demo."
        ),
        "",
        "| Agent | Cases | Scenarios |",
        "|---|---:|---|",
    ]
    for agent, n in counts.items():
        scenarios = [c["scenario"] for c in inputs.values() if c["agent"] == agent]
        balance.append(f"| {agent} | {n} | {', '.join(scenarios)} |")
    balance += [
        "",
        (
            "Gaps: real saved sources, additional companies, messy/long excerpts, conflicting "
            "periods and bases, oscillating RSI series, live retrieval relevance, private-book passage "
            "relevance, repeated model runs and unseen holdout examples. Source fixtures test collection "
            "logic, not search precision/recall. Book fixtures test policy coverage, not semantic retrieval."
        ),
        "",
    ]
    (args.output_dir / "dataset_analysis.md").write_text("\n".join(balance))
    print(json.dumps({agent: dict(g) for agent, g in groups.items()}, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=["offline", "live", "report"], required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--source-runs", nargs="*", type=Path, default=[])
    parser.add_argument("--case-id", action="append")
    args = parser.parse_args()
    if args.mode == "report":
        if not args.source_runs:
            parser.error("report mode needs --source-runs")
        report(args)
    else:
        run(args)


if __name__ == "__main__":
    main()
