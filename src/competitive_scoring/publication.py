"""One final, deterministic scan before a report can reach the user.

The graph validates individual agent outputs while it runs.  This module is a
second boundary around the *assembled* report.  It distrusts cached totals,
ranks, audit flags, and rendered prose: each of those values is calculated again
from the final company maps and compared with the stored result.

Keeping this scan independent of Streamlit is important.  The background run
manager, the JSON report store, and future interfaces all apply the exact same
release rules.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from .agents import (
    _demo_fundamental_quality,
    _demo_growth_quality,
    _fundamental_growth_score,
    _fundamental_quality_score,
    _technical_score,
    audit_results,
)
from .models import (
    AnalysisBlock,
    AuditResult,
    CompanyIdentity,
    FinalBriefing,
    Metric,
    ScoredCompany,
    TraceEvent,
)
from .report import render_markdown
from .scoring import (
    calculate_core_score,
    calculate_final_score,
    rank_companies,
    research_confidence,
)
from .tracing import event, traced

FIXED_TITLE = "PNGJL Competitive Investment Scoring Briefing"

# This project deliberately compares one target with three vetted listed peers.
# Names, symbols, exchanges, and reasons are policy inputs, not model output.
FIXED_IDENTITIES: dict[str, CompanyIdentity] = {
    "PNGJL": CompanyIdentity(
        name="P N Gadgil Jewellers Limited",
        ticker="PNGJL",
        exchange="NSE",
        comparison_reason="User-selected target company.",
    ),
    "KALYANKJIL": CompanyIdentity(
        name="Kalyan Jewellers India Limited",
        ticker="KALYANKJIL",
        exchange="NSE",
        comparison_reason="Listed national jewellery retailer with a scaled network.",
    ),
    "SENCO": CompanyIdentity(
        name="Senco Gold Limited",
        ticker="SENCO",
        exchange="NSE",
        comparison_reason="Listed regional jewellery peer with an eastern India base.",
    ),
    "THANGAMAYL": CompanyIdentity(
        name="Thangamayil Jewellery Limited",
        ticker="THANGAMAYL",
        exchange="NSE",
        comparison_reason="Listed regional jewellery peer with a Tamil Nadu base.",
    ),
}
FIXED_TICKERS = frozenset(FIXED_IDENTITIES)
FUNDAMENTAL_MAX_AGE_DAYS = 550
MANAGEMENT_MAX_AGE_DAYS = 550
VALUATION_MAX_AGE_DAYS = 120
TECHNICAL_MAX_AGE_DAYS = 7
FIXED_BOOK_SCORE_VERSION = "peaceful_investing_v1"

EXPECTED_BLOCK_AGENTS = {
    "business": "Business Agent",
    "fundamentals": "Fundamentals Agent",
    "management": "Management Agent",
    "technicals": "Technicals Agent",
    "valuation": "Valuation Agent",
}
LIVE_FUNDAMENTAL_CODES = {
    "roe",
    "roce",
    "revenue_growth",
    "profit_growth",
    "debt_equity",
    "net_debt_equity",
    "interest_coverage",
    "current_ratio",
    "cfo_pat",
}
LIVE_MANAGEMENT_CODES = {"credit_rating"}
LIVE_VALUATION_CODES = {"pe_ttm", "peg", "price_sales", "gsec_10y_yield"}
LIVE_TECHNICAL_CODES = {"close", "sma20", "sma50", "rsi14"}


@dataclass(frozen=True, slots=True)
class PublicationIssue:
    """A compact release-gate problem safe to show in developer diagnostics."""

    code: str
    message: str
    company_ticker: str | None = None


@dataclass(frozen=True, slots=True)
class PublicationScan:
    """Result of recalculating and checking one complete briefing."""

    issues: tuple[PublicationIssue, ...]
    recomputed_audit: AuditResult

    @property
    def passed(self) -> bool:
        return not self.issues


class PublicationValidationError(ValueError):
    """Raised when a report is structurally valid but unsafe to publish."""

    def __init__(self, issues: tuple[PublicationIssue, ...]) -> None:
        self.issues = issues
        preview = "; ".join(issue.code for issue in issues[:5])
        if len(issues) > 5:
            preview += f"; and {len(issues) - 5} more"
        super().__init__(
            "Only reports with a passed evidence audit, zero warnings, and a complete ranking "
            f"may be promoted. Publication scan found {len(issues)} issue(s): {preview}."
        )


@traced("report.publication_scan")
def scan_for_publication(
    briefing: FinalBriefing,
    trace: list[TraceEvent] | tuple[TraceEvent, ...],
) -> PublicationScan:
    """Rebuild every publication-critical value and return all detected issues.

    The function intentionally gathers all problems instead of stopping at the
    first one.  A rejected run can therefore be repaired in one pass while the
    previously validated report remains visible to users.
    """

    issues: list[PublicationIssue] = []

    if briefing.title != FIXED_TITLE:
        _add(issues, "TITLE_MISMATCH", f"Expected the fixed title {FIXED_TITLE!r}.")
    if briefing.target != FIXED_IDENTITIES["PNGJL"]:
        _add(issues, "TARGET_IDENTITY_MISMATCH", "The target must be the fixed PNGJL identity.")

    tickers = [item.research.company.ticker for item in briefing.companies]
    if len(tickers) != 4 or len(set(tickers)) != 4 or set(tickers) != FIXED_TICKERS:
        _add(
            issues,
            "COMPANY_SET_MISMATCH",
            "The report must contain PNGJL, KALYANKJIL, SENCO, and THANGAMAYL exactly once.",
        )

    # A dictionary is needed by the deterministic auditor.  Duplicate symbols
    # have already been reported above; retaining the first avoids a crash and
    # lets the scan continue collecting useful diagnostics.
    companies_by_ticker: dict[str, ScoredCompany] = {}
    book_hashes: set[str] = set()
    for item in briefing.companies:
        ticker = item.research.company.ticker
        companies_by_ticker.setdefault(ticker, item)
        expected_identity = FIXED_IDENTITIES.get(ticker)
        if expected_identity is None:
            continue
        if item.research.company != expected_identity:
            _add(
                issues,
                "COMPANY_IDENTITY_MISMATCH",
                "Research identity does not match the fixed comparison policy.",
                ticker,
            )
        if item.book_score.company != expected_identity:
            _add(
                issues,
                "BOOK_IDENTITY_MISMATCH",
                "Book score identity does not match the fixed comparison policy.",
                ticker,
            )
        if item.book_score.as_of_date != briefing.as_of_date:
            _add(
                issues,
                "BOOK_DATE_MISMATCH",
                "Book score date must exactly match the report research date.",
                ticker,
            )

        for source in item.research.sources:
            if source.company_ticker != ticker:
                _add(
                    issues,
                    "SOURCE_COMPANY_MISMATCH",
                    f"Source {source.source_id!r} belongs to another ticker.",
                    ticker,
                )
        source_ids = [source.source_id for source in item.research.sources]
        if len(source_ids) != len(set(source_ids)):
            _add(
                issues,
                "DUPLICATE_SOURCE_ID",
                "Company evidence contains duplicate source identifiers.",
                ticker,
            )

        if briefing.mode == "live":
            if _contains_demo_data(item):
                _add(
                    issues,
                    "LIVE_ILLUSTRATIVE_DATA",
                    "A live report cannot contain illustrative metrics or demo-only sources.",
                    ticker,
                )
            usable_primary_documents = [
                source
                for source in item.research.sources
                if source.source_type == "official_document"
                and source.authority in {"primary_regulatory", "primary_company"}
                and source.excerpt.strip()
            ]
            if not usable_primary_documents:
                _add(
                    issues,
                    "OFFICIAL_DOCUMENT_MISSING",
                    "At least one successfully read first-party filing/result is required.",
                    ticker,
                )
            if not any(
                "you_primary" in source.tags and source.excerpt.strip()
                for source in item.research.sources
            ):
                _add(
                    issues,
                    "YOU_EVIDENCE_MISSING",
                    "The primary You.com discovery layer returned no usable evidence.",
                    ticker,
                )

        named_blocks = (
            ("business", item.research.business),
            ("fundamentals", item.research.fundamentals),
            ("management", item.research.management),
            ("technicals", item.research.technicals),
            ("valuation", item.research.valuation),
        )
        seen_metric_codes: set[str] = set()
        for field_name, block in named_blocks:
            if block.agent != EXPECTED_BLOCK_AGENTS[field_name]:
                _add(
                    issues,
                    "BLOCK_AGENT_MISMATCH",
                    (
                        f"The {field_name} field must contain "
                        f"{EXPECTED_BLOCK_AGENTS[field_name]!r}, not {block.agent!r}."
                    ),
                    ticker,
                )
            if block.status != "complete" or block.score is None:
                _add(
                    issues,
                    "BLOCK_NOT_COMPLETE",
                    f"{block.agent} must have a complete evidence-backed score.",
                    ticker,
                )
            if block.confidence < 0.50:
                _add(
                    issues,
                    "BLOCK_LOW_CONFIDENCE",
                    f"{block.agent} confidence is below 0.50.",
                    ticker,
                )
            normalized_codes = [metric.code.lower().strip() for metric in block.metrics]
            duplicate_codes = {
                code
                for code in normalized_codes
                if normalized_codes.count(code) > 1 or code in seen_metric_codes
            }
            if duplicate_codes:
                _add(
                    issues,
                    "DUPLICATE_METRIC_CODE",
                    f"Duplicate metric code(s): {sorted(duplicate_codes)}.",
                    ticker,
                )
            seen_metric_codes.update(normalized_codes)
            if field_name in {"fundamentals", "technicals", "valuation"}:
                nonnumeric = [
                    metric.code
                    for metric in block.metrics
                    if metric.value is not None and not _is_finite_number(metric.value)
                ]
                if nonnumeric:
                    _add(
                        issues,
                        "NUMERIC_METRIC_INVALID",
                        f"Non-numeric scored metric(s): {sorted(nonnumeric)}.",
                        ticker,
                    )

        if briefing.mode == "live":
            _scan_live_metric_policy(item, issues)
            _scan_live_citation_quality(item, issues)

        blocks = [block for _, block in named_blocks]
        usable_source_count = sum(
            bool(source.excerpt.strip())
            and not source.source_type.startswith("manual_reference")
            for source in item.research.sources
        )
        expected_research_confidence = research_confidence(
            blocks,
            source_count=usable_source_count,
        )
        if item.research.confidence != expected_research_confidence:
            _add(
                issues,
                "RESEARCH_CONFIDENCE_MISMATCH",
                (
                    f"Stored research confidence {item.research.confidence!r} should be "
                    f"{expected_research_confidence!r}."
                ),
                ticker,
            )

        if item.research.warnings:
            _add(
                issues,
                "RESEARCH_WARNINGS_PRESENT",
                f"Company research contains {len(item.research.warnings)} warning(s).",
                ticker,
            )

        book = item.book_score
        document_hash = book.document_hash.strip()
        if not document_hash:
            _add(
                issues,
                "BOOK_DOCUMENT_HASH_MISSING",
                "The private-book score must record a nonempty document hash.",
                ticker,
            )
        else:
            book_hashes.add(document_hash)
        if book.score_version != FIXED_BOOK_SCORE_VERSION:
            _add(
                issues,
                "BOOK_SCORE_VERSION_MISMATCH",
                f"Book scoring policy must be {FIXED_BOOK_SCORE_VERSION!r}.",
                ticker,
            )
        if book.decision_status != "complete":
            _add(
                issues,
                "BOOK_NOT_COMPLETE",
                "The Peaceful Investing score must be complete.",
                ticker,
            )
        if book.coverage_weight < 80 or book.total_score is None:
            _add(
                issues,
                "BOOK_COVERAGE_INSUFFICIENT",
                "Book evidence coverage must be at least 80% with a calculated total.",
                ticker,
            )
        if book.hard_red_flags:
            _add(
                issues,
                "BOOK_RED_FLAGS_PRESENT",
                "A hard red flag requires review before publication.",
                ticker,
            )

    if len(book_hashes) != 1:
        _add(
            issues,
            "BOOK_DOCUMENT_HASH_MISMATCH",
            "Every company must be scored from the same nonempty private-book document hash.",
        )

    warning_count = sum(event.status == "warning" for event in trace)
    if warning_count:
        _add(
            issues,
            "TRACE_WARNINGS_PRESENT",
            f"Execution trace contains {warning_count} warning event(s).",
        )

    # Re-run the auditor from the final maps.  This prevents a stale or manually
    # edited ``audit.passed`` flag from approving data the auditor would reject.
    reports = {ticker: item.research for ticker, item in companies_by_ticker.items()}
    book_scores = {ticker: item.book_score for ticker, item in companies_by_ticker.items()}
    recomputed_audit = audit_results(
        reports,
        book_scores,
        retry_count=briefing.audit.retry_count,
        mode=briefing.mode,
    )
    if briefing.audit != recomputed_audit:
        _add(
            issues,
            "AUDIT_MISMATCH",
            "The stored evidence audit differs from an audit rebuilt from the final maps.",
        )
    blocking_audit = [
        finding for finding in recomputed_audit.findings if finding.severity in {"warning", "error"}
    ]
    if not recomputed_audit.passed or blocking_audit:
        _add(
            issues,
            "AUDIT_NOT_CLEAN",
            f"The recomputed audit contains {len(blocking_audit)} warning/error finding(s).",
        )

    expected_companies: list[ScoredCompany] = []
    for item in briefing.companies:
        research = item.research
        expected_fundamentals, expected_valuation, expected_technicals = (
            _recompute_deterministic_blocks(item, mode=briefing.mode, issues=issues)
        )
        expected_core = calculate_core_score(
            expected_fundamentals,
            expected_valuation,
            research.management,
            expected_technicals,
        )
        if research.core_score != expected_core:
            _add(
                issues,
                "CORE_SCORE_MISMATCH",
                f"Stored core score {research.core_score!r} should be {expected_core!r}.",
                research.company.ticker,
            )
        rebuilt_research = research.model_copy(
            update={
                "fundamentals": expected_fundamentals,
                "valuation": expected_valuation,
                "technicals": expected_technicals,
                "core_score": expected_core,
            }
        )
        expected_final = calculate_final_score(expected_core, item.book_score)
        if item.final_score != expected_final:
            _add(
                issues,
                "FINAL_SCORE_MISMATCH",
                f"Stored final score {item.final_score!r} should be {expected_final!r}.",
                research.company.ticker,
            )
        expected_companies.append(
            item.model_copy(
                update={
                    "research": rebuilt_research,
                    "final_score": expected_final,
                    "rank": None,
                }
            )
        )

    expected_ranked = rank_companies(expected_companies)
    expected_order = [item.research.company.ticker for item in expected_ranked]
    actual_order = [item.research.company.ticker for item in briefing.companies]
    if actual_order != expected_order:
        _add(
            issues,
            "RANK_ORDER_MISMATCH",
            f"Report order {actual_order!r} should be {expected_order!r}.",
        )
    expected_ranks = {item.research.company.ticker: item.rank for item in expected_ranked}
    for item in briefing.companies:
        ticker = item.research.company.ticker
        if item.rank != expected_ranks.get(ticker):
            _add(
                issues,
                "RANK_MISMATCH",
                f"Stored rank {item.rank!r} should be {expected_ranks.get(ticker)!r}.",
                ticker,
            )

    expected_conclusion = _conclusion_for(
        mode=briefing.mode,
        audit=recomputed_audit,
        ranked=expected_ranked,
    )
    if briefing.conclusion != expected_conclusion:
        _add(
            issues,
            "CONCLUSION_MISMATCH",
            "The conclusion does not match the recomputed winner and score.",
        )

    # Render from recalculated companies/audit/conclusion rather than from the
    # possibly stale values supplied in ``briefing``.
    canonical = briefing.model_copy(
        update={
            "companies": expected_ranked,
            "audit": recomputed_audit,
            "conclusion": expected_conclusion,
            "markdown": "",
        }
    )
    if briefing.markdown != render_markdown(canonical):
        _add(
            issues,
            "MARKDOWN_MISMATCH",
            "Rendered Markdown is stale or inconsistent with the final structured report.",
        )

    event("publication.result", passed=not issues, issue_count=len(issues))
    for issue in issues:
        event("publication.issue", code=issue.code, ticker=issue.company_ticker)
    return PublicationScan(issues=tuple(issues), recomputed_audit=recomputed_audit)


def require_publishable(
    briefing: FinalBriefing,
    trace: list[TraceEvent] | tuple[TraceEvent, ...],
) -> PublicationScan:
    """Return the clean scan or raise before an invalid report is published."""

    scan = scan_for_publication(briefing, trace)
    if not scan.passed:
        raise PublicationValidationError(scan.issues)
    return scan


def _conclusion_for(*, mode: str, audit: AuditResult, ranked: list[ScoredCompany]) -> str:
    """Recreate the graph's deterministic executive conclusion."""

    available = [item for item in ranked if item.final_score is not None]
    if not audit.passed:
        return (
            "No validated ranking was issued because the evidence audit failed. "
            "Inspect the audit findings, correct the evidence, and rerun the workflow."
        )
    if not available:
        return (
            "No final ranking was issued because required company or book evidence was "
            "insufficient."
        )
    leader = available[0]
    return (
        f"{leader.research.company.name} ranks first in this "
        f"{'illustrative demo' if mode == 'demo' else 'evidence-backed run'} "
        f"at {leader.final_score:.2f}/100. Treat the ranking as a research starting "
        "point and inspect confidence, valuation assumptions, and downside triggers."
    )


