"""Typed contracts passed between agents.

LangGraph moves a shared state object from node to node.  These Pydantic models
act like labelled containers: they catch missing or malformed data at the edge
of each agent rather than letting an error quietly reach the final ranking.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

BOOK_CATEGORY_WEIGHTS = {
    "financial_strength": 20,
    "earnings_quality": 20,
    "moat_growth": 20,
    "management": 20,
    "valuation": 15,
    "credit_resilience": 5,
}


class StrictModel(BaseModel):
    """Base class that rejects accidental/unknown fields."""

    model_config = ConfigDict(extra="forbid")


class CompanyIdentity(StrictModel):
    name: str
    ticker: str
    exchange: str = "NSE"
    comparison_reason: str = ""


class EvidenceItem(StrictModel):
    """One traceable web, news, filing, market-data, or book source."""

    source_id: str
    company_ticker: str
    title: str
    url: str
    publisher: str = ""
    source_type: str = "web"
    published_at: str | None = None
    accessed_at: datetime
    excerpt: str = ""
    tags: list[str] = Field(default_factory=list)
    search_query: str | None = None
    search_uuid: str | None = None
    search_endpoint: str | None = None
    search_latency_seconds: float | None = None
    result_rank: int | None = None
    extraction_mode: str | None = None
    authority: Literal["primary_regulatory", "primary_company", "secondary", "private_book"] = (
        "secondary"
    )


class Metric(StrictModel):
    """A value with period, units, evidence, and confidence kept together."""

    code: str
    label: str
    value: float | str | None
    unit: str = ""
    period: str = ""
    as_of_date: date | None = None
    period_type: Literal[
        "FY",
        "quarter",
        "half_year",
        "nine_months",
        "TTM",
        "point_in_time",
        "multi_year",
        "unknown",
    ] = "unknown"
    accounting_basis: Literal[
        "consolidated", "standalone", "segment", "not_applicable", "unknown"
    ] = "unknown"
    measurement_type: Literal[
        "reported", "derived", "peer_normalized", "management_adjusted", "illustrative"
    ] = "reported"
    source_ids: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.5, ge=0, le=1)
    definition: str = ""
    formula: str = ""
    input_metric_codes: list[str] = Field(default_factory=list)
    flags: list[str] = Field(default_factory=list)


class AnalysisBlock(StrictModel):
    """Structured output shared by the Business/Fundamental/etc. agents."""

    agent: str
    score: float | None = Field(default=None, ge=0, le=100)
    status: Literal["complete", "insufficient_evidence"] = "complete"
    summary: str
    strengths: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    metrics: list[Metric] = Field(default_factory=list)
    source_ids: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.5, ge=0, le=1)
    growth_score: float | None = Field(default=None, ge=0, le=100)

    @model_validator(mode="after")
    def validate_status_and_score(self) -> AnalysisBlock:
        if self.status == "complete" and self.score is None:
            raise ValueError("A complete analysis block needs a score.")
        if self.status == "insufficient_evidence" and self.score is not None:
            raise ValueError("Insufficient-evidence blocks must not invent a score.")
        return self


FundamentalMetricCode = Literal[
    "roe",
    "roce",
    "revenue_growth",
    "profit_growth",
    "debt_equity",
    "net_debt_equity",
    "interest_coverage",
    "current_ratio",
    "cfo_pat",
    "operating_cash_flow",
    "pat",
]


class FundamentalObservation(StrictModel):
    """One compact fact extracted by the live Fundamentals LLM.

    The complete :class:`Metric` contract has many audit fields and is ideal
    inside the application, but it is expensive for a model to repeat eight
    times.  This smaller wire contract keeps only facts the model must read
    from evidence. Python adds deterministic labels, units, and scoring later.
    """

    code: FundamentalMetricCode
    value: float
    unit: Literal[
        "percent",
        "ratio",
        "INR_crore",
        "INR_lakh",
        "INR_million",
        "INR_billion",
    ]
    as_of_date: date
    period_type: Literal[
        "FY", "quarter", "half_year", "nine_months", "TTM", "point_in_time", "multi_year"
    ]
    accounting_basis: Literal["consolidated", "standalone"]
    source_id: str = Field(min_length=1)


class FundamentalsExtraction(StrictModel):
    """Small structured-output boundary used only by the live Fundamentals Agent."""

    # Raw CFO and PAT may legitimately repeat for several fiscal years.  The
    # deterministic layer matches those dated pairs and calculates cumulative
    # cash conversion; the LLM is never asked to do the arithmetic.
    observations: list[FundamentalObservation] = Field(max_length=20)


class CitedClaim(StrictModel):
    """One short qualitative statement tied to supplied company evidence."""

    text: str = Field(min_length=1, max_length=500)
    source_ids: list[str] = Field(min_length=1, max_length=2)


class QualitativeSlice(StrictModel):
    """Compact business/management output inside one shared company dossier."""

    status: Literal["complete", "insufficient_evidence"] = "complete"
    score: float | None = Field(default=None, ge=0, le=100)
    summary: str = Field(min_length=1, max_length=1_000)
    strengths: list[CitedClaim] = Field(default_factory=list, max_length=4)
    risks: list[CitedClaim] = Field(default_factory=list, max_length=4)
    source_ids: list[str] = Field(default_factory=list, max_length=8)
    confidence: float = Field(default=0.5, ge=0, le=1)

    @model_validator(mode="after")
    def validate_status_and_score(self) -> QualitativeSlice:
        if self.status == "complete" and self.score is None:
            raise ValueError("A complete qualitative slice needs a score.")
        if self.status == "insufficient_evidence" and self.score is not None:
            raise ValueError("An insufficient qualitative slice cannot have a score.")
        return self


ValuationMetricCode = Literal["pe_ttm", "peg", "price_sales", "gsec_10y_yield"]


class ValuationObservation(StrictModel):
    """A compact reported valuation fact; Python performs the scoring."""

    code: ValuationMetricCode
    value: float
    unit: Literal["ratio", "percent"]
    as_of_date: date
    period_type: Literal["TTM", "point_in_time"]
    accounting_basis: Literal["consolidated", "standalone", "not_applicable"]
    source_id: str = Field(min_length=1)


class CompanyDossierExtraction(StrictModel):
    """One bounded Kimi response shared by the logical Stage 2 agents."""

    ticker: str = Field(min_length=1, max_length=20)
    business: QualitativeSlice
    management: QualitativeSlice
    fundamentals: FundamentalsExtraction
    valuation: list[ValuationObservation] = Field(default_factory=list, max_length=4)


class CompanyResearch(StrictModel):
    """All Stage 2 outputs for one company after the four-way join."""

    company: CompanyIdentity
    sources: list[EvidenceItem]
    business: AnalysisBlock
    fundamentals: AnalysisBlock
    management: AnalysisBlock
    technicals: AnalysisBlock
    valuation: AnalysisBlock
    core_score: float | None = Field(default=None, ge=0, le=100)
    confidence: float = Field(ge=0, le=1)
    warnings: list[str] = Field(default_factory=list)

    def metric_map(self) -> dict[str, Metric]:
        """Return the best comparable occurrence of each normalized metric code.

        "Latest" cannot mean "whichever agent happened to write last."  We sort
        by dated period, then prefer peer-normalized/consolidated observations.
        The full observations still remain on their original analysis blocks.
        """

        candidates: dict[str, list[Metric]] = {}
        for block in (
            self.business,
            self.fundamentals,
            self.management,
            self.technicals,
            self.valuation,
        ):
            for metric in block.metrics:
                candidates.setdefault(metric.code.lower().strip(), []).append(metric)

        measurement_priority = {
            "peer_normalized": 4,
            "derived": 3,
            "reported": 2,
            "management_adjusted": 1,
            "illustrative": 0,
        }
        basis_priority = {
            "consolidated": 4,
            "standalone": 3,
            "segment": 2,
            "not_applicable": 1,
            "unknown": 0,
        }

        def preference(metric: Metric) -> tuple[date, int, int, float]:
            return (
                metric.as_of_date or date.min,
                measurement_priority[metric.measurement_type],
                basis_priority[metric.accounting_basis],
                metric.confidence,
            )

        return {code: max(items, key=preference) for code, items in candidates.items()}


class BookCitation(StrictModel):
    principle_id: str
    chapter: str
    printed_page: int
    pdf_page: int
    chunk_id: str
    paraphrase: str


class BookCategoryScore(StrictModel):
    id: str
    label: str
    weight: int
    score: int | None = Field(default=None, ge=0, le=5)
    weighted_points: float | None = Field(default=None, ge=0)
    rationale: str
    book_basis: list[BookCitation] = Field(default_factory=list)
    company_source_ids: list[str] = Field(default_factory=list)
    missing_data: list[str] = Field(default_factory=list)
    sector_context: str = ""
    confidence: Literal["low", "medium", "high"] = "medium"


class BookScore(StrictModel):
    """Stage 3 score. It measures alignment with the book, not a buy signal."""

    score_version: str = "peaceful_investing_v1"
    company: CompanyIdentity
    as_of_date: date
    document_hash: str
    categories: list[BookCategoryScore]
    coverage_weight: int = Field(ge=0, le=100)
    total_score: float | None = Field(default=None, ge=0, le=100)
    score_band: str
    hard_red_flags: list[str] = Field(default_factory=list)
    decision_status: Literal["complete", "insufficient_evidence", "manual_review"]

    @model_validator(mode="after")
    def validate_fixed_policy(self) -> BookScore:
        if len(self.categories) != 6:
            raise ValueError("The fixed book policy requires exactly six categories.")
        ids = [item.id for item in self.categories]
        if len(set(ids)) != len(ids) or set(ids) != set(BOOK_CATEGORY_WEIGHTS):
            raise ValueError("Book score must contain each fixed category exactly once.")
        for item in self.categories:
            if item.weight != BOOK_CATEGORY_WEIGHTS[item.id]:
                raise ValueError(f"Unexpected weight for book category {item.id}.")
            expected_points = None if item.score is None else round(item.weight * item.score / 5, 2)
            if item.weighted_points != expected_points:
                raise ValueError(f"Weighted points do not match score for {item.id}.")

        calculated_coverage = sum(
            item.weight
            for item in self.categories
            if item.score is not None and item.book_basis and item.company_source_ids
        )
        if self.coverage_weight != calculated_coverage:
            raise ValueError("Book evidence coverage does not match the covered categories.")
        if self.coverage_weight < 80 and self.total_score is not None:
            raise ValueError("Book total must be null when evidence coverage is below 80%.")
        if self.coverage_weight >= 80:
            covered_points = sum(
                item.weighted_points or 0
                for item in self.categories
                if item.score is not None and item.book_basis and item.company_source_ids
            )
            expected_total = round(covered_points * 100 / self.coverage_weight, 2)
            if self.total_score != expected_total:
                raise ValueError("Book total does not match covered weighted points.")
        return self


class ScoredCompany(StrictModel):
    research: CompanyResearch
    book_score: BookScore
    final_score: float | None = Field(default=None, ge=0, le=100)
    rank: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def validate_company_match(self) -> ScoredCompany:
        if self.research.company.ticker != self.book_score.company.ticker:
            raise ValueError("Research and book scores belong to different companies.")
        if self.book_score.decision_status != "complete" and self.final_score is not None:
            raise ValueError("Only a complete book score can receive a final score.")
        return self


class AuditFinding(StrictModel):
    severity: Literal["info", "warning", "error"]
    company_ticker: str | None = None
    code: str
    message: str


class AuditResult(StrictModel):
    passed: bool
    retryable_tickers: list[str] = Field(default_factory=list)
    findings: list[AuditFinding] = Field(default_factory=list)
    retry_count: int = 0


class TraceEvent(StrictModel):
    stage: str
    agent: str
    status: Literal["started", "completed", "warning"]
    detail: str
    timestamp: datetime


class FinalBriefing(StrictModel):
    title: str
    mode: Literal["demo", "live"]
    as_of_date: date
    target: CompanyIdentity
    companies: list[ScoredCompany]
    audit: AuditResult
    conclusion: str
    methodology: str
    disclaimer: str
    markdown: str = ""


class PeerDiscoveryOutput(StrictModel):
    peers: list[CompanyIdentity]
    rationale: str


def json_ready(value: Any) -> Any:
    """Convert Pydantic models into objects accepted by `json.dumps`."""

    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    return value
