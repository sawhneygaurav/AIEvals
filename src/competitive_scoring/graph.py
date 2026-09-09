"""LangGraph orchestration for the competitive-scoring workflow.

Read this file from top to bottom to see the architecture expressed as code.
Nodes are agents, edges are dependencies, and `Send` creates the parallel work.
"""

from __future__ import annotations

import operator
from datetime import UTC, datetime
from typing import Annotated, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

from .agents import (
    RuntimeServices,
    analyze_business,
    analyze_fundamentals,
    analyze_management,
    analyze_technicals,
    analyze_valuation,
    assemble_company_report,
    audit_results,
    combine_scores,
    discover_peers,
    extract_company_dossier,
    gather_sources,
    score_with_book,
)
from .book_policy import BookScoringAgent
from .config import Settings
from .llm import StructuredLLM
from .models import (
    AnalysisBlock,
    AuditResult,
    BookScore,
    CompanyDossierExtraction,
    CompanyIdentity,
    CompanyResearch,
    EvidenceItem,
    FinalBriefing,
    TraceEvent,
)
from .progress import ProgressCallback, ProgressEvent
from .rag import BookKnowledgeBase
from .report import render_markdown
from .scoring import rank_companies
from .tools.free_sources import FreeSourceCollector
from .tools.market_data import YahooChartClient
from .tools.you_search import YouSearchClient
from .tracing import event, span, trace_run, traced_node


def _merge_by_ticker(left: dict | None, right: dict | None) -> dict:
    """LangGraph reducer used when parallel workers update the same state field."""

    merged = dict(left or {})
    merged.update(right or {})
    return merged


class CompanyGraphState(TypedDict, total=False):
    """State visible inside one company's Stage 2 subgraph."""

    company: CompanyIdentity
    sources: list[EvidenceItem]
    dossier: CompanyDossierExtraction
    business: AnalysisBlock
    fundamentals: AnalysisBlock
    management: AnalysisBlock
    technicals: AnalysisBlock
    valuation: AnalysisBlock
    report: CompanyResearch


class WorkflowState(TypedDict, total=False):
    """Shared state for the top-level graph."""

    target: CompanyIdentity
    peers: list[CompanyIdentity]
    companies: list[CompanyIdentity]
    company: CompanyIdentity  # temporary field received by a Send worker
    report: CompanyResearch  # temporary field received by a book Send worker
    company_reports: Annotated[dict[str, CompanyResearch], _merge_by_ticker]
    book_scores: Annotated[dict[str, BookScore], _merge_by_ticker]
    audit: AuditResult
    retry_count: int
    final: FinalBriefing
    trace: Annotated[list[TraceEvent], operator.add]


def _trace(stage: str, agent: str, detail: str, status: str = "completed") -> TraceEvent:
    return TraceEvent(
        stage=stage,
        agent=agent,
        status=status,  # type: ignore[arg-type]
        detail=detail,
        timestamp=datetime.now(UTC),
    )


def _progress(
    runtime: RuntimeServices,
    *,
    key: str,
    stage: str,
    message: str,
    status: str,
    ticker: str | None = None,
) -> None:
    """Notify an optional UI observer without coupling the graph to Streamlit."""

    if runtime.progress_callback is None:
        return
    runtime.progress_callback(
        ProgressEvent(
            key=key,
            stage=stage,
            message=message,
            status=status,  # type: ignore[arg-type]
            ticker=ticker,
        )
    )


