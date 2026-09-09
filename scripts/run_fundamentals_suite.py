"""Run every approved Fundamentals case once and preserve per-case artifacts."""
import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=False)
    refs = [json.loads(line) for line in (ROOT / "evals/fundamentals/suite_references.jsonl").read_text().splitlines()]
    manifest = []
    for ref in refs:
        if ref["review_status"] != "approved_for_pilot":
            continue
        case_id = ref["case_id"]
        directory = output / case_id
        print("Running", case_id, flush=True)
        completed = subprocess.run([sys.executable, str(ROOT / "scripts/run_fundamentals_eval.py"),
                                    "--case-id", case_id, "--output-dir", str(directory)],
                                   cwd=ROOT, capture_output=True, text=True)
        (output / f"{case_id}.log").write_text(completed.stdout + completed.stderr)
        if completed.returncode:
            raise SystemExit(f"{case_id} did not complete; inspect the saved log. Successful cases remain saved.")
        grade = json.loads((directory / "grade.json").read_text())
        manifest.append({"case_id": case_id, "directory": str(directory.relative_to(ROOT))})
        (output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
        print(case_id, grade["status"], flush=True)


if __name__ == "__main__":
    main()
