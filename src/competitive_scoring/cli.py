"""Command-line entry points for indexing, running, and inspecting the graph."""

from __future__ import annotations

import argparse
import json
from dataclasses import replace
from pathlib import Path

from .config import Settings
from .graph import build_workflow, create_runtime, run_research
from .publication import PublicationValidationError, require_publishable
from .rag import BookKnowledgeBase
from .tracing import span, trace_run


def _settings(mode: str, book: str | None = None) -> Settings:
    settings = Settings.from_env(mode=mode)  # type: ignore[arg-type]
    if book:
        settings = replace(settings, book_pdf_path=Path(book).expanduser().resolve())
    return settings


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="competitive-scoring",
        description="Run the PNGJL competitive investment-scoring agents.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    index_parser = subparsers.add_parser("ingest-book", help="Build the private local RAG index.")
    index_parser.add_argument("--pdf", help="Path to your licensed Peaceful Investing PDF.")
    index_parser.add_argument(
        "--force", action="store_true", help="Rebuild even if the hash matches."
    )

    run_parser = subparsers.add_parser("run", help="Run the complete LangGraph workflow.")
    run_parser.add_argument("--mode", choices=("demo", "live"), default="demo")
    run_parser.add_argument("--pdf", help="Override BOOK_PDF_PATH for this run.")
    run_parser.add_argument("--output", default="outputs/briefing.md")

    graph_parser = subparsers.add_parser("graph", help="Print the generated Mermaid architecture.")
    graph_parser.add_argument("--mode", choices=("demo", "live"), default="demo")

    args = parser.parse_args()
    settings = _settings(getattr(args, "mode", "demo"), getattr(args, "pdf", None))

    if args.command == "ingest-book":
        knowledge_base = BookKnowledgeBase(
            pdf_path=settings.book_pdf_path,
            index_dir=settings.book_index_dir,
        )
        result = knowledge_base.ensure_index(force=args.force)
        print(json.dumps(result, indent=2))
        return

    if args.command == "graph":
        graph = build_workflow(create_runtime(settings))
        print(graph.get_graph().draw_mermaid())
        return

    with trace_run(settings) as diagnostics:
        try:
            _run_and_export(settings, args.output, diagnostics)
        finally:
            print(f"Run diagnostics: {diagnostics.directory}")


def _run_and_export(settings: Settings, output_path: str, diagnostics) -> None:
    briefing, trace = run_research(settings)
    # The CLI and Streamlit share the same final safety boundary.  Validate
    # before creating a directory or writing either output file, so a failed
    # draft cannot be mistaken for a completed briefing by another tool.
    try:
        require_publishable(briefing, tuple(trace))
    except PublicationValidationError as exc:
        diagnostics.status = "unvalidated"
        print(f"Report was not written: {exc}")
        raise SystemExit(2) from exc

    output = Path(output_path).expanduser().resolve()
    with span("report.export"):
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(briefing.markdown, encoding="utf-8")
        json_path = output.with_suffix(".json")
        json_path.write_text(briefing.model_dump_json(indent=2), encoding="utf-8")
    print(f"Wrote {output}")
    print(f"Wrote {json_path}")
    print(f"Completed {len(trace)} trace events; audit passed={briefing.audit.passed}")
    diagnostics.status = "succeeded"


if __name__ == "__main__":
    main()