def build_company_subgraph(runtime: RuntimeServices):
    """Build Stage 2 for one company, including the explicit four-way join."""

    graph = StateGraph(CompanyGraphState)

    def source_node(state: CompanyGraphState) -> dict:
        company = state["company"]
        key = f"source:{company.ticker}"
        _progress(
            runtime,
            key=key,
            stage="Research",
            message=f"Searching and verifying evidence for {company.ticker}…",
            status="started",
            ticker=company.ticker,
        )
        sources = gather_sources(runtime, company)
        _progress(
            runtime,
            key=key,
            stage="Research",
            message=f"Evidence ready for {company.ticker}.",
            status="completed",
            ticker=company.ticker,
        )
        return {"sources": sources}

    def dossier_node(state: CompanyGraphState) -> dict:
        company = state["company"]
        _progress(
            runtime,
            key=f"dossier:{company.ticker}",
            stage="Shared evidence extraction",
            message=f"Kimi is extracting one validated dossier for {company.ticker}…",
            status="started",
            ticker=company.ticker,
        )
        dossier = extract_company_dossier(runtime, company, state["sources"])
        _progress(
            runtime,
            key=f"dossier:{company.ticker}",
            stage="Shared evidence extraction",
            message=f"Validated dossier ready for {company.ticker}.",
            status="completed",
            ticker=company.ticker,
        )
        return {"dossier": dossier}

    def business_node(state: CompanyGraphState) -> dict:
        company = state["company"]
        key = f"business:{company.ticker}"
        _progress(
            runtime,
            key=key,
            stage="Parallel analysis",
            message=f"Business Agent is analysing {company.ticker}…",
            status="started",
            ticker=company.ticker,
        )
        result = analyze_business(runtime, company, state["sources"], state["dossier"])
        _progress(
            runtime,
            key=key,
            stage="Parallel analysis",
            message=f"Business analysis completed for {company.ticker}.",
            status="completed",
            ticker=company.ticker,
        )
        return {"business": result}

    def fundamentals_node(state: CompanyGraphState) -> dict:
        company = state["company"]
        key = f"fundamentals:{company.ticker}"
        _progress(
            runtime,
            key=key,
            stage="Parallel analysis",
            message=f"Fundamentals Agent is analysing {company.ticker}…",
            status="started",
            ticker=company.ticker,
        )
        result = analyze_fundamentals(runtime, company, state["sources"], state["dossier"])
        _progress(
            runtime,
            key=key,
            stage="Parallel analysis",
            message=f"Fundamentals analysis completed for {company.ticker}.",
            status="completed",
            ticker=company.ticker,
        )
        return {"fundamentals": result}

    def management_node(state: CompanyGraphState) -> dict:
        company = state["company"]
        key = f"management:{company.ticker}"
        _progress(
            runtime,
            key=key,
            stage="Parallel analysis",
            message=f"Management Agent is analysing {company.ticker}…",
            status="started",
            ticker=company.ticker,
        )
        result = analyze_management(runtime, company, state["sources"], state["dossier"])
        _progress(
            runtime,
            key=key,
            stage="Parallel analysis",
            message=f"Management analysis completed for {company.ticker}.",
            status="completed",
            ticker=company.ticker,
        )
        return {"management": result}

    def technicals_node(state: CompanyGraphState) -> dict:
        company = state["company"]
        key = f"technicals:{company.ticker}"
        _progress(
            runtime,
            key=key,
            stage="Parallel analysis",
            message=f"Calculating technical indicators for {company.ticker}…",
            status="started",
            ticker=company.ticker,
        )
        result = analyze_technicals(runtime, company, state["sources"])
        _progress(
            runtime,
            key=key,
            stage="Parallel analysis",
            message=f"Technicals completed for {company.ticker}.",
            status="completed",
            ticker=company.ticker,
        )
        return {"technicals": result}

    def valuation_node(state: CompanyGraphState) -> dict:
        # If any of these four keys is absent, Python raises immediately.  That is
        # deliberate proof that Valuation cannot run after Fundamentals alone.
        company = state["company"]
        key = f"valuation:{company.ticker}"
        _progress(
            runtime,
            key=key,
            stage="Valuation",
            message=f"JOIN ALL reached; valuing {company.ticker}…",
            status="started",
            ticker=company.ticker,
        )
        result = analyze_valuation(
            runtime,
            company,
            state["sources"],
            state["business"],
            state["fundamentals"],
            state["management"],
            state["technicals"],
            state["dossier"],
        )
        _progress(
            runtime,
            key=key,
            stage="Valuation",
            message=f"Valuation completed for {company.ticker}.",
            status="completed",
            ticker=company.ticker,
        )
        return {"valuation": result}

    def assemble_node(state: CompanyGraphState) -> dict:
        return {
            "report": assemble_company_report(
                state["company"],
                state["sources"],
                state["business"],
                state["fundamentals"],
                state["management"],
                state["technicals"],
                state["valuation"],
            )
        }

    graph.add_node("source_research_agent", traced_node("graph.source_research_agent", source_node))
    graph.add_node("company_dossier_extractor", traced_node("graph.company_dossier_extractor", dossier_node))
    graph.add_node("business_agent", traced_node("graph.business_agent", business_node))
    graph.add_node("fundamentals_agent", traced_node("graph.fundamentals_agent", fundamentals_node))
    graph.add_node("management_agent", traced_node("graph.management_agent", management_node))
    graph.add_node("technicals_agent", traced_node("graph.technicals_agent", technicals_node))
    graph.add_node("valuation_agent", traced_node("graph.valuation_agent", valuation_node))
    graph.add_node("assemble_company", traced_node("graph.assemble_company", assemble_node))

    graph.add_edge(START, "source_research_agent")

    # Source collection fans out into one Kimi dossier and local technicals.
    # Across the top-level Send workers, all four company dossiers run in one
    # provider wave. Business/Fundamentals/Management then consume their own
    # slice without making three more model calls.
    graph.add_edge("source_research_agent", "company_dossier_extractor")
    graph.add_edge("source_research_agent", "technicals_agent")

    for analyst in (
        "business_agent",
        "fundamentals_agent",
        "management_agent",
    ):
        graph.add_edge("company_dossier_extractor", analyst)

    # List-valued start nodes mean "wait for every one of these nodes".  This is
    # the four-way synchronization barrier requested in the architecture.
    graph.add_edge(
        ["business_agent", "fundamentals_agent", "management_agent", "technicals_agent"],
        "valuation_agent",
    )
    graph.add_edge("valuation_agent", "assemble_company")
    graph.add_edge("assemble_company", END)
    return graph.compile()