def _add(
    issues: list[PublicationIssue],
    code: str,
    message: str,
    company_ticker: str | None = None,
) -> None:
    issues.append(PublicationIssue(code=code, message=message, company_ticker=company_ticker))


def _contains_demo_data(item: ScoredCompany) -> bool:
    """Return ``True`` when demo-only markers leaked into a live result."""

    for source in item.research.sources:
        tags = {tag.lower().strip() for tag in source.tags}
        source_text = f"{source.title} {source.excerpt}".lower()
        if source.source_type.lower().startswith("demo_") or tags.intersection(
            {"illustrative", "not-live-evidence"}
        ) or any(
            marker in source_text
            for marker in ("demo mode", "invented fixture", "not-live", "not live evidence")
        ):
            return True

    blocks = (
        item.research.business,
        item.research.fundamentals,
        item.research.management,
        item.research.technicals,
        item.research.valuation,
    )
    for block in blocks:
        analysis_text = " ".join([block.summary, *block.strengths, *block.risks]).lower()
        if "illustrative" in analysis_text or "demo " in analysis_text:
            return True
        for metric in block.metrics:
            flags = {flag.lower().strip() for flag in metric.flags}
            if (
                metric.measurement_type == "illustrative"
                or "illustrative" in flags
                or "illustrative" in metric.period.lower()
                or (
                    isinstance(metric.value, str)
                    and any(
                        marker in metric.value.lower()
                        for marker in ("illustrative", "demo", "not-live")
                    )
                )
            ):
                return True

    # The demo policy can also leak through a copied book rationale even after
    # a caller has relabelled source and metric metadata.
    return any(
        "illustrative" in f"{category.rationale} {category.sector_context}".lower()
        for category in item.book_score.categories
    )


