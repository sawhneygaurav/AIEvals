"""Run an isolated LLM judge on saved ROE/ROCE/CFO outputs, or diagnose its findings."""
import argparse
import inspect
import json
import time
from datetime import UTC, datetime
from pathlib import Path

from competitive_scoring import agents
from competitive_scoring.config import Settings
from competitive_scoring.evaluation.fundamentals import fingerprint
from competitive_scoring.evaluation.llm_judge import (
    DIAGNOSIS_PROMPT, FOCUS_FIELDS, JUDGE_PROMPT, JudgeResult, PromptDiagnosis,
    judge_packet, validate_judgments,
)
from competitive_scoring.llm import StructuredLLM
from competitive_scoring.models import FundamentalsExtraction

ROOT = Path(__file__).resolve().parents[1]


def read(path):
    return json.loads(path.read_text())


def invoke(settings, directory, schema, instructions, packet, context):
    directory.mkdir(parents=True, exist_ok=False)
    (directory / "instructions.md").write_text(instructions)
    (directory / "packet.json").write_text(json.dumps(packet, indent=2, ensure_ascii=False) + "\n")
    (directory / "schema.json").write_text(json.dumps(schema.model_json_schema(), indent=2) + "\n")
    metadata = {
        "started_at": datetime.now(UTC).isoformat(), "provider": settings.llm_provider,
        "model": settings.llm_model, "api_style": settings.llm_api_style,
        "reasoning_effort": settings.llm_reasoning_effort, "max_attempts": 1,
        "max_tokens": settings.llm_max_tokens,
        "packet_sha256": fingerprint(packet), "instructions_sha256": fingerprint(instructions),
        "schema_sha256": fingerprint(schema.model_json_schema()),
    }
    llm = StructuredLLM(api_key=settings.llm_api_key, model=settings.llm_model,
                        provider=settings.llm_provider, base_url=settings.llm_base_url,
                        nebius_api_style=settings.llm_api_style, max_attempts=1,
                        max_concurrent_requests=1, max_tokens=settings.llm_max_tokens,
                        reasoning_effort=settings.llm_reasoning_effort,
                        request_timeout_seconds=settings.llm_request_timeout_seconds)
    started = time.monotonic()
    try:
        result = llm.generate(schema=schema, instructions=instructions,
                              prompt=json.dumps(packet, ensure_ascii=False), context=context)
        metadata["status"] = "completed"
        body = result.model_dump(mode="json")
        metadata["response_sha256"] = fingerprint(body)
        (directory / "response.json").write_text(json.dumps(body, indent=2, ensure_ascii=False) + "\n")
        return result
    except Exception as exc:
        metadata["status"] = "execution_error"
        metadata["error_type"] = type(exc).__name__
        raise
    finally:
        metadata["elapsed_seconds"] = time.monotonic() - started
        (directory / "metadata.json").write_text(json.dumps(metadata, indent=2) + "\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=["judge", "diagnose"], default="judge")
    parser.add_argument("--source-run", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--judge-run", type=Path)
    args = parser.parse_args()
    settings = Settings.from_env(mode="live")
    if not settings.llm_api_key or not settings.llm_model:
        raise SystemExit("The configured model and API credential are required.")
    manifest = read(args.source_run / "manifest.json")
    if args.mode == "judge":
        args.output_dir.mkdir(parents=True, exist_ok=False)
        completed = []
        for item in manifest:
            source = ROOT / item["directory"]
            inp, ref, actual, meta = [read(source / f"{name}.json") for name in ("input", "reference", "actual", "metadata")]
            assert fingerprint(actual) == meta["actual_sha256"]
            assert fingerprint(inp) == meta["input_sha256"]
            packet = judge_packet(inp, ref, actual)
            print("Judging", inp["case_id"], flush=True)
            directory = args.output_dir / inp["case_id"]
            result = invoke(settings, directory, JudgeResult, JUDGE_PROMPT, packet,
                            "Evidence judge — " + inp["case_id"])
            validate_judgments(result, packet)
            completed.append({"case_id": inp["case_id"], "directory": str(directory.relative_to(ROOT))
                              if directory.is_absolute() else str(directory),
                              "source_directory": str(source.relative_to(ROOT)),
                              "verdicts": [j.verdict for j in result.judgments]})
            (args.output_dir / "manifest.json").write_text(json.dumps(completed, indent=2) + "\n")
            print(inp["case_id"], [j.verdict for j in result.judgments], flush=True)
    else:
        if args.judge_run is None:
            parser.error("--judge-run is required for diagnosis")
        traces = []
        for item in manifest:
            source = ROOT / item["directory"]
            calls = read(source / "calls.json")
            traces.append({
                "case_id": item["case_id"],
                "question": read(source / "input.json")["question"],
                "actual_delivered_instructions": calls[0]["instructions"],
                "actual_delivered_prompt": calls[0]["prompt"],
                "raw_target_observations": [o for o in read(source / "dossier.json")["fundamentals"]["observations"] if o["code"] in FOCUS_FIELDS],
                "final_target_facts": [f for f in read(source / "actual.json")["facts"] if f["field"] in FOCUS_FIELDS],
                "judge_assessment": read(args.judge_run / item["case_id"] / "response.json"),
            })
        packet = {"traces": traces, "wire_schema": FundamentalsExtraction.model_json_schema(),
                  "extraction_instructions_code": inspect.getsource(agents._fundamentals_extraction_instructions),
                  "final_selection_code": inspect.getsource(agents._build_fundamentals_block)}
        result = invoke(settings, args.output_dir, PromptDiagnosis, DIAGNOSIS_PROMPT, packet,
                        "ROE ROCE CFO prompt diagnosis")
        print(json.dumps(result.model_dump(mode="json"), indent=2), flush=True)


if __name__ == "__main__":
    main()