def build_workflow(runtime: RuntimeServices):
    """Build the complete four-stage graph."""

    company_graph = build_company_subgraph(runtime)
    graph = StateGraph(WorkflowState)

    def orchestrator_intake(state: WorkflowState) -> dict:
        settings = runtime.settings
        target = CompanyIdentity(
            name=settings.target_name,
            ticker=settings.target_symbol,
            comparison_reason="User-selected target company.",
        )
        result = {
            "target": target,
            "retry_count": 0,
            "company_reports": {},
            "book_scores": {},
            "trace": [
                _trace(
                    "Stage 1",
                    "Orchestrator Agent",
                    f"Scoped {target.ticker} for a {settings.horizon} comparison.",
                )
            ],
        }
        _progress(
            runtime,
            key="setup",
            stage="Setup",
            message="Research scope and runtime checks completed.",
            status="completed",
        )
        return result

    def peer_discovery_node(state: WorkflowState) -> dict:
        peers = discover_peers(runtime)
        companies = [state["target"], *peers]
        result = {
            "peers": peers,
            "companies": companies,
            "trace": [
                _trace(
                    "Stage 1",
                    "Fixed Peer Selection Agent",
                    "Applied the vetted NSE peer policy: "
                    + ", ".join(company.ticker for company in peers),
                )
            ],
        }
        _progress(
            runtime,
            key="peers",
            stage="Setup",
            message="PNGJL and three fixed NSE peers are ready.",
            status="completed",
        )
        return result

    def send_company_workers(state: WorkflowState):
        # One Send per company is LangGraph's map step.  All four subgraphs may run
        # concurrently; each one also contains its own four-agent parallel step.
        return [Send("company_worker", {"company": company}) for company in state["companies"]]

    def company_worker(state: WorkflowState) -> dict:
        company = state["company"]
        result = company_graph.invoke(
            {"company": company},
            config={"max_concurrency": runtime.settings.max_workers},
        )
        report = result["report"]
        return {
            "company_reports": {company.ticker: report},
            "trace": [
                _trace(
                    "Stage 2",
                    f"{company.ticker} research subgraph",
                    "You.com primary → Indian-source verification → one shared Kimi dossier "
                    "+ local technicals → three logical specialists → JOIN ALL → deterministic "
                    "Valuation completed.",
                )
            ],
        }

    def company_join(state: WorkflowState) -> dict:
        expected = {company.ticker for company in state["companies"]}
        actual = set(state["company_reports"])
        if actual != expected:
            raise RuntimeError(f"Company join expected {sorted(expected)}, got {sorted(actual)}.")
        return {
            "trace": [
                _trace(
                    "Stage 2",
                    "Orchestrator Agent",
                    "JOIN ALL: every company valuation is complete.",
                )
            ]
        }

    def prepare_book_node(state: WorkflowState) -> dict:
        details = runtime.book_agent.knowledge_base.ensure_index()
        return {
            "trace": [
                _trace(
                    "Stage 3",
                    "Book RAG Agent",
                    f"Private local index ready: {details['pages']} pages / {details['chunks']} chunks.",
                )
            ]
        }

    def send_book_workers(state: WorkflowState):
        # Stage 3 begins only after company_join and prepare_book_node.
        ordered = [state["company_reports"][item.ticker] for item in state["companies"]]
        return [Send("book_worker", {"report": report}) for report in ordered]

    def book_worker(state: WorkflowState) -> dict:
        report = state["report"]
        key = f"book:{report.company.ticker}"
        _progress(
            runtime,
            key=key,
            stage="Book RAG scoring",
            message=f"Applying Peaceful Investing policy to {report.company.ticker}…",
            status="started",
            ticker=report.company.ticker,
        )
        score = score_with_book(runtime, report)
        _progress(
            runtime,
            key=key,
            stage="Book RAG scoring",
            message=f"Book score completed for {report.company.ticker}.",
            status="completed",
            ticker=report.company.ticker,
        )
        return {
            "book_scores": {report.company.ticker: score},
            "trace": [
                _trace(
                    "Stage 3",
                    f"Book RAG Scorer — {report.company.ticker}",
                    f"Coverage {score.coverage_weight}%; score {score.total_score}.",
                )
            ],
        }

    def book_join(state: WorkflowState) -> dict:
        expected = set(state["company_reports"])
        actual = set(state["book_scores"])
        if actual != expected:
            raise RuntimeError(f"Book join expected {sorted(expected)}, got {sorted(actual)}.")
        return {
            "trace": [_trace("Stage 3", "Orchestrator Agent", "JOIN ALL book scores completed.")]
        }

    def auditor_node(state: WorkflowState) -> dict:
        result = audit_results(
            state["company_reports"],
            state["book_scores"],
            retry_count=state.get("retry_count", 0),
            mode=runtime.settings.mode,
            runtime_diagnostics=runtime.diagnostics_snapshot(),
        )
        event("audit_result", passed=result.passed, finding_count=len(result.findings))
        for finding in result.findings:
            event("audit.finding", code=finding.code, ticker=finding.company_ticker)
        output = {
            "audit": result,
            "trace": [
                _trace(
                    "Stage 3",
                    "Evidence Auditor Agent",
                    "Passed." if result.passed else f"Found {len(result.findings)} issue(s).",
                    "completed" if result.passed else "warning",
                )
            ],
        }
        _progress(
            runtime,
            key="audit",
            stage="Evidence audit",
            message=(
                "Evidence audit passed."
                if result.passed
                else f"Evidence audit completed with {len(result.findings)} finding(s)."
            ),
            status="completed",
        )
        return output

    def route_after_audit(state: WorkflowState) -> str:
        audit = state["audit"]
        if audit.passed:
            return "final_synthesis"
        if not audit.retryable_tickers:
            return "final_synthesis"
        if state.get("retry_count", 0) >= runtime.settings.max_audit_retries:
            return "final_synthesis"
        return "targeted_retry"

    def targeted_retry_node(state: WorkflowState) -> dict:
        # Only book retrieval/citation issues are automatically retried.  Missing
        # company facts require new external evidence and are never hallucinated.
        replacements: dict[str, BookScore] = {}
        for ticker in state["audit"].retryable_tickers:
            with span("book.retry", ticker=ticker, attempt=state.get("retry_count", 0) + 1):
                replacements[ticker] = score_with_book(runtime, state["company_reports"][ticker])
        retry_count = state.get("retry_count", 0) + 1
        return {
            "book_scores": replacements,
            "retry_count": retry_count,
            "trace": [
                _trace(
                    "Stage 3",
                    "Evidence Auditor Agent",
                    f"Targeted book-retrieval retry {retry_count} for {sorted(replacements)}.",
                )
            ],
        }

    def final_synthesis_node(state: WorkflowState) -> dict:
        scored = [
            combine_scores(report, state["book_scores"][ticker])
            for ticker, report in state["company_reports"].items()
        ]
        if state["audit"].passed:
            ranked = rank_companies(scored)
        else:
            # An audit failure never produces a winner.  We retain every
            # company and diagnostic, but clear scores/ranks so an invalid run
            # cannot be mistaken for an investment conclusion.
            ranked = [
                item.model_copy(update={"final_score": None, "rank": None}) for item in scored
            ]
        available = [item for item in ranked if item.final_score is not None]
        if not state["audit"].passed:
            conclusion = (
                "No validated ranking was issued because the evidence audit failed. "
                "Inspect the audit findings, correct the evidence, and rerun the workflow."
            )
        elif available:
            leader = available[0]
            conclusion = (
                f"{leader.research.company.name} ranks first in this "
                f"{'illustrative demo' if runtime.settings.mode == 'demo' else 'evidence-backed run'} "
                f"at {leader.final_score:.2f}/100. Treat the ranking as a research starting "
                "point and inspect confidence, valuation assumptions, and downside triggers."
            )
        else:
            conclusion = (
                "No final ranking was issued because required company or book evidence was "
                "insufficient."
            )

        briefing = FinalBriefing(
            title="PNGJL Competitive Investment Scoring Briefing",
            mode=runtime.settings.mode,
            as_of_date=runtime.settings.research_as_of,
            target=state["target"],
            companies=ranked,
            audit=state["audit"],
            conclusion=conclusion,
            methodology=(
                "Core Score = Fundamentals 25% + Growth 20% + Valuation 25% + "
                "Management 20% + Technicals 10%. Final Score = Core 80% + "
                "Peaceful Investing Book Alignment 20%. Business analysis informs the "
                "thesis and book/valuation assessments but is not double-counted in Core."
            ),
            disclaimer=(
                "Educational research system, not personalized financial advice or a buy/sell "
                "recommendation. Verify all live figures against exchange filings and consult a "
                "qualified adviser. Demo-mode figures are invented."
            ),
        )
        briefing = briefing.model_copy(update={"markdown": render_markdown(briefing)})
        _progress(
            runtime,
            key="final",
            stage="Final report",
            message="The complete briefing has been assembled.",
            status="completed",
        )
        return {
            "final": briefing,
            "trace": [
                _trace(
                    "Stage 4",
                    "Orchestrator Agent",
                    "Validated ranking compiled."
                    if state["audit"].passed and available
                    else "Unranked briefing compiled with audit diagnostics.",
                    "completed" if state["audit"].passed else "warning",
                )
            ],
        }

    graph.add_node("orchestrator_intake", traced_node("graph.orchestrator_intake", orchestrator_intake))
    graph.add_node("peer_discovery", traced_node("graph.peer_discovery", peer_discovery_node))
    graph.add_node("company_worker", traced_node("graph.company_worker", company_worker))
    graph.add_node("company_join", traced_node("graph.company_join", company_join))
    graph.add_node("prepare_book", traced_node("graph.prepare_book", prepare_book_node))
    graph.add_node("book_worker", traced_node("graph.book_worker", book_worker))
    graph.add_node("book_join", traced_node("graph.book_join", book_join))
    graph.add_node("evidence_auditor", traced_node("graph.evidence_auditor", auditor_node))
    graph.add_node("targeted_retry", traced_node("graph.targeted_retry", targeted_retry_node))
    graph.add_node("final_synthesis", traced_node("graph.final_synthesis", final_synthesis_node))

    graph.add_edge(START, "orchestrator_intake")
    graph.add_edge("orchestrator_intake", "peer_discovery")
    graph.add_conditional_edges("peer_discovery", send_company_workers, ["company_worker"])
    graph.add_edge("company_worker", "company_join")
    graph.add_edge("company_join", "prepare_book")
    graph.add_conditional_edges("prepare_book", send_book_workers, ["book_worker"])
    graph.add_edge("book_worker", "book_join")
    graph.add_edge("book_join", "evidence_auditor")
    graph.add_conditional_edges(
        "evidence_auditor",
        route_after_audit,
        {
            "targeted_retry": "targeted_retry",
            "final_synthesis": "final_synthesis",
        },
    )
    graph.add_edge("targeted_retry", "evidence_auditor")
    graph.add_edge("final_synthesis", END)
    return graph.compile()


