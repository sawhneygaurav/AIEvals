from competitive_scoring.agents import _technical_score
from competitive_scoring.models import AnalysisBlock
from competitive_scoring.scoring import calculate_core_score


def block(agent: str, score: float, *, growth: float | None = None) -> AnalysisBlock:
    return AnalysisBlock(agent=agent, score=score, growth_score=growth, summary="test")


def test_core_score_uses_the_agreed_fixed_weights() -> None:
    score = calculate_core_score(
        block("fundamentals", 80, growth=70),
        block("valuation", 60),
        block("management", 90),
        block("technicals", 50),
    )
    # 80*25% + 70*20% + 60*25% + 90*20% + 50*10% = 72
    assert score == 72.0


def test_missing_growth_does_not_turn_into_an_invented_neutral_score() -> None:
    score = calculate_core_score(
        block("fundamentals", 80),
        block("valuation", 60),
        block("management", 90),
        block("technicals", 50),
    )
    assert score is None


def test_insufficient_evidence_block_makes_company_unscorable() -> None:
    missing = AnalysisBlock(
        agent="management",
        status="insufficient_evidence",
        score=None,
        summary="No dated governance evidence.",
    )
    score = calculate_core_score(
        block("fundamentals", 80, growth=70),
        block("valuation", 60),
        missing,
        block("technicals", 50),
    )
    assert score is None


def test_flat_market_is_neutral_instead_of_bullish() -> None:
    assert _technical_score(close=100, sma20=100, sma50=100, rsi14=50) == 50
