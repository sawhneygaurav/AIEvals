"""Deterministic score formulas.

LLMs collect and explain evidence, but they do not get to invent the weighting
formula.  Keeping the arithmetic here makes every result reproducible and easy
to audit.
"""

from __future__ import annotations

from .models import AnalysisBlock, BookScore, ScoredCompany

# These are the weights agreed for a 2-3 year comparison.  Technicals stay small
# because they help with entry timing, not the long-term business thesis.
CORE_WEIGHTS = {
    "fundamentals": 0.25,
    "growth": 0.20,
    "valuation": 0.25,
    "management": 0.20,
    "technicals": 0.10,
}

FINAL_WEIGHTS = {"core": 0.80, "book": 0.20}


def calculate_core_score(
    fundamentals: AnalysisBlock,
    valuation: AnalysisBlock,
    management: AnalysisBlock,
    technicals: AnalysisBlock,
) -> float | None:
    """Calculate Stage 2's score, or return ``None`` when evidence is incomplete.

    This is an important guardrail: an unknown value is *not* the same as an
    average value.  If one required analyst could not support a score, the
    company remains visible in the report but is excluded from the ranking.
    """

    required_blocks = (fundamentals, valuation, management, technicals)
    if any(block.status != "complete" or block.score is None for block in required_blocks):
        return None
    if fundamentals.growth_score is None:
        return None

    # Pydantic's status checks above establish that these values are numeric.
    growth = fundamentals.growth_score

    result = (
        fundamentals.score * CORE_WEIGHTS["fundamentals"]
        + growth * CORE_WEIGHTS["growth"]
        + valuation.score * CORE_WEIGHTS["valuation"]
        + management.score * CORE_WEIGHTS["management"]
        + technicals.score * CORE_WEIGHTS["technicals"]
    )
    return round(result, 2)


def research_confidence(blocks: list[AnalysisBlock], *, source_count: int) -> float:
    """Average agent confidence; missing sources prevent an unrealistically high value."""

    if not blocks:
        return 0.0
    average = sum(block.confidence for block in blocks) / len(blocks)
    if source_count == 0:
        average *= 0.5
    return round(max(0.0, min(1.0, average)), 3)


def calculate_final_score(core_score: float | None, book_score: BookScore) -> float | None:
    """Blend the evidence score with the book-alignment component."""

    if (
        core_score is None
        or book_score.total_score is None
        or book_score.decision_status != "complete"
    ):
        return None
    return round(
        core_score * FINAL_WEIGHTS["core"] + book_score.total_score * FINAL_WEIGHTS["book"],
        2,
    )


def rank_companies(companies: list[ScoredCompany]) -> list[ScoredCompany]:
    """Rank deterministically; unavailable scores sort last."""

    available = [item for item in companies if item.final_score is not None]
    unavailable = [item for item in companies if item.final_score is None]
    available.sort(
        key=lambda item: (
            -(item.final_score or 0),
            -item.research.confidence,
            item.research.company.ticker,
        )
    )
    unavailable.sort(key=lambda item: item.research.company.ticker)
    ranked = [item.model_copy(update={"rank": index}) for index, item in enumerate(available, 1)]
    # An unavailable company is shown for transparency but is explicitly unranked.
    return ranked + [item.model_copy(update={"rank": None}) for item in unavailable]