def _is_usable_live_source(source_type: str, excerpt: str) -> bool:
    """Match the graph's evidence rule and explicitly exclude demo placeholders."""

    normalized_type = source_type.lower().strip()
    return bool(excerpt.strip()) and not normalized_type.startswith(
        ("manual_reference", "demo_")
    )


def _scan_live_citation_quality(
    item: ScoredCompany,
    issues: list[PublicationIssue],
) -> None:
    """Require every scored live claim to resolve to evidence the agents could read."""

    ticker = item.research.company.ticker
    source_by_id = {source.source_id: source for source in item.research.sources}
    usable_ids = {
        source_id
        for source_id, source in source_by_id.items()
        if _is_usable_live_source(source.source_type, source.excerpt)
    }
    bad_ids: set[str] = set()
    missing_block_evidence: list[str] = []
    blocks = (
        item.research.business,
        item.research.fundamentals,
        item.research.management,
        item.research.technicals,
        item.research.valuation,
    )
    for block in blocks:
        if block.status == "complete" and not set(block.source_ids).intersection(usable_ids):
            missing_block_evidence.append(block.agent)
        bad_ids.update(source_id for source_id in block.source_ids if source_id not in usable_ids)
        for metric in block.metrics:
            if metric.value is not None:
                bad_ids.update(
                    source_id for source_id in metric.source_ids if source_id not in usable_ids
                )
    for category in item.book_score.categories:
        bad_ids.update(
            source_id for source_id in category.company_source_ids if source_id not in usable_ids
        )

    # Unknown ids are separately reported by the deterministic evidence audit;
    # this check is specifically about resolved-but-empty/manual placeholders.
    unusable_resolved = sorted(bad_ids.intersection(source_by_id))
    if unusable_resolved or missing_block_evidence:
        details = []
        if unusable_resolved:
            details.append(f"unusable cited source ids {unusable_resolved}")
        if missing_block_evidence:
            details.append(f"blocks without readable evidence {missing_block_evidence}")
        _add(
            issues,
            "LIVE_CITATION_NOT_USABLE",
            "; ".join(details) + ".",
            ticker,
        )


