"""Small, deterministic data set for the offline scoring demo.

Every number in this module is intentionally illustrative.  The values are useful
for demonstrating sorting, scoring, citations, and UI states, but they are *not*
current market facts and must never be presented as investment research.

The public helpers return ordinary dictionaries and defensive copies.  That keeps
the fixture independent of Pydantic/LangChain models and prevents one demo run
from accidentally changing the data used by the next run.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

DISCLAIMER = (
    "ILLUSTRATIVE OFFLINE FIXTURE — all numbers are invented for software "
    "demonstration and testing. They are not current investment facts, live "
    "quotes, research, recommendations, or financial advice."
)

DEMO_TICKERS = ("PNGJL", "KALYANKJIL", "SENCO", "THANGAMAYL")


# These are real reference locations that a live research run could inspect.
# They are deliberately NOT claimed as evidence for the invented values below.
_SOURCES: dict[str, dict[str, Any]] = {
    "pngjl_official_ir": {
        "source_id": "pngjl_official_ir",
        "ticker": "PNGJL",
        "title": "P N Gadgil Jewellers investor-relations page",
        "kind": "official_company_reference",
        "url": "https://www.pngjewellers.com/pages/investors",
        "supports_fixture_numbers": False,
        "fixture_note": "Reference URL only; no live retrieval occurs in demo mode.",
    },
    "pngjl_nse_quote": {
        "source_id": "pngjl_nse_quote",
        "ticker": "PNGJL",
        "title": "NSE quote page for PNGJL",
        "kind": "official_exchange_reference",
        "url": "https://www.nseindia.com/get-quotes/equity?symbol=PNGJL",
        "supports_fixture_numbers": False,
        "fixture_note": "Reference URL only; technical values are illustrative.",
    },
    "kalyan_official_reports": {
        "source_id": "kalyan_official_reports",
        "ticker": "KALYANKJIL",
        "title": "Kalyan Jewellers annual-report page",
        "kind": "official_company_reference",
        "url": "https://www.kalyanjewellers.net/investors/annual-report/annual-reports.php",
        "supports_fixture_numbers": False,
        "fixture_note": "Reference URL only; no live retrieval occurs in demo mode.",
    },
    "kalyan_nse_quote": {
        "source_id": "kalyan_nse_quote",
        "ticker": "KALYANKJIL",
        "title": "NSE quote page for KALYANKJIL",
        "kind": "official_exchange_reference",
        "url": "https://www.nseindia.com/get-quotes/equity?symbol=KALYANKJIL",
        "supports_fixture_numbers": False,
        "fixture_note": "Reference URL only; technical values are illustrative.",
    },
    "senco_nse_filing": {
        "source_id": "senco_nse_filing",
        "ticker": "SENCO",
        "title": "NSE-hosted Senco Gold financial filing",
        "kind": "official_exchange_reference",
        "url": (
            "https://nsearchives.nseindia.com/corporate/ixbrl/"
            "INTEGRATED_FILING_INDAS_161677_26052026221807_iXBRL_WEB.html"
        ),
        "supports_fixture_numbers": False,
        "fixture_note": "Reference URL only; no live retrieval occurs in demo mode.",
    },
    "senco_nse_quote": {
        "source_id": "senco_nse_quote",
        "ticker": "SENCO",
        "title": "NSE quote page for SENCO",
        "kind": "official_exchange_reference",
        "url": "https://www.nseindia.com/get-quotes/equity?symbol=SENCO",
        "supports_fixture_numbers": False,
        "fixture_note": "Reference URL only; technical values are illustrative.",
    },
    "thangamayil_official_report": {
        "source_id": "thangamayil_official_report",
        "ticker": "THANGAMAYL",
        "title": "Thangamayil Jewellery annual-report reference",
        "kind": "official_company_reference",
        "url": (
            "https://www.thangamayil.com/corporate/wp-content/uploads/2026/07/"
            "26th-Annual-Report-2025-2026.pdf"
        ),
        "supports_fixture_numbers": False,
        "fixture_note": "Reference URL only; no live retrieval occurs in demo mode.",
    },
    "thangamayil_nse_quote": {
        "source_id": "thangamayil_nse_quote",
        "ticker": "THANGAMAYL",
        "title": "NSE quote page for THANGAMAYL",
        "kind": "official_exchange_reference",
        "url": "https://www.nseindia.com/get-quotes/equity?symbol=THANGAMAYL",
        "supports_fixture_numbers": False,
        "fixture_note": "Reference URL only; technical values are illustrative.",
    },
}


# Keep every company in the same shape.  Beginners can therefore iterate over
# sections without special cases, while still seeing Thangamayil's standalone
# reporting-basis caveat.
_COMPANIES: dict[str, dict[str, Any]] = {
    "PNGJL": {
        "ticker": "PNGJL",
        "company_name": "P N Gadgil Jewellers Limited",
        "is_target": True,
        "data_status": "illustrative_offline_fixture",
        "disclaimer": DISCLAIMER,
        "business": {
            "summary": "Illustrative western-India-led jewellery retailer profile.",
            "core_market": "Maharashtra-led",
            "business_model": "Predominantly company-operated retail with expansion",
            "positioning": "Heritage-led trusted regional brand",
            "key_risks": [
                "Regional concentration",
                "Inventory-intensive expansion",
                "Execution and funding risk during store rollout",
            ],
        },
        "fundamentals": {
            "period_label": "ILLUSTRATIVE_FY",
            "reporting_basis": "consolidated_demo_basis",
            "measurement_type": "illustrative_peer_normalized",
            "revenue_crore": 10_800.0,
            "pat_crore": 450.0,
            "ebitda_margin_pct": 7.7,
            "pat_margin_pct": 4.2,
            "roe_pct": 23.0,
            "roce_pct": 22.0,
            "operating_cash_flow_crore": -350.0,
            "free_cash_flow_crore": -420.0,
            "cfo_pat_ratio": -0.78,
            "debt_to_equity": 0.82,
            "interest_coverage": 5.2,
            "current_ratio": 1.18,
            "inventory_days": 112,
            "working_capital_days": 98,
            "history_years": 4,
            "self_sustainable_growth_pct": 14.0,
        },
        "growth": {
            "period_label": "ILLUSTRATIVE_YOY",
            "revenue_growth_pct": 36.0,
            "pat_growth_pct": 44.0,
            "same_store_sales_growth_pct": 20.0,
            "store_count_growth_pct": 18.0,
            "quality_note": "Strong demo growth with a working-capital watch item.",
        },
        "management": {
            "score_100": 78.0,
            "credit_rating": "A+ Stable (illustrative)",
            "ownership_style": "promoter_led_demo_profile",
            "strengths": ["Brand stewardship", "Growth execution"],
            "watch_items": [
                "Capital-allocation discipline",
                "Equity dilution and inventory funding",
                "Geographic diversification",
            ],
        },
        "valuation": {
            "period_label": "ILLUSTRATIVE_TTM",
            "market_cap_crore": 9_000.0,
            "pe_ttm": 20.0,
            "peg": 0.56,
            "price_to_sales": 0.83,
            "gsec_10y_yield_pct": 6.9,
            "ev_to_ebitda_ttm": 14.0,
            "price_to_book": 4.3,
            "valuation_note": "Moderate demo valuation relative to the growth inputs.",
        },
        "technicals": {
            "period_label": "ILLUSTRATIVE_FIXTURE_DAY_100",
            "close": 580.0,
            "sma_20": 572.0,
            "sma_50": 548.0,
            "rsi_14": 57.0,
            "trend_label": "positive_demo_trend",
        },
        "freshness": {
            "clock": "FIXTURE_DAY_100",
            "age_days": 1,
            "score_100": 95.0,
            "scope": "offline_fixture_recency_only",
        },
        "confidence": {
            "score_100": 90.0,
            "scope": "fixture_completeness_only",
            "real_world_accuracy_asserted": False,
        },
        "reference_source_ids": ["pngjl_official_ir", "pngjl_nse_quote"],
    },
    "KALYANKJIL": {
        "ticker": "KALYANKJIL",
        "company_name": "Kalyan Jewellers India Limited",
        "is_target": False,
        "data_status": "illustrative_offline_fixture",
        "disclaimer": DISCLAIMER,
        "business": {
            "summary": "Illustrative scaled India-and-international jewellery profile.",
            "core_market": "Pan-India with international presence",
            "business_model": "Mixed owned and franchise-operated network",
            "positioning": "Scaled national wedding-jewellery brand",
            "key_risks": [
                "Premium valuation",
                "Franchise execution consistency",
                "Margin pressure during rapid expansion",
            ],
        },
        "fundamentals": {
            "period_label": "ILLUSTRATIVE_FY",
            "reporting_basis": "consolidated_demo_basis",
            "measurement_type": "illustrative_peer_normalized",
            "revenue_crore": 36_000.0,
            "pat_crore": 1_400.0,
            "ebitda_margin_pct": 7.0,
            "pat_margin_pct": 3.9,
            "roe_pct": 24.5,
            "roce_pct": 24.0,
            "operating_cash_flow_crore": 900.0,
            "free_cash_flow_crore": 500.0,
            "cfo_pat_ratio": 0.64,
            "debt_to_equity": 0.90,
            "interest_coverage": 4.5,
            "current_ratio": 1.10,
            "inventory_days": 145,
            "working_capital_days": 118,
            "history_years": 6,
            "self_sustainable_growth_pct": 18.0,
        },
        "growth": {
            "period_label": "ILLUSTRATIVE_YOY",
            "revenue_growth_pct": 30.0,
            "pat_growth_pct": 35.0,
            "same_store_sales_growth_pct": 18.0,
            "store_count_growth_pct": 25.0,
            "quality_note": "Broad-based demo growth supported by an asset-light mix.",
        },
        "management": {
            "score_100": 87.0,
            "credit_rating": "AA- Stable (illustrative)",
            "ownership_style": "promoter_led_demo_profile",
            "strengths": ["Scaled execution", "Network expansion", "Debt discipline"],
            "watch_items": [
                "Franchise controls",
                "New-format profitability",
                "Margin dilution",
            ],
        },
        "valuation": {
            "period_label": "ILLUSTRATIVE_TTM",
            "market_cap_crore": 60_000.0,
            "pe_ttm": 42.0,
            "peg": 1.20,
            "price_to_sales": 1.67,
            "gsec_10y_yield_pct": 6.9,
            "ev_to_ebitda_ttm": 25.0,
            "price_to_book": 9.0,
            "valuation_note": "High demo quality accompanied by a premium multiple.",
        },
        "technicals": {
            "period_label": "ILLUSTRATIVE_FIXTURE_DAY_100",
            "close": 590.0,
            "sma_20": 602.0,
            "sma_50": 565.0,
            "rsi_14": 49.0,
            "trend_label": "mixed_demo_trend",
        },
        "freshness": {
            "clock": "FIXTURE_DAY_100",
            "age_days": 2,
            "score_100": 92.0,
            "scope": "offline_fixture_recency_only",
        },
        "confidence": {
            "score_100": 92.0,
            "scope": "fixture_completeness_only",
            "real_world_accuracy_asserted": False,
        },
        "reference_source_ids": ["kalyan_official_reports", "kalyan_nse_quote"],
    },
    "SENCO": {
        "ticker": "SENCO",
        "company_name": "Senco Gold Limited",
        "is_target": False,
        "data_status": "illustrative_offline_fixture",
        "disclaimer": DISCLAIMER,
        "business": {
            "summary": "Illustrative east-India-led jewellery retailer profile.",
            "core_market": "East-India-led",
            "business_model": "Mixed owned and franchise-operated network",
            "positioning": "Design and craftsmanship-led regional challenger",
            "key_risks": [
                "Long working-capital cycle",
                "Earnings sensitivity to inventory gains",
                "Subsidiary and acquisition execution",
            ],
        },
        "fundamentals": {
            "period_label": "ILLUSTRATIVE_FY",
            "reporting_basis": "consolidated_demo_basis",
            "measurement_type": "illustrative_peer_normalized",
            "revenue_crore": 9_000.0,
            "pat_crore": 400.0,
            "ebitda_margin_pct": 8.2,
            "pat_margin_pct": 4.4,
            "roe_pct": 20.0,
            "roce_pct": 19.0,
            "operating_cash_flow_crore": -600.0,
            "free_cash_flow_crore": -700.0,
            "cfo_pat_ratio": -1.50,
            "debt_to_equity": 1.05,
            "interest_coverage": 3.0,
            "current_ratio": 1.05,
            "inventory_days": 215,
            "working_capital_days": 195,
            "history_years": 6,
            "self_sustainable_growth_pct": 10.0,
        },
        "growth": {
            "period_label": "ILLUSTRATIVE_YOY",
            "revenue_growth_pct": 24.0,
            "pat_growth_pct": 18.0,
            "same_store_sales_growth_pct": 14.0,
            "store_count_growth_pct": 12.0,
            "quality_note": "Value-looking demo profile with lower cash conversion.",
        },
        "management": {
            "score_100": 68.0,
            "credit_rating": "A Stable (illustrative)",
            "ownership_style": "promoter_led_demo_profile",
            "strengths": ["Regional brand knowledge", "Product craftsmanship"],
            "watch_items": [
                "Cash conversion",
                "Adjusted versus reported earnings",
                "Acquisition integration",
            ],
        },
        "valuation": {
            "period_label": "ILLUSTRATIVE_TTM",
            "market_cap_crore": 6_000.0,
            "pe_ttm": 15.0,
            "peg": 0.83,
            "price_to_sales": 0.67,
            "gsec_10y_yield_pct": 6.9,
            "ev_to_ebitda_ttm": 10.0,
            "price_to_book": 2.3,
            "valuation_note": "Low demo multiple offset by earnings-quality risks.",
        },
        "technicals": {
            "period_label": "ILLUSTRATIVE_FIXTURE_DAY_100",
            "close": 350.0,
            "sma_20": 360.0,
            "sma_50": 372.0,
            "rsi_14": 42.0,
            "trend_label": "negative_demo_trend",
        },
        "freshness": {
            "clock": "FIXTURE_DAY_100",
            "age_days": 5,
            "score_100": 82.0,
            "scope": "offline_fixture_recency_only",
        },
        "confidence": {
            "score_100": 78.0,
            "scope": "fixture_completeness_only",
            "real_world_accuracy_asserted": False,
        },
        "reference_source_ids": ["senco_nse_filing", "senco_nse_quote"],
    },
    "THANGAMAYL": {
        "ticker": "THANGAMAYL",
        "company_name": "Thangamayil Jewellery Limited",
        "is_target": False,
        "data_status": "illustrative_offline_fixture",
        "disclaimer": DISCLAIMER,
        "business": {
            "summary": "Illustrative Tamil-Nadu-focused jewellery retailer profile.",
            "core_market": "Tamil Nadu",
            "business_model": "Regional company-operated retail network",
            "positioning": "High-return regional specialist",
            "key_risks": [
                "Single-state concentration",
                "Premium valuation",
                "Commodity and inventory-profit-related earnings volatility",
            ],
        },
        "fundamentals": {
            "period_label": "ILLUSTRATIVE_FY",
            "reporting_basis": "standalone_demo_basis",
            "measurement_type": "illustrative_peer_normalized",
            "revenue_crore": 8_500.0,
            "pat_crore": 360.0,
            "ebitda_margin_pct": 6.4,
            "pat_margin_pct": 4.2,
            "roe_pct": 28.0,
            "roce_pct": 26.0,
            "operating_cash_flow_crore": 250.0,
            "free_cash_flow_crore": 150.0,
            "cfo_pat_ratio": 0.69,
            "debt_to_equity": 0.58,
            "interest_coverage": 5.5,
            "current_ratio": 1.20,
            "inventory_days": 120,
            "working_capital_days": 112,
            "history_years": 8,
            "self_sustainable_growth_pct": 20.0,
        },
        "growth": {
            "period_label": "ILLUSTRATIVE_YOY",
            "revenue_growth_pct": 35.0,
            "pat_growth_pct": 40.0,
            "same_store_sales_growth_pct": 22.0,
            "store_count_growth_pct": 15.0,
            "quality_note": "High-return demo growth with regional concentration.",
        },
        "management": {
            "score_100": 80.0,
            "credit_rating": "A+ Stable (illustrative)",
            "ownership_style": "promoter_led_demo_profile",
            "strengths": ["Regional execution", "Inventory discipline"],
            "watch_items": [
                "Geographic concentration",
                "Expansion outside core markets",
                "Commodity-related profit normalization",
            ],
        },
        "valuation": {
            "period_label": "ILLUSTRATIVE_TTM",
            "market_cap_crore": 16_500.0,
            "pe_ttm": 40.0,
            "peg": 1.00,
            "price_to_sales": 1.94,
            "gsec_10y_yield_pct": 6.9,
            "ev_to_ebitda_ttm": 27.0,
            "price_to_book": 11.0,
            "valuation_note": "Strong demo returns with limited valuation cushion.",
        },
        "technicals": {
            "period_label": "ILLUSTRATIVE_FIXTURE_DAY_100",
            "close": 5_300.0,
            "sma_20": 5_180.0,
            "sma_50": 4_940.0,
            "rsi_14": 64.0,
            "trend_label": "positive_demo_trend",
        },
        "freshness": {
            "clock": "FIXTURE_DAY_100",
            "age_days": 3,
            "score_100": 88.0,
            "scope": "offline_fixture_recency_only",
        },
        "confidence": {
            "score_100": 84.0,
            "scope": "fixture_completeness_only",
            "real_world_accuracy_asserted": False,
        },
        "reference_source_ids": [
            "thangamayil_official_report",
            "thangamayil_nse_quote",
        ],
    },
}


_METADATA: dict[str, Any] = {
    "dataset_id": "pngjl_peer_illustrative_demo_v1",
    "schema_version": 1,
    "data_status": "illustrative_offline_fixture",
    "is_illustrative": True,
    "not_for_investment_decisions": True,
    "fixture_clock": "FIXTURE_DAY_100",
    "live_as_of": None,
    "currency": "INR",
    "money_unit": "crore",
    "disclaimer": DISCLAIMER,
    "ticker_order": list(DEMO_TICKERS),
    "comparison_note": (
        "The first three demo profiles use a consolidated label; THANGAMAYL uses "
        "a standalone label and should be shown with that comparability caveat."
    ),
    "technical_conventions": {
        "sma_20": "Illustrative 20-session simple moving average.",
        "sma_50": "Illustrative 50-session simple moving average.",
        "rsi_14": (
            "Illustrative 14-session Relative Strength Index. The UI may accept "
            "the common user typo 'RSA', but the canonical field is rsi_14."
        ),
    },
}


def get_demo_dataset() -> dict[str, Any]:
    """Return the complete offline fixture as a fresh plain dictionary."""

    return deepcopy(
        {
            "metadata": _METADATA,
            "sources": _SOURCES,
            "companies": _COMPANIES,
        }
    )


def get_demo_companies() -> dict[str, dict[str, Any]]:
    """Return all four company records, keyed by their NSE ticker."""

    return deepcopy(_COMPANIES)


def get_demo_company(ticker: str) -> dict[str, Any]:
    """Return one company fixture; ticker matching is whitespace/case tolerant."""

    if not isinstance(ticker, str):
        raise TypeError("ticker must be a string")

    normalized = ticker.strip().upper()
    if normalized not in _COMPANIES:
        allowed = ", ".join(DEMO_TICKERS)
        raise KeyError(f"Unknown demo ticker {ticker!r}. Choose one of: {allowed}")
    return deepcopy(_COMPANIES[normalized])


def get_demo_sources() -> dict[str, dict[str, Any]]:
    """Return the reference-source catalogue used to populate citation widgets."""

    return deepcopy(_SOURCES)
