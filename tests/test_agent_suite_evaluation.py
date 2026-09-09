"""Evaluation integrity: answer-key blinding, strict comparisons and judge coverage."""

import copy
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from competitive_scoring.agents import RuntimeServices
from competitive_scoring.config import Settings
from competitive_scoring.evaluation.agent_suite import (
    MODEL_AGENTS,
    AgentJudgment,
    grade_checks,
    make_judge_packet,
    run_deterministic_case,
    run_model_case,
    validate_judge,
)
from competitive_scoring.evaluation.fundamentals import fingerprint
from competitive_scoring.models import (
    CompanyDossierExtraction,
    FundamentalsExtraction,
    QualitativeSlice,
)

SUITE = Path(__file__).resolve().parents[1] / "evals/agents"
CASES = [json.loads(line) for line in (SUITE / "inputs.jsonl").read_text().splitlines()]
REFS = {
    r["case_id"]: r for r in map(json.loads, (SUITE / "references.jsonl").read_text().splitlines())
}


def test_dataset_ids_and_evidence_validate():
    from competitive_scoring.evaluation.agent_suite import source_items

    assert len(CASES) == len({c["case_id"] for c in CASES}) == len(REFS) == 44
    for case in CASES:
        sources = source_items(case)
        assert len({s.source_id for s in sources}) == len(sources)
        assert case["data_kind"] == "synthetic_development"
        assert REFS[case["case_id"]]["human_review_status"] == "pending"
        assert "expected" not in json.dumps(case)


@pytest.mark.parametrize(
    "actual,expected,op,passed",
    [
        ({}, None, "equal", False),
        ({"a": None}, None, "equal", True),
        ({"a": False}, 0, "equal", False),
        ({"a": True}, 1, "numeric", False),
        ({"a": "1"}, 1, "numeric", False),
        ({"a": float("nan")}, 1, "numeric", False),
        ({"a": float("inf")}, 1, "numeric", False),
        ({"a": 1.0}, 1, "numeric", True),
        ({}, None, "absent", True),
        ({"a": None}, None, "absent", False),
    ],
)
def test_grader_does_not_coerce_absence_boolean_or_nonfinite(actual, expected, op, passed):
    reference = {"checks": [{"check_id": "a", "path": "a", "op": op, "expected": expected}]}
    assert (grade_checks(actual, reference)[0]["verdict"] == "pass") == passed


def sample_actual():
    return {
        "block": {
            "summary": "Aster has 40 stores.",
            "strengths": [],
            "risks": [],
            "metrics": [],
            "source_ids": ["B1"],
        }
    }


def test_judge_packet_is_whitelisted_and_does_not_leak_reference_or_intermediate():
    case = {**CASES[0], "reference": {"gold": "SECRET-ANSWER"}, "grade": "SECRET-GRADE"}
    actual = {**sample_actual(), "dossier": {"business": "SECRET-INTERMEDIATE"}}
    packet = make_judge_packet(case, actual)
    assert "SECRET" not in json.dumps(packet)
    assert packet["candidate"] == actual["block"]


def judgment_packet():
    packet = make_judge_packet(CASES[0], sample_actual())
    result = {
        "case_id": CASES[0]["case_id"],
        "criteria": [
            {
                "criterion_id": c["criterion_id"],
                "verdict": "pass",
                "reason": "Fixture",
                "evidence_source_ids": ["B1"],
            }
            for c in packet["criteria"]
        ],
        "claims": [
            {
                "claim_id": "summary",
                "support": "supported",
                "reason": "Fixture",
                "evidence_source_ids": ["B1"],
            }
        ],
    }
    return result, packet


@pytest.mark.parametrize(
    "corruption", ["case", "omission", "duplicate", "unknown_source", "unsupported_citation"]
)
def test_judge_validator_rejects_incomplete_or_invalid_reviews(corruption):
    result, packet = judgment_packet()
    if corruption == "case":
        result["case_id"] = "WRONG"
    elif corruption == "omission":
        result["criteria"].pop()
    elif corruption == "duplicate":
        result["claims"].append(copy.deepcopy(result["claims"][0]))
    elif corruption == "unknown_source":
        result["claims"][0]["evidence_source_ids"] = ["UNKNOWN"]
    else:
        result["claims"][0]["evidence_source_ids"] = []
    with pytest.raises(ValueError):
        validate_judge(AgentJudgment.model_validate(result), packet)