def _scan_live_metric_policy(
    item: ScoredCompany,
    issues: list[PublicationIssue],
) -> None:
    """Independently enforce freshness/comparability on a final live report."""

    # FinalBriefing already validates this as a date. Keeping the helper small
    # and private avoids duplicating the public model contract here.
    cutoff = item.book_score.as_of_date
    ticker = item.research.company.ticker
    source_by_id = {source.source_id: source for source in item.research.sources}

    blocks_and_policy = (
        ("business", item.research.business, set(), FUNDAMENTAL_MAX_AGE_DAYS),
        (
            "fundamentals",
            item.research.fundamentals,
            LIVE_FUNDAMENTAL_CODES,
            FUNDAMENTAL_MAX_AGE_DAYS,
        ),
        (
            "management",
            item.research.management,
            LIVE_MANAGEMENT_CODES,
            MANAGEMENT_MAX_AGE_DAYS,
        ),
        ("technicals", item.research.technicals, LIVE_TECHNICAL_CODES, TECHNICAL_MAX_AGE_DAYS),
        ("valuation", item.research.valuation, LIVE_VALUATION_CODES, VALUATION_MAX_AGE_DAYS),
    )
    freshness_codes = {
        "business": "BUSINESS_METRICS_UNEXPECTED",
        "fundamentals": "FUNDAMENTAL_STALE",
        "management": "MANAGEMENT_NOT_CURRENT",
        "technicals": "TECHNICALS_NOT_CURRENT",
        "valuation": "VALUATION_NOT_CURRENT",
    }
    for field_name, block, allowed_codes, max_age_days in blocks_and_policy:
        valued_metrics = [metric for metric in block.metrics if metric.value is not None]
        unexpected = {
            metric.code
            for metric in valued_metrics
            if metric.code.lower().strip() not in allowed_codes
        }
        if unexpected:
            _add(
                issues,
                "LIVE_METRIC_CODE_UNEXPECTED",
                f"Unexpected {field_name} metric code(s): {sorted(unexpected)}.",
                ticker,
            )
        outside_window = [
            metric.code
            for metric in valued_metrics
            if metric.as_of_date is None
            or not 0 <= (cutoff - metric.as_of_date).days <= max_age_days
        ]
        if outside_window:
            _add(
                issues,
                freshness_codes[field_name],
                (
                    f"{field_name.title()} metric(s) {sorted(outside_window)} must be dated "
                    f"between the cutoff and {max_age_days} days before it."
                ),
                ticker,
            )

    fundamental_metrics = _metric_map(item.research.fundamentals)
    required = {"roe", "roce", "revenue_growth", "profit_growth", "cfo_pat"}
    protection = sum(
        (
            bool({"debt_equity", "net_debt_equity"}.intersection(fundamental_metrics)),
            "interest_coverage" in fundamental_metrics,
            "current_ratio" in fundamental_metrics,
        )
    )
    if not required.issubset(fundamental_metrics) or protection < 2:
        _add(
            issues,
            "FUNDAMENTAL_COVERAGE_INCOMPLETE",
            "Fresh ROE, ROCE, comparable growth, CFO/PAT and two protection categories are required.",
            ticker,
        )

    growth = [
        fundamental_metrics.get("revenue_growth"),
        fundamental_metrics.get("profit_growth"),
    ]
    if all(growth):
        revenue, profit = growth
        if (
            revenue.as_of_date != profit.as_of_date
            or revenue.period_type != profit.period_type
            or revenue.accounting_basis != profit.accounting_basis
        ):
            _add(
                issues,
                "GROWTH_NOT_COMPARABLE",
                "Revenue and PAT growth must share date, period type, and accounting basis.",
                ticker,
            )

    primary_ids = {
        source_id
        for source_id, source in source_by_id.items()
        if source.authority in {"primary_regulatory", "primary_company"} and source.excerpt.strip()
    }
    cited_fundamental_ids = {
        source_id for metric in fundamental_metrics.values() for source_id in metric.source_ids
    }
    if not primary_ids.intersection(cited_fundamental_ids):
        _add(
            issues,
            "FUNDAMENTALS_NOT_PRIMARY_VERIFIED",
            "At least one scored fundamental must cite a first-party source.",
            ticker,
        )

    valuation_metrics = _metric_map(item.research.valuation)
    pe = valuation_metrics.get("pe_ttm")
    if (
        pe is None
        or pe.as_of_date is None
        or not 0 <= (cutoff - pe.as_of_date).days <= VALUATION_MAX_AGE_DAYS
        or not isinstance(pe.value, (int, float))
        or pe.value <= 0
    ):
        _add(
            issues,
            "VALUATION_NOT_CURRENT",
            f"A positive cited TTM P/E within {VALUATION_MAX_AGE_DAYS} days is required.",
            ticker,
        )

    technical_metrics = _metric_map(item.research.technicals)
    required_technicals = {"close", "sma20", "sma50", "rsi14"}
    if not required_technicals.issubset(technical_metrics):
        _add(
            issues,
            "TECHNICALS_NOT_CURRENT",
            f"Close, SMA20, SMA50 and RSI(14) must be within {TECHNICAL_MAX_AGE_DAYS} days.",
            ticker,
        )


