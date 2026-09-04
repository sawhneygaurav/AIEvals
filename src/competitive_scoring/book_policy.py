"""Fixed, page-cited Peaceful Investing scoring policy.

The book supplies methodology.  Current filings/search evidence supplies company
facts.  This separation is the most important guardrail in Stage 3.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date

from .models import (
    BookCategoryScore,
    BookCitation,
    BookScore,
    CompanyResearch,
    Metric,
)
from .rag import BookKnowledgeBase


@dataclass(frozen=True, slots=True)
class CategorySpec:
    id: str
    label: str
    weight: int
    principle_id: str
    printed_ranges: tuple[tuple[int, int], ...]
    retrieval_query: str
    paraphrase: str


BOOK_CATEGORIES: tuple[CategorySpec, ...] = (
    CategorySpec(
        "financial_strength",
        "Financial strength and consistency",
        20,
        "PI-FIN-01",
        ((43, 51), (182, 186)),
        "sustained sales profit margins debt equity interest coverage current ratio financial analysis checklist",
        "Look for durable growth, stable margins, sensible leverage, liquidity, and adequate interest coverage.",
    ),
    CategorySpec(
        "earnings_quality",
        "Earnings quality and cash conversion",
        20,
        "PI-CASH-01",
        ((49, 59), (162, 171)),
        "cash flow from operations cumulative profit free cash flow working capital accounting warning signs",
        "Compare reported profit with operating cash, free cash flow, and working-capital behavior.",
    ),
    CategorySpec(
        "moat_growth",
        "Business moat and self-sustainable growth",
        20,
        "PI-MOAT-01",
        ((60, 67), (104, 112)),
        "self sustainable growth competitive advantage industry customers suppliers pricing power retained earnings",
        "Reward growth that the business can fund and defend, not growth dependent mainly on debt or accounting effects.",
    ),
    CategorySpec(
        "management",
        "Management integrity and capital allocation",
        20,
        "PI-MGMT-01",
        ((122, 161),),
        "management integrity remuneration related party transactions promoter execution succession capital allocation minority shareholders",
        "Test integrity, minority-shareholder alignment, disclosure, execution, succession, and capital allocation.",
    ),
    CategorySpec(
        "valuation",
        "Valuation and margin of safety",
        15,
        "PI-VAL-01",
        ((68, 103), (162, 171)),
        "valuation price earnings yield government bond yield PEG price sales margin of safety normalized earnings",
        "Require a valuation cushion using normalized earnings, cash generation, growth, and margin-of-safety tests.",
    ),
    CategorySpec(
        "credit_resilience",
        "Credit and downside resilience",
        5,
        "PI-CREDIT-01",
        ((172, 181),),
        "credit rating report liquidity debt covenants rating sensitivities default downside risk",
        "Use rating reports and liquidity/debt sensitivities to judge downside resilience.",
    ),
)


def _as_number(metric: Metric | None) -> float | None:
    if metric is None or metric.value is None:
        return None
    try:
        return float(metric.value)
    except (TypeError, ValueError):
        return None


def _first_metric(metrics: dict[str, Metric], *aliases: str) -> Metric | None:
    for alias in aliases:
        if alias in metrics:
            return metrics[alias]
    return None


def _bounded(value: int) -> int:
    return max(0, min(5, value))


def _source_ids(metrics: list[Metric | None], fallback: list[str]) -> list[str]:
    ids = [source for metric in metrics if metric for source in metric.source_ids]
    return list(dict.fromkeys(ids or fallback))


def _history_cap(score: int, history_years: float | None) -> int:
    # The fixed policy requires long comparable history for a high-conviction 4/5.
    if history_years is None or history_years < 5:
        return min(3, score)
    return score


def _score_financial(report: CompanyResearch) -> tuple[int | None, str, list[str], list[str]]:
    metrics = report.metric_map()
    debt = _first_metric(metrics, "debt_equity", "net_debt_equity", "de_ratio")
    interest = _first_metric(metrics, "interest_coverage")
    current = _first_metric(metrics, "current_ratio")
    history = _first_metric(metrics, "history_years")
    known = [item for item in (debt, interest, current) if _as_number(item) is not None]
    if len(known) < 2:
        return (
            None,
            "Insufficient leverage/liquidity history.",
            ["debt/equity, interest coverage, current ratio"],
            [],
        )

    debt_value, interest_value, current_value = map(_as_number, (debt, interest, current))
    score = 3
    if debt_value is not None:
        score += 1 if debt_value < 0.5 else (-1 if debt_value > 1 else 0)
    if interest_value is not None:
        if interest_value < 1:
            score = 0
        else:
            score += 1 if interest_value >= 5 else (-1 if interest_value < 3 else 0)
    if current_value is not None:
        score += 1 if current_value >= 1.25 else (-1 if current_value < 1 else 0)
    score = _history_cap(_bounded(score), _as_number(history))
    rationale = (
        f"D/E={debt_value if debt_value is not None else 'unknown'}, "
        f"interest cover={interest_value if interest_value is not None else 'unknown'}, "
        f"current ratio={current_value if current_value is not None else 'unknown'}; "
        "the long-history cap is applied where required."
    )
    return (
        score,
        rationale,
        [],
        _source_ids([debt, interest, current, history], report.fundamentals.source_ids),
    )


def _score_cash(report: CompanyResearch) -> tuple[int | None, str, list[str], list[str]]:
    metrics = report.metric_map()
    conversion = _first_metric(metrics, "cfo_pat", "cfo_pat_ratio", "cash_conversion")
    free_cash_flow = _first_metric(metrics, "free_cash_flow", "fcf")
    history = _first_metric(metrics, "history_years")
    conversion_value = _as_number(conversion)
    if conversion_value is None:
        return None, "Cumulative CFO/PAT evidence is missing.", ["cumulative CFO/PAT"], []

    if conversion_value < 0.5:
        score = 1
    elif conversion_value < 0.8:
        score = 2
    elif conversion_value < 1.0:
        score = 3
    elif conversion_value < 1.2:
        score = 4
    else:
        score = 5
    fcf_value = _as_number(free_cash_flow)
    if fcf_value is not None:
        score += 1 if fcf_value > 0 else -1
    # Score 0 is reserved for verified manipulation/qualified accounts or a
    # clearly chronic failure, not a single weak cash-flow observation.
    score = max(1, score)
    score = _history_cap(_bounded(score), _as_number(history))
    return (
        score,
        (
            f"CFO/PAT={conversion_value:.2f}; free cash flow is "
            f"{fcf_value if fcf_value is not None else 'not established'}."
        ),
        [],
        _source_ids([conversion, free_cash_flow, history], report.fundamentals.source_ids),
    )


def _score_moat(report: CompanyResearch) -> tuple[int | None, str, list[str], list[str]]:
    metrics = report.metric_map()
    revenue_growth = _first_metric(metrics, "revenue_growth", "revenue_cagr")
    profit_growth = _first_metric(metrics, "profit_growth", "pat_growth", "pat_cagr")
    ssgr = _first_metric(metrics, "ssgr", "self_sustainable_growth")
    history = _first_metric(metrics, "history_years")
    growth_value = _as_number(revenue_growth)
    profit_value = _as_number(profit_growth)
    if (
        report.business.status != "complete"
        or report.business.score is None
        or report.fundamentals.growth_score is None
        or growth_value is None
        or profit_value is None
    ):
        return None, "Comparable growth evidence is missing.", ["revenue/profit growth"], []

    evidence_score = report.business.score * 0.55 + report.fundamentals.growth_score * 0.45
    score = _bounded(round(evidence_score / 20))
    ssgr_value = _as_number(ssgr)
    if ssgr_value is not None and growth_value is not None:
        score += 1 if ssgr_value >= growth_value else -1
    score = _history_cap(_bounded(score), _as_number(history))
    return (
        score,
        (
            f"Business-quality score={report.business.score:.0f}; revenue growth="
            f"{growth_value if growth_value is not None else 'unknown'}%; profit growth="
            f"{profit_value if profit_value is not None else 'unknown'}%; SSGR="
            f"{ssgr_value if ssgr_value is not None else 'unknown'}%."
        ),
        [],
        _source_ids(
            [revenue_growth, profit_growth, ssgr, history],
            report.business.source_ids + report.fundamentals.source_ids,
        ),
    )


def _score_management(report: CompanyResearch) -> tuple[int | None, str, list[str], list[str]]:
    if (
        report.management.status != "complete"
        or report.management.score is None
        or not report.management.source_ids
    ):
        return (
            None,
            "No dated management/governance source was resolved.",
            ["management evidence"],
            [],
        )
    score = _bounded(round(report.management.score / 20))
    return (
        score,
        f"The evidence-backed management assessment is {report.management.score:.0f}/100.",
        [],
        list(report.management.source_ids),
    )


def _score_valuation(report: CompanyResearch) -> tuple[int | None, str, list[str], list[str]]:
    metrics = report.metric_map()
    pe = _first_metric(metrics, "pe_ttm", "pe")
    peg = _first_metric(metrics, "peg")
    price_sales = _first_metric(metrics, "price_sales", "ps_ratio")
    gsec = _first_metric(metrics, "gsec_10y_yield")
    pe_value = _as_number(pe)
    if pe_value is None or pe_value <= 0:
        return (
            None,
            "Normalized positive earnings valuation is unavailable.",
            ["positive normalized P/E"],
            [],
        )

    earnings_yield = 100 / pe_value
    score = 3
    gsec_value = _as_number(gsec)
    if gsec_value is not None:
        score += 1 if earnings_yield > gsec_value else -1
    peg_value = _as_number(peg)
    if peg_value is not None:
        score += 1 if peg_value < 1 else (-1 if peg_value > 2 else 0)
    ps_value = _as_number(price_sales)
    if ps_value is not None:
        score += 1 if ps_value < 1.5 else (-1 if ps_value > 3 else 0)
    score = _bounded(score)
    return (
        score,
        (
            f"P/E={pe_value:.2f} (earnings yield {earnings_yield:.2f}%); PEG="
            f"{peg_value if peg_value is not None else 'unknown'}; P/S="
            f"{ps_value if ps_value is not None else 'unknown'}; current G-sec evidence="
            f"{gsec_value if gsec_value is not None else 'missing'}%."
        ),
        [],
        _source_ids([pe, peg, price_sales, gsec], report.valuation.source_ids),
    )


def _rating_to_score(value: str) -> int | None:
    rating = value.upper().replace(" ", "")
    if not rating or rating in {"UNRATED", "NA", "N/A"}:
        return None
    if "D" == rating or "DEFAULT" in rating:
        return 0
    if "AAA" in rating or "AA" in rating:
        return 5
    if rating.startswith("A"):
        return 4
    if "BBB" in rating:
        return 3 if "NEGATIVE" not in rating else 2
    return 1


def _score_credit(report: CompanyResearch) -> tuple[int | None, str, list[str], list[str]]:
    metrics = report.metric_map()
    rating_metric = _first_metric(metrics, "credit_rating")
    if rating_metric is None or rating_metric.value is None:
        return (
            None,
            "Credit rating evidence was not found; unrated is not scored as zero.",
            ["credit rating"],
            [],
        )
    score = _rating_to_score(str(rating_metric.value))
    if score is None:
        return (
            None,
            "The company is unrated, so this category remains unknown.",
            ["rated credit opinion"],
            [],
        )
    return (
        score,
        f"The resolved rating signal is {rating_metric.value}; rating sensitivities still require review.",
        [],
        _source_ids([rating_metric], report.management.source_ids),
    )


ScoreFunction = Callable[[CompanyResearch], tuple[int | None, str, list[str], list[str]]]
SCORERS: dict[str, ScoreFunction] = {
    "financial_strength": _score_financial,
    "earnings_quality": _score_cash,
    "moat_growth": _score_moat,
    "management": _score_management,
    "valuation": _score_valuation,
    "credit_resilience": _score_credit,
}


class BookScoringAgent:
    """Retrieve book principles and score one company against fixed anchors."""

    def __init__(self, knowledge_base: BookKnowledgeBase, *, as_of_date: date) -> None:
        self.knowledge_base = knowledge_base
        self.as_of_date = as_of_date

    def score(self, report: CompanyResearch) -> BookScore:
        self.knowledge_base.ensure_index()
        categories: list[BookCategoryScore] = []
        covered_weight = 0

        for spec in BOOK_CATEGORIES:
            matches = self.knowledge_base.retrieve(
                spec.retrieval_query,
                printed_ranges=spec.printed_ranges,
                per_range=1,
            )
            citations = [
                BookCitation(
                    principle_id=spec.principle_id,
                    chapter=match.chapter,
                    printed_page=match.printed_page,
                    pdf_page=match.pdf_page,
                    chunk_id=match.chunk_id,
                    paraphrase=spec.paraphrase,
                )
                for match in matches[:2]
            ]
            score, rationale, missing, source_ids = SCORERS[spec.id](report)
            if not citations:
                missing = [*missing, "resolved book passage"]
            is_covered = score is not None and bool(citations) and bool(source_ids)
            if is_covered:
                covered_weight += spec.weight
            weighted = None if score is None else round(spec.weight * score / 5, 2)
            categories.append(
                BookCategoryScore(
                    id=spec.id,
                    label=spec.label,
                    weight=spec.weight,
                    score=score,
                    weighted_points=weighted,
                    rationale=rationale,
                    book_basis=citations,
                    company_source_ids=source_ids,
                    missing_data=missing,
                    sector_context=(
                        "Jewellery context: interpret gold-metal loans, inventory intensity, "
                        "same-store sales, store expansion, hedging, and gold-price inventory gains "
                        "separately; the literal book thresholds are not relaxed."
                    ),
                    confidence="high"
                    if is_covered and len(source_ids) >= 2
                    else "medium"
                    if is_covered
                    else "low",
                )
            )

        hard_flags = self._hard_red_flags(report)
        total: float | None = None
        if covered_weight >= 80:
            raw_points = sum(
                item.weighted_points or 0 for item in categories if item.score is not None
            )
            # Normalize only across categories that truly had both kinds of evidence.
            covered_points = sum(
                item.weighted_points or 0
                for item in categories
                if item.score is not None and item.book_basis and item.company_source_ids
            )
            _ = raw_points  # Kept explicit for novice debugging in a breakpoint.
            total = round(covered_points * 100 / covered_weight, 2)

        if hard_flags:
            status = "manual_review"
        elif total is None:
            status = "insufficient_evidence"
        else:
            status = "complete"

        return BookScore(
            company=report.company,
            as_of_date=self.as_of_date,
            document_hash=self.knowledge_base.document_hash(),
            categories=categories,
            coverage_weight=covered_weight,
            total_score=total,
            score_band=self._band(total),
            hard_red_flags=hard_flags,
            decision_status=status,  # type: ignore[arg-type]
        )

    @staticmethod
    def _hard_red_flags(report: CompanyResearch) -> list[str]:
        text = " ".join(
            report.business.risks + report.fundamentals.risks + report.management.risks
        ).lower()
        phrases = (
            "confirmed fraud",
            "adverse auditor opinion",
            "regulatory disqualification",
            "credit default",
            "withdrawn for non-cooperation",
        )
        return [phrase for phrase in phrases if phrase in text]

    @staticmethod
    def _band(total: float | None) -> str:
        if total is None:
            return "Not scored"
        if total >= 80:
            return "Strong book alignment"
        if total >= 65:
            return "Good book alignment"
        if total >= 50:
            return "Mixed book alignment"
        return "Weak book alignment"
