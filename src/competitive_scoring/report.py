"""Turn structured graph output into a portable Markdown briefing."""

from __future__ import annotations

from .models import FinalBriefing, Metric, ScoredCompany


def _cell(value: object) -> str:
    """Keep user/model text from breaking a Markdown table."""

    return str(value).replace("|", "\\|").replace("\n", " ")


def _metric(company: ScoredCompany, code: str) -> Metric | None:
    return company.research.metric_map().get(code)


def _metric_text(company: ScoredCompany, code: str) -> str:
    metric = _metric(company, code)
    if metric is None or metric.value is None:
        return "Unknown"
    return f"{metric.value}{metric.unit if metric.unit in {'x', '%'} else (' ' + metric.unit if metric.unit else '')}"


def render_markdown(briefing: FinalBriefing) -> str:
    """Build the report without another model call, so the numbers cannot drift."""

    demo_banner = ""
    if briefing.mode == "demo":
        demo_banner = (
            "> **Illustrative demo:** every company number below is invented for software "
            "testing. Source URLs are reference-only and do not support the fixture values.\n\n"
        )

    lines = [
        f"# {briefing.title}",
        "",
        demo_banner.rstrip(),
        "",
        f"**Research date:** {briefing.as_of_date.isoformat()}  ",
        f"**Target:** {briefing.target.name} ({briefing.target.ticker}:NSE)  ",
        "**Horizon:** 2–3 years",
        "",
        "## Executive result",
        "",
        briefing.conclusion,
        "",
        "| Rank | Company | Core /100 | Book alignment /100 | Final /100 | Confidence |",
        "|---:|---|---:|---:|---:|---:|",
    ]

    for item in briefing.companies:
        book = item.book_score.total_score
        final = item.final_score
        core = item.research.core_score
        lines.append(
            "| {rank} | {name} ({ticker}) | {core} | {book} | {final} | {confidence:.0%} |".format(
                rank=item.rank or "–",
                name=_cell(item.research.company.name),
                ticker=item.research.company.ticker,
                core=f"{core:.2f}" if core is not None else "Not scored",
                book=f"{book:.2f}" if book is not None else "Not scored",
                final=f"{final:.2f}" if final is not None else "Not ranked",
                confidence=item.research.confidence,
            )
        )

    lines.extend(
        [
            "",
            "## Comparable indicators",
            "",
            "| Company | ROE | ROCE | Revenue growth | P/E | Close | SMA20 | SMA50 | RSI(14) |",
            "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for item in briefing.companies:
        lines.append(
            f"| {item.research.company.ticker} | {_metric_text(item, 'roe')} | "
            f"{_metric_text(item, 'roce')} | {_metric_text(item, 'revenue_growth')} | "
            f"{_metric_text(item, 'pe_ttm')} | {_metric_text(item, 'close')} | "
            f"{_metric_text(item, 'sma20')} | {_metric_text(item, 'sma50')} | "
            f"{_metric_text(item, 'rsi14')} |"
        )

    for item in briefing.companies:
        report = item.research
        lines.extend(
            [
                "",
                f"## {item.rank or 'Unranked'}. {report.company.name} ({report.company.ticker})",
                "",
                f"- **Business:** {report.business.summary}",
                f"- **Fundamentals and growth:** {report.fundamentals.summary}",
                f"- **Management:** {report.management.summary}",
                f"- **Valuation:** {report.valuation.summary}",
                f"- **Technical timing:** {report.technicals.summary}",
                "",
                "### Peaceful Investing alignment",
                "",
                "| Category | Weight | Score | Points | Book pages (printed / PDF) |",
                "|---|---:|---:|---:|---|",
            ]
        )
        for category in item.book_score.categories:
            pages = (
                ", ".join(
                    f"{citation.printed_page} / {citation.pdf_page}"
                    for citation in category.book_basis
                )
                or "Unresolved"
            )
            score = str(category.score) if category.score is not None else "Unknown"
            points = (
                f"{category.weighted_points:.2f}"
                if category.weighted_points is not None
                else "Unknown"
            )
            lines.append(
                f"| {_cell(category.label)} | {category.weight}% | {score}/5 | {points} | {pages} |"
            )
        lines.extend(
            [
                "",
                (
                    f"Book evidence coverage: **{item.book_score.coverage_weight}%**. "
                    f"Status: **{item.book_score.decision_status}**."
                ),
                "",
                "### Sources",
                "",
            ]
        )
        for source in report.sources:
            qualifier = " — reference only (demo)" if briefing.mode == "demo" else ""
            lines.append(f"- [{_cell(source.title)}]({source.url}){qualifier}")

    lines.extend(
        [
            "",
            "## Evidence audit",
            "",
            (
                f"Audit passed: **{'yes' if briefing.audit.passed else 'no'}**; "
                f"targeted retries used: **{briefing.audit.retry_count}**."
            ),
        ]
    )
    for finding in briefing.audit.findings:
        label = f" [{finding.company_ticker}]" if finding.company_ticker else ""
        lines.append(f"- {finding.severity.upper()}{label} `{finding.code}`: {finding.message}")

    lines.extend(
        [
            "",
            "## Method",
            "",
            briefing.methodology,
            "",
            (
                "The book is used only as a private methodology source. Current company claims "
                "must come from dated company/exchange/web/market evidence. The book-alignment "
                "score measures framework fit; it is not a recommendation by itself."
            ),
            "",
            f"> {briefing.disclaimer}",
        ]
    )
    return "\n".join(line for line in lines if line is not None).strip() + "\n"