def _metric_map(block: AnalysisBlock) -> dict[str, Metric]:
    """Build a normalized map only for valued metrics.

    Duplicate codes are rejected separately.  Keeping this helper deliberately
    simple prevents a stale duplicate from being silently selected as the
    supposedly authoritative value during the final scan.
    """

    result: dict[str, Metric] = {}
    for metric in block.metrics:
        if metric.value is not None:
            result.setdefault(metric.code.lower().strip(), metric)
    return result


def _numeric_values(block: AnalysisBlock) -> dict[str, float]:
    """Return finite numeric metrics; qualitative strings are intentionally omitted."""

    values: dict[str, float] = {}
    for code, metric in _metric_map(block).items():
        if not _is_finite_number(metric.value):
            continue
        value = float(metric.value)
        values[code] = value
    return values


def _is_finite_number(value: object) -> bool:
    return (
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and math.isfinite(float(value))
    )


def _recompute_deterministic_blocks(
    item: ScoredCompany,
    *,
    mode: str,
    issues: list[PublicationIssue],
) -> tuple[AnalysisBlock, AnalysisBlock, AnalysisBlock]:
    """Recalculate the three numerical analyst scores used by Core Score.

    Management remains an evidence-backed qualitative judgement, so only its
    status, confidence, identity, and citations can be validated mechanically.
    """

    ticker = item.research.company.ticker
    fundamentals = item.research.fundamentals
    valuation = item.research.valuation
    technicals = item.research.technicals
    fundamental_values = _numeric_values(fundamentals)

    expected_quality: float | None = None
    expected_growth: float | None = None
    if mode == "demo":
        quality_codes = {"roe", "roce", "debt_equity", "operating_cash_flow"}
        growth_codes = {
            "revenue_growth",
            "profit_growth",
            "same_store_sales_growth",
            "operating_cash_flow",
        }
        if quality_codes.issubset(fundamental_values):
            expected_quality = _demo_fundamental_quality(
                {
                    "roe_pct": fundamental_values["roe"],
                    "roce_pct": fundamental_values["roce"],
                    "debt_to_equity": fundamental_values["debt_equity"],
                    "operating_cash_flow_crore": fundamental_values["operating_cash_flow"],
                }
            )
        if growth_codes.issubset(fundamental_values):
            expected_growth = _demo_growth_quality(
                {
                    "revenue_growth_pct": fundamental_values["revenue_growth"],
                    "pat_growth_pct": fundamental_values["profit_growth"],
                    "same_store_sales_growth_pct": fundamental_values[
                        "same_store_sales_growth"
                    ],
                },
                {"operating_cash_flow_crore": fundamental_values["operating_cash_flow"]},
            )
    else:
        required = {"roe", "roce", "revenue_growth", "profit_growth", "cfo_pat"}
        protection = sum(
            (
                bool({"debt_equity", "net_debt_equity"}.intersection(fundamental_values)),
                "interest_coverage" in fundamental_values,
                "current_ratio" in fundamental_values,
            )
        )
        if required.issubset(fundamental_values) and protection >= 2:
            expected_quality = _fundamental_quality_score(fundamental_values)
            expected_growth = _fundamental_growth_score(fundamental_values)

    if expected_quality is not None:
        if fundamentals.score != expected_quality:
            _add(
                issues,
                "FUNDAMENTALS_SCORE_MISMATCH",
                f"Fundamentals score {fundamentals.score!r} should be {expected_quality!r}.",
                ticker,
            )
        if fundamentals.growth_score != expected_growth:
            _add(
                issues,
                "GROWTH_SCORE_MISMATCH",
                f"Growth score {fundamentals.growth_score!r} should be {expected_growth!r}.",
                ticker,
            )
        fundamentals = fundamentals.model_copy(
            update={"score": expected_quality, "growth_score": expected_growth}
        )

    technical_values = _numeric_values(technicals)
    expected_technical: float | None = None
    if LIVE_TECHNICAL_CODES.issubset(technical_values):
        expected_technical = _technical_score(
            close=technical_values["close"],
            sma20=technical_values["sma20"],
            sma50=technical_values["sma50"],
            rsi14=technical_values["rsi14"],
        )
    if expected_technical is not None:
        if technicals.score != expected_technical:
            _add(
                issues,
                "TECHNICAL_SCORE_MISMATCH",
                f"Technical score {technicals.score!r} should be {expected_technical!r}.",
                ticker,
            )
        technicals = technicals.model_copy(update={"score": expected_technical})

    valuation_values = _numeric_values(valuation)
    pe = valuation_values.get("pe_ttm")
    growth_for_valuation = expected_growth
    if growth_for_valuation is None:
        growth_for_valuation = fundamentals.growth_score
    if pe is not None and growth_for_valuation is not None:
        if mode == "demo":
            growth_for_valuation = growth_for_valuation or 50.0
        expected_valuation = round(
            max(15.0, min(90.0, 92 - pe * 1.35 + growth_for_valuation * 0.25)),
            2,
        )
        if valuation.score != expected_valuation:
            _add(
                issues,
                "VALUATION_SCORE_MISMATCH",
                f"Valuation score {valuation.score!r} should be {expected_valuation!r}.",
                ticker,
            )
        valuation = valuation.model_copy(update={"score": expected_valuation})

    return fundamentals, valuation, technicals
