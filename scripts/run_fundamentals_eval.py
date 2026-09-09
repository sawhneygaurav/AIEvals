"""Run one accepted case through the existing production agent, then grade offline.

No retrieval, book data, publication, or application score-policy changes. The
runner passes the question and requested identities to the production agent.
The answer key is evaluator-only. Saved source hashes identify the tested code.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import time
from dataclasses import replace
from datetime import UTC, date, datetime
from pathlib import Path

from competitive_scoring.agents import (
    RuntimeServices,
    analyze_fundamentals,
    extract_company_dossier,
)
from competitive_scoring.config import Settings
from competitive_scoring.evaluation.fundamentals import (
    adapt_production_metrics,
    fingerprint,
    grade_execution,
)
from competitive_scoring.llm import StructuredLLM
from competitive_scoring.models import CompanyIdentity, EvidenceItem, FundamentalsRequest

ROOT = Path(__file__).resolve().parents[1]
SUITE = ROOT / "evals/fundamentals"


def read_cases(path: Path) -> dict:
    return {r["case_id"]: r for r in map(json.loads, path.read_text().splitlines())}


class RecordedLLM:
    """Persist the exact prompt and response of each logical call for diagnosis."""

    def __init__(self, llm: StructuredLLM, directory: Path):
        self.llm = llm
        self.directory = directory
        self.calls: list[dict] = []

    def generate(self, **kwargs):
        record = {k: v for k, v in kwargs.items() if k != "schema"}
        record["schema"] = kwargs["schema"].__name__
        record["schema_sha256"] = fingerprint(kwargs["schema"].model_json_schema())
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
            (self.directory / "calls.json").write_text(
                json.dumps(self.calls, indent=2, ensure_ascii=False) + "\n"
            )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case-id", default="FUND-PNGJL-FY2026-001")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--regrade", action="store_true", help="Grade saved output; no model call.")
    parser.add_argument("--evidence-review", type=Path)
    parser.add_argument("--request-spec", type=Path, default=SUITE / "suite_requests.jsonl")
    args = parser.parse_args()
    inp = read_cases(SUITE / "suite_inputs.jsonl")[args.case_id]
    directory = args.output_dir.resolve()
    if args.regrade:
        saved_input = json.loads((directory / "input.json").read_text())
        actual = json.loads((directory / "actual.json").read_text())
        ref = json.loads((directory / "reference.json").read_text())
        labels = json.loads(args.evidence_review.read_text()) if args.evidence_review else None
        metadata = json.loads((directory / "metadata.json").read_text())
        result = grade_execution(saved_input, ref, actual, metadata["diagnostics"], labels)
        (directory / "grade.json").write_text(json.dumps(result, indent=2) + "\n")
        print(json.dumps(result, indent=2))
        return
    # Inspect status before dispatch; only the input object is passed to the agent.
    ref = read_cases(SUITE / "suite_references.jsonl")[args.case_id]
    if ref["review_status"] != "approved_for_pilot":
        raise SystemExit("This case is still a draft; review its answer key before a live run.")
    if directory.exists():
        raise SystemExit("Output directory already exists; use a new path or --regrade.")
    settings = replace(
        Settings.from_env(mode="live"), research_as_of=date.fromisoformat(inp["research_cutoff"])
    )
    scope = read_cases(args.request_spec)[args.case_id]
    request = FundamentalsRequest(company_ticker=inp["company"]["ticker"],
                                  question=inp["question"], slots=scope["slots"])
    if not settings.llm_api_key or not settings.llm_model:
        raise SystemExit("The configured model and API credential are required.")
    directory.mkdir(parents=True)
    (directory / "input.json").write_text(json.dumps(inp, indent=2, ensure_ascii=False) + "\n")
    (directory / "reference.json").write_text(json.dumps(ref, indent=2) + "\n")
    (directory / "request.json").write_text(request.model_dump_json(indent=2) + "\n")
    source_pdf_sha256 = None
    if ref.get("source_manifest"):
        source_manifest = json.loads((SUITE / ref["source_manifest"]).read_text())["sources"][0]
        pdf = ROOT / source_manifest["local_path_relative_to_repository"]
        source_pdf_sha256 = hashlib.sha256(pdf.read_bytes()).hexdigest()
        if source_pdf_sha256 != source_manifest["sha256"]:
            raise SystemExit("Saved source fingerprint changed. Re-verify the source first.")
    synthetic = ref["data_kind"] == "synthetic"
    llm = RecordedLLM(
        StructuredLLM(
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
        ),
        directory,
    )
    runtime = RuntimeServices(settings=settings, book_agent=None, llm=llm)  # type: ignore[arg-type]
    company = CompanyIdentity(**inp["company"])
    now = datetime.now(UTC)
    sources = [
        EvidenceItem(
            source_id=s["source_id"],
            company_ticker=company.ticker,
            title=s["title"],
            url=s["url"],
            publisher="Synthetic evaluation fixture" if synthetic else company.name,
            source_type="user_upload" if synthetic else "official_document",
            accessed_at=now,
            excerpt=s["excerpt"],
            tags=["official", "fundamentals"],
            authority="secondary" if synthetic else "primary_company",
        )
        for s in inp["evidence"]
    ]
    start = time.monotonic()
    print(f"Running {args.case_id} with {settings.llm_provider}/{settings.llm_model}", flush=True)
    dossier = extract_company_dossier(runtime, company, sources, fundamentals_request=request)
    print("Dossier returned; applying existing Fundamentals post-processing.", flush=True)
    block = analyze_fundamentals(runtime, company, sources, dossier=dossier, request=request)
    actual = adapt_production_metrics(inp["case_id"], company.ticker, block.requested_facts)
    metadata = {
        "started_at": now.isoformat(),
        "elapsed_seconds": time.monotonic() - start,
        "provider": settings.llm_provider,
        "model": settings.llm_model,
        "api_style": settings.llm_api_style,
        "reasoning_effort": settings.llm_reasoning_effort,
        "logical_calls": len(llm.calls),
        "max_attempts_per_call": 1,
        "max_tokens": settings.llm_max_tokens,
        "request_timeout_seconds": settings.llm_request_timeout_seconds,
        "input_sha256": fingerprint(inp),
        "request_sha256": fingerprint(request.model_dump(mode="json")),
        "reference_sha256": fingerprint(ref),
        "source_pdf_sha256": source_pdf_sha256,
        "actual_sha256": fingerprint(actual),
        "production_source_sha256": {
            p: hashlib.sha256((ROOT / p).read_bytes()).hexdigest()
            for p in [
                "src/competitive_scoring/agents.py",
                "src/competitive_scoring/models.py",
                "src/competitive_scoring/llm.py",
            ]
        },
        "baseline_scope": "Production dossier with the original question and caller-authored requested identities, followed by per-identity financial answer selection. Current-score metrics stay separate. Frozen text excerpts; no retrieval, PDF parsing, full graph or UI run. Combined prompt/request/schema/selection change, not a prompt-only experiment.",
        "adapter": "Select requested output codes; map unit display aliases only. Do not infer, convert or fill missing values.",
        "diagnostics": [
            {"code": d.code, "message": d.message, "ticker": d.ticker}
            for d in runtime.diagnostics_snapshot()
        ],
    }
    result = grade_execution(inp, ref, actual, metadata["diagnostics"])
    for name, content in [
        ("actual", actual),
        ("reference", ref),
        ("grade", result),
        ("metadata", metadata),
        ("dossier", dossier.model_dump(mode="json")),
        ("fundamentals", block.model_dump(mode="json")),
    ]:
        (directory / f"{name}.json").write_text(
            json.dumps(content, indent=2, ensure_ascii=False) + "\n"
        )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