def test_production_actor_never_receives_answer_key_or_case_question():
    captured = []

    class LLM:
        def generate(self, **kwargs):
            captured.append(kwargs)
            absent = QualitativeSlice(
                status="insufficient_evidence", score=None, summary="No assessment.", confidence=0
            )
            return CompanyDossierExtraction(
                ticker="ASTER",
                business=absent,
                management=absent,
                fundamentals=FundamentalsExtraction(observations=[]),
            )

    case = {**CASES[0], "question": "SECRET-QUESTION", "reference": "SECRET-ANSWER"}
    runtime = RuntimeServices(settings=Settings(mode="live"), book_agent=None, llm=LLM())
    run_model_case(case, runtime)
    assert len(captured) == 1
    assert "SECRET" not in captured[0]["prompt"] + captured[0]["instructions"]


@pytest.mark.parametrize(
    "case", [c for c in CASES if c["agent"] not in MODEL_AGENTS], ids=lambda c: c["case_id"]
)
def test_frozen_policy_cases_run_and_match_reviewed_expectations(case):
    actual = run_deterministic_case(case)
    rows = grade_checks(actual, REFS[case["case_id"]])
    assert rows and all(row["verdict"] == "pass" for row in rows), rows


def saved_report_fixture(tmp_path):
    spec = importlib.util.spec_from_file_location(
        "agent_eval_runner", SUITE.parents[1] / "scripts/run_agent_evals.py"
    )
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    case = next(c for c in CASES if c["case_id"] == "PEERS-001")
    ref = REFS[case["case_id"]]
    actual = {"tickers": ["KALYANKJIL", "SENCO", "THANGAMAYL"], "error": None}
    directory = tmp_path / "run/PEERS-001"
    directory.mkdir(parents=True)
    for name, value in {
        "input": case,
        "reference": ref,
        "actual": actual,
        "metadata": {
            "input_sha256": fingerprint(case),
            "reference_sha256": fingerprint(ref),
            "actual_sha256": fingerprint(actual),
            "status": "completed",
        },
        "grade": {"checks": grade_checks(actual, ref), "judge": None},
    }.items():
        runner.save(directory / f"{name}.json", value)
    runner.save(
        directory.parent / "manifest.json",
        [{"case_id": case["case_id"], "directory": str(directory)}],
    )
    args = SimpleNamespace(source_runs=[directory.parent], output_dir=tmp_path / "report")
    return runner, args, directory


def test_saved_report_preserves_comments_and_unrun_denominators(tmp_path):
    import csv

    runner, args, _ = saved_report_fixture(tmp_path)
    runner.report(args)
    path = args.output_dir / "expected_vs_predicted.csv"
    with path.open() as file:
        data = list(csv.DictReader(file))
    data[0]["human_comments"] = "Human reviewed this exact check."
    with path.open("w", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=list(data[0]))
        writer.writeheader()
        writer.writerows(data)
    runner.report(args)
    assert "Human reviewed this exact check." in path.read_text()
    result = json.loads((args.output_dir / "results.json").read_text())
    assert result["groups"]["peers"]["case_pass"] == 1
    assert result["groups"]["peers"]["unrun"] == 2
    assert result["metrics"]["business"]["case_pass_rate"] is None


def test_report_rejects_tampered_outputs_and_duplicate_case_runs(tmp_path):
    runner, args, directory = saved_report_fixture(tmp_path)
    runner.save(directory / "actual.json", {"tickers": [], "error": None})
    with pytest.raises(ValueError, match="fingerprint changed"):
        runner.report(args)
    runner.save(
        directory / "actual.json", {"tickers": ["KALYANKJIL", "SENCO", "THANGAMAYL"], "error": None}
    )
    args.source_runs *= 2
    with pytest.raises(ValueError, match="Duplicate result"):
        runner.report(args)