def create_runtime(
    settings: Settings,
    *,
    progress_callback: ProgressCallback | None = None,
) -> RuntimeServices:
    """Construct adapters once and inject them into all nodes."""

    knowledge_base = BookKnowledgeBase(
        pdf_path=settings.book_pdf_path,
        index_dir=settings.book_index_dir,
    )
    runtime = RuntimeServices(
        settings=settings,
        book_agent=BookScoringAgent(knowledge_base, as_of_date=settings.research_as_of),
        progress_callback=progress_callback,
    )
    if settings.mode == "live":
        # You.com is the broad discovery/news layer. The second collector is a
        # separate trust boundary restricted to hard-coded NSE/company hosts and
        # private user uploads; publisher links remain manual-only.
        runtime.you_search = YouSearchClient(api_key=settings.ydc_api_key)
        runtime.source_collector = FreeSourceCollector()
        runtime.llm = StructuredLLM(
            api_key=settings.llm_api_key or "",
            model=settings.llm_model or "",
            provider=settings.llm_provider,
            base_url=settings.llm_base_url,
            nebius_api_style=settings.llm_api_style,
            max_tokens=settings.llm_max_tokens,
            reasoning_effort=settings.llm_reasoning_effort,
            max_concurrent_requests=settings.llm_max_concurrent_requests,
            max_attempts=settings.llm_max_attempts,
            request_timeout_seconds=settings.llm_request_timeout_seconds,
        )
        runtime.market_data = YahooChartClient()
    return runtime


def run_research(
    settings: Settings,
    *,
    progress_callback: ProgressCallback | None = None,
) -> tuple[FinalBriefing, list[TraceEvent]]:
    """Convenience entry point used by both Streamlit and the CLI."""

    with trace_run(settings) as diagnostics:
        with span("run.preflight"):
            settings.validate_for_run()
        with span("run.runtime_setup"):
            runtime = create_runtime(settings, progress_callback=progress_callback)
            workflow = build_workflow(runtime)
        try:
            with span("run.workflow"):
                result = workflow.invoke(
                    {"company_reports": {}, "book_scores": {}, "trace": [], "retry_count": 0},
                    config={"recursion_limit": 50, "max_concurrency": settings.max_workers},
                )
            diagnostics.status = "completed" if result["final"].audit.passed else "unvalidated"
            return result["final"], result["trace"]
        finally:
            if runtime.source_collector is not None:
                runtime.source_collector.close()
