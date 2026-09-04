"""End-to-end graph test that needs no API key and no copyrighted test fixture."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from competitive_scoring.agents import RuntimeServices
from competitive_scoring.book_policy import BookScoringAgent
from competitive_scoring.config import Settings
from competitive_scoring.graph import build_workflow
from competitive_scoring.rag import RetrievedChunk


class FakeBookKnowledgeBase:
    """Behave like the real local RAG boundary without embedding the private book."""

    def ensure_index(self, *, force: bool = False) -> dict[str, int | str]:
        del force
        return {"status": "ready", "pages": 271, "chunks": 6, "document_hash": "test-hash"}

    def document_hash(self) -> str:
        return "test-hash"

    def retrieve(
        self,
        query: str,
        *,
        printed_ranges: tuple[tuple[int, int], ...],
        per_range: int = 2,
    ) -> list[RetrievedChunk]:
        del query, per_range
        printed_page = printed_ranges[0][0]
        return [
            RetrievedChunk(
                chunk_id=f"test-p{printed_page}",
                document="Private test placeholder; the real book text is not in the repository.",
                printed_page=printed_page,
                pdf_page=printed_page + 1,
                chapter="Test-resolved chapter",
                distance=0.1,
            )
        ]


class EmptyBookKnowledgeBase(FakeBookKnowledgeBase):
    """Simulate a broken index so the audit-failure path is exercised."""

    def retrieve(
        self,
        query: str,
        *,
        printed_ranges: tuple[tuple[int, int], ...],
        per_range: int = 2,
    ) -> list[RetrievedChunk]:
        del query, printed_ranges, per_range
        return []


def test_demo_graph_fans_out_joins_and_finishes() -> None:
    settings = Settings(
        mode="demo",
        research_as_of=date(2026, 9, 2),
        book_pdf_path=Path("private-book-not-needed-by-this-test.pdf"),
    )
    book_agent = BookScoringAgent(FakeBookKnowledgeBase(), as_of_date=settings.research_as_of)  # type: ignore[arg-type]
    workflow = build_workflow(RuntimeServices(settings=settings, book_agent=book_agent))

    result = workflow.invoke(
        {"company_reports": {}, "book_scores": {}, "trace": [], "retry_count": 0},
        config={"recursion_limit": 50},
    )

    assert set(result["company_reports"]) == {"PNGJL", "KALYANKJIL", "SENCO", "THANGAMAYL"}
    assert set(result["book_scores"]) == set(result["company_reports"])
    assert result["audit"].passed is True
    assert len(result["final"].companies) == 4
    assert all(item.final_score is not None for item in result["final"].companies)

    trace_details = [event.detail for event in result["trace"]]
    company_join = trace_details.index("JOIN ALL: every company valuation is complete.")
    book_prepare = next(
        index
        for index, detail in enumerate(trace_details)
        if detail.startswith("Private local index ready")
    )
    book_join = trace_details.index("JOIN ALL book scores completed.")
    audit_passed = trace_details.index("Passed.")
    assert company_join < book_prepare < book_join < audit_passed


def test_every_book_citation_keeps_both_page_conventions() -> None:
    settings = Settings(mode="demo")
    fake = FakeBookKnowledgeBase()
    agent = BookScoringAgent(fake, as_of_date=settings.research_as_of)  # type: ignore[arg-type]

    # Reuse the graph to construct a fully typed company report before scoring.
    workflow = build_workflow(RuntimeServices(settings=settings, book_agent=agent))
    result = workflow.invoke(
        {"company_reports": {}, "book_scores": {}, "trace": [], "retry_count": 0}
    )
    for score in result["book_scores"].values():
        for category in score.categories:
            assert category.book_basis
            assert all(
                citation.pdf_page == citation.printed_page + 1 for citation in category.book_basis
            )


def test_failed_audit_never_declares_a_winner() -> None:
    settings = Settings(mode="demo", max_audit_retries=1)
    book_agent = BookScoringAgent(
        EmptyBookKnowledgeBase(),  # type: ignore[arg-type]
        as_of_date=settings.research_as_of,
    )
    result = build_workflow(RuntimeServices(settings=settings, book_agent=book_agent)).invoke(
        {"company_reports": {}, "book_scores": {}, "trace": [], "retry_count": 0}
    )

    briefing = result["final"]
    assert briefing.audit.passed is False
    assert all(item.rank is None and item.final_score is None for item in briefing.companies)
    assert briefing.conclusion.startswith("No validated ranking was issued")
