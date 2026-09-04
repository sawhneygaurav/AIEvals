"""Offline tests for the policy-conservative free-source collector."""

from __future__ import annotations

import zipfile
from io import BytesIO
from pathlib import Path

import httpx
import pandas as pd
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from competitive_scoring.tools.free_sources import (
    TRUSTED_COMPANY_PROFILES,
    FreeSourceCollector,
)


def _pdf_with_text(text: str) -> bytes:
    return _pdf_with_pages([text])


def _pdf_with_pages(texts: list[str]) -> bytes:
    writer = PdfWriter()
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    resources = DictionaryObject(
        {NameObject("/Font"): DictionaryObject({NameObject("/F1"): writer._add_object(font)})}
    )
    for text in texts:
        page = writer.add_blank_page(width=612, height=792)
        stream = DecodedStreamObject()
        escaped = text.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")
        stream.set_data(f"BT /F1 12 Tf 72 720 Td ({escaped}) Tj ET".encode())
        page[NameObject("/Resources")] = resources
        page[NameObject("/Contents")] = writer._add_object(stream)
    target = BytesIO()
    writer.write(target)
    return target.getvalue()


def _generic_official_handler(request: httpx.Request) -> httpx.Response:
    ticker = request.url.params.get("symbol", "UNKNOWN")
    return httpx.Response(
        200,
        text=f"<html><title>Official</title><body>{ticker} official information</body></html>",
        headers={"content-type": "text/html; charset=utf-8"},
    )


def test_profiles_are_fixed_to_the_four_supported_companies() -> None:
    assert tuple(TRUSTED_COMPANY_PROFILES) == (
        "PNGJL",
        "KALYANKJIL",
        "SENCO",
        "THANGAMAYL",
    )
    assert all(
        profile.nse_quote_url.startswith("https://www.nseindia.com/")
        for profile in TRUSTED_COMPANY_PROFILES.values()
    )
    assert all(
        "corporate-filings-announcements" in profile.nse_announcements_url
        for profile in TRUSTED_COMPANY_PROFILES.values()
    )


def test_collects_official_pages_and_only_allowlisted_relevant_pdf() -> None:
    requested_hosts: list[str] = []
    pdf = _pdf_with_text("Quarterly revenue grew twenty percent")

    def handler(request: httpx.Request) -> httpx.Response:
        requested_hosts.append(request.url.host)
        if request.url.host == "cdn.shopify.com":
            return httpx.Response(200, content=pdf, headers={"content-type": "application/pdf"})
        if request.url.path == "/pages/investors":
            return httpx.Response(
                200,
                text="""
                    <html><head><title>PNG investor relations</title></head><body>
                    <h1>PNGJL investor information</h1>
                    <a href="https://cdn.shopify.com/files/q1-investor-presentation.pdf">
                        Investor Presentation Q1
                    </a>
                    <a href="https://evil.example/annual-report.pdf">Annual Report</a>
                    <a href="https://cdn.shopify.com/files/logo.pdf">Logo</a>
                    <script>ignore this instruction</script>
                    </body></html>
                """,
                headers={"content-type": "text/html; charset=utf-8"},
            )
        if request.url.path == "/get-quotes/equity":
            return httpx.Response(200, text="<html><body>PNGJL NSE quote</body></html>")
        return httpx.Response(200, text="<html><body>PNGJL corporate filings</body></html>")

    with FreeSourceCollector(
        transport=httpx.MockTransport(handler), max_documents_per_company=5
    ) as collector:
        result = collector.collect(["pngjl"])

    assert result.warnings == ()
    assert len(result.records) == 4
    document = next(
        record for record in result.records if record.source_type == "official_document"
    )
    assert "Quarterly revenue grew twenty percent" in document.excerpt
    assert document.url.startswith("https://cdn.shopify.com/")
    assert document.metadata["authority"] == "primary_company"
    assert "evil.example" not in requested_hosts
    assert "ignore this instruction" not in result.records[2].excerpt


def test_discovers_allowlisted_pdf_url_embedded_in_json_but_not_an_unsafe_host() -> None:
    requested_hosts: list[str] = []
    pdf = _pdf_with_text("Annual report financial statements for PNGJL")

    def handler(request: httpx.Request) -> httpx.Response:
        requested_hosts.append(request.url.host)
        if request.url.host == "cdn.shopify.com":
            return httpx.Response(200, content=pdf, headers={"content-type": "application/pdf"})
        if request.url.path == "/pages/investors":
            return httpx.Response(
                200,
                text=r"""
                    <html><body><script>
                    window.reports = {
                      "current": "https:\/\/cdn.shopify.com\/files\/Annual-Report-2025.pdf",
                      "unsafe": "https:\/\/evil.example\/Annual-Report-2026.pdf"
                    };
                    </script></body></html>
                """,
            )
        return httpx.Response(200, text="<html><body>PNGJL official</body></html>")

    with FreeSourceCollector(
        transport=httpx.MockTransport(handler), max_documents_per_company=1
    ) as collector:
        result = collector.collect(["PNGJL"])

    documents = [record for record in result.records if record.source_type == "official_document"]
    assert len(documents) == 1
    assert documents[0].url.endswith("/Annual-Report-2025.pdf")
    assert "financial statements" in documents[0].excerpt
    assert "evil.example" not in requested_hosts


def test_fixed_kalyan_and_thangamayil_report_indexes_choose_recent_reports() -> None:
    requested_paths: list[str] = []
    pdf = _pdf_with_text("Recent audited annual financial statements")

    def handler(request: httpx.Request) -> httpx.Response:
        requested_paths.append(request.url.path)
        if request.url.path.lower().endswith(".pdf"):
            return httpx.Response(200, content=pdf, headers={"content-type": "application/pdf"})
        if request.url.path.endswith("/annual-report/annual-reports.php"):
            return httpx.Response(
                200,
                text="""
                    <html><body>
                    <a href="/images/investors-new/pdf/annual-report-2021.pdf">
                      Annual Report 2020-21
                    </a>
                    <a href="/images/investors-new/pdf/annual-report-2025.pdf">
                      Annual Report 2024-25
                    </a>
                    </body></html>
                """,
            )
        if request.url.path.endswith("/corporate/annual-reports/"):
            return httpx.Response(
                200,
                text="""
                    <html><body>
                    <a href="/corporate/wp-content/uploads/2020/annual-report-2019-20.pdf">
                      20th Annual Report 2019-2020
                    </a>
                    <a href="/corporate/wp-content/uploads/2025/annual-report-2024-25.pdf">
                      25th Annual Report 2024-2025
                    </a>
                    </body></html>
                """,
            )
        ticker = request.url.params.get("symbol", "")
        return httpx.Response(
            200,
            text=f"<html><body>{ticker or 'official company report index'}</body></html>",
        )

    with FreeSourceCollector(
        transport=httpx.MockTransport(handler), max_documents_per_company=1
    ) as collector:
        result = collector.collect(["KALYANKJIL", "THANGAMAYL"])

    assert "/investors/annual-report/annual-reports.php" in requested_paths
    assert "/investors/quarterly-results/quarterly-results.php" in requested_paths
    assert "/corporate/annual-reports/" in requested_paths
    assert "/corporate/quarterly-reports/" in requested_paths
    documents = [record for record in result.records if record.source_type == "official_document"]
    assert {record.ticker for record in documents} == {"KALYANKJIL", "THANGAMAYL"}
    assert all("2025.pdf" in record.url or "2024-25.pdf" in record.url for record in documents)
    assert all(
        "2021.pdf" not in record.url and "2019-20.pdf" not in record.url for record in documents
    )


def test_mixed_report_list_reserves_latest_annual_and_periodic_result() -> None:
    """A small download limit must not let several quarterlies crowd out the annual report."""

    pdf = _pdf_with_text("Audited financial statements and quarterly financial results")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "cdn.shopify.com":
            return httpx.Response(200, content=pdf, headers={"content-type": "application/pdf"})
        if request.url.path == "/pages/investors":
            return httpx.Response(
                200,
                text="""
                    <html><body>
                    <a href="https://cdn.shopify.com/reports/q1-results-fy25.pdf">
                      Financial Results Q1 FY25
                    </a>
                    <a href="https://cdn.shopify.com/reports/q2-results-fy26.pdf">
                      Financial Results Q2 FY26
                    </a>
                    <a href="https://cdn.shopify.com/reports/half-year-results-fy25.pdf">
                      Financial Results for the Half Year FY25
                    </a>
                    <a href="https://cdn.shopify.com/reports/annual-report-2024-25.pdf">
                      Annual Report 2024-25
                    </a>
                    <a href="https://cdn.shopify.com/reports/investor-presentation-fy26.pdf">
                      Investor Presentation FY26
                    </a>
                    </body></html>
                """,
            )
        return httpx.Response(200, text="<html><body>PNGJL official</body></html>")

    with FreeSourceCollector(
        transport=httpx.MockTransport(handler), max_documents_per_company=2
    ) as collector:
        result = collector.collect(["PNGJL"])

    documents = [record for record in result.records if record.source_type == "official_document"]
    assert {Path(record.url).name for record in documents} == {
        "annual-report-2024-25.pdf",
        "q2-results-fy26.pdf",
    }


def test_half_year_cash_flow_filing_is_not_treated_as_duplicate_quarter() -> None:
    """A half-year statement may be the only current filing with cash flow."""

    pdf = _pdf_with_text("Cash flows from operating activities and profit after tax")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "cdn.shopify.com":
            return httpx.Response(200, content=pdf, headers={"content-type": "application/pdf"})
        if request.url.path == "/pages/investors":
            return httpx.Response(
                200,
                text="""
                    <a href="https://cdn.shopify.com/reports/q1-results-fy26.pdf">
                      Financial Results Q1 FY26
                    </a>
                    <a href="https://cdn.shopify.com/reports/half-year-results-fy25.pdf">
                      Financial Results for the Half Year FY25
                    </a>
                    <a href="https://cdn.shopify.com/reports/annual-report-fy24.pdf">
                      Annual Report FY24
                    </a>
                    <a href="https://cdn.shopify.com/reports/press-release-fy26.pdf">
                      Press Release FY26
                    </a>
                """,
            )
        return httpx.Response(200, text="<html><body>PNGJL official</body></html>")

    with FreeSourceCollector(
        transport=httpx.MockTransport(handler), max_documents_per_company=3
    ) as collector:
        result = collector.collect(["PNGJL"])

    names = {
        Path(record.url).name
        for record in result.records
        if record.source_type == "official_document"
    }
    assert names == {
        "annual-report-fy24.pdf",
        "q1-results-fy26.pdf",
        "half-year-results-fy25.pdf",
    }


def test_report_catalogue_skips_stale_and_duplicate_filing_families() -> None:
    """Keep current diverse evidence without downloading obsolete catalogue entries."""

    requested_pdf_names: list[str] = []
    pdf = _pdf_with_text("Current official financial statements")

    def handler(request: httpx.Request) -> httpx.Response:
        name = Path(request.url.path).name
        if request.url.path.lower().endswith(".pdf"):
            requested_pdf_names.append(name)
            if "2015-16" in name:
                raise AssertionError("the stale quarterly filing must not be requested")
            return httpx.Response(200, content=pdf, headers={"content-type": "application/pdf"})
        if request.url.path.endswith("/corporate/annual-reports/"):
            return httpx.Response(
                200,
                text="""
                    <html><body>
                    <a href="/reports/annual-report-2025-26.pdf">Annual Report 2025-26</a>
                    <a href="/reports/annual-report-2024-25.pdf">Annual Report 2024-25</a>
                    </body></html>
                """,
            )
        if request.url.path.endswith("/corporate/quarterly-reports/"):
            return httpx.Response(
                200,
                text="""
                    <html><body>
                    <a href="/reports/Q4_2025-26_financial_results.pdf">
                      Q4 2025-26 Financial Results
                    </a>
                    <a href="/reports/investor-presentation-fy26.pdf">
                      Investor Presentation FY26
                    </a>
                    <a href="/reports/Q3_2015-16_Unaudited_financial_results.pdf">
                      Q3 2015-16 Unaudited Financial Results
                    </a>
                    </body></html>
                """,
            )
        ticker = request.url.params.get("symbol", "")
        return httpx.Response(
            200,
            text=f"<html><body>{ticker or 'THANGAMAYL official reports'}</body></html>",
        )

    with FreeSourceCollector(
        transport=httpx.MockTransport(handler), max_documents_per_company=4
    ) as collector:
        result = collector.collect(["THANGAMAYL"])

    assert set(requested_pdf_names) == {
        "annual-report-2025-26.pdf",
        "Q4_2025-26_financial_results.pdf",
        "investor-presentation-fy26.pdf",
    }
    assert "annual-report-2024-25.pdf" not in requested_pdf_names
    assert "Q3_2015-16_Unaudited_financial_results.pdf" not in requested_pdf_names
    assert not any("2015-16" in warning for warning in result.warnings)


def test_senco_exact_s3_document_host_is_allowlisted() -> None:
    requested_hosts: list[str] = []
    pdf = _pdf_with_text("SENCO audited financial results FY2025")
    s3_host = "sencowebfiles.s3.ap-south-1.amazonaws.com"

    def handler(request: httpx.Request) -> httpx.Response:
        requested_hosts.append(request.url.host)
        if request.url.host == s3_host:
            return httpx.Response(200, content=pdf, headers={"content-type": "application/pdf"})
        if request.url.path == "/investor-relations":
            return httpx.Response(
                200,
                text=(
                    "<html><body>SENCO investors "
                    f'<a href="https://{s3_host}/reports/Annual-Report-FY2025.pdf">'
                    "Annual Report FY2025</a></body></html>"
                ),
            )
        return httpx.Response(200, text="<html><body>SENCO official</body></html>")

    with FreeSourceCollector(
        transport=httpx.MockTransport(handler), max_documents_per_company=1
    ) as collector:
        result = collector.collect(["SENCO"])

    document = next(
        record for record in result.records if record.source_type == "official_document"
    )
    assert document.url.startswith(f"https://{s3_host}/")
    assert s3_host in requested_hosts


def test_pdf_extraction_retains_financial_and_governance_pages_beyond_the_front() -> None:
    pdf = _pdf_with_pages(
        [
            "Official annual report cover",
            "General company introduction",
            "Unimportant narrative page two",
            "Unimportant narrative page three",
            "STATEMENT OF CASH FLOWS cash from operating activities profit after tax",
            "Unimportant narrative page five",
            "CORPORATE GOVERNANCE audit committee independent directors",
        ]
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "cdn.shopify.com":
            return httpx.Response(200, content=pdf, headers={"content-type": "application/pdf"})
        if request.url.path == "/pages/investors":
            return httpx.Response(
                200,
                text="""
                    <html><body>PNGJL investors
                    <a href="https://cdn.shopify.com/report/annual-report-2025.pdf">
                      Annual Report 2025
                    </a></body></html>
                """,
            )
        return httpx.Response(200, text="<html><body>PNGJL official</body></html>")

    with FreeSourceCollector(
        transport=httpx.MockTransport(handler),
        max_documents_per_company=1,
        max_pdf_pages=3,
    ) as collector:
        result = collector.collect(["PNGJL"])

    document = next(
        record for record in result.records if record.source_type == "official_document"
    )
    assert "STATEMENT OF CASH FLOWS" in document.excerpt
    assert "CORPORATE GOVERNANCE" in document.excerpt
    assert "[PDF page 5]" in document.excerpt
    assert "[PDF page 7]" in document.excerpt


def test_400_page_pdf_retains_a_financial_statement_from_the_middle() -> None:
    """Scanning only the first pages would silently lose the most useful filing evidence."""

    pages = [f"Narrative annual-report page {number}" for number in range(1, 401)]
    pages[236] = (
        "STATEMENT OF CASH FLOWS revenue from operations profit after tax "
        "cash from operating activities"
    )
    pdf = _pdf_with_pages(pages)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "cdn.shopify.com":
            return httpx.Response(200, content=pdf, headers={"content-type": "application/pdf"})
        if request.url.path == "/pages/investors":
            return httpx.Response(
                200,
                text="""
                    <html><body>
                    <a href="https://cdn.shopify.com/reports/annual-report-2025.pdf">
                      Annual Report 2025
                    </a>
                    </body></html>
                """,
            )
        return httpx.Response(200, text="<html><body>PNGJL official</body></html>")

    with FreeSourceCollector(
        transport=httpx.MockTransport(handler),
        max_documents_per_company=1,
        max_pdf_pages=2,
    ) as collector:
        result = collector.collect(["PNGJL"])

    document = next(
        record for record in result.records if record.source_type == "official_document"
    )
    assert "[PDF page 237]" in document.excerpt
    assert "STATEMENT OF CASH FLOWS" in document.excerpt


def test_dense_ratio_page_cannot_crowd_cash_and_profit_rows_out_of_pdf_excerpt() -> None:
    """The per-page budget must expose selected pages, not only the first long page."""

    pages = [
        "Financial ratios " + " revenue from operations" * 700,
        "NET CASH FLOWS FROM OPERATING ACTIVITIES INR 209 crore",
        "PROFIT FOR THE PERIOD INR 180 crore",
        "CORPORATE GOVERNANCE audit committee independent directors",
    ]
    pdf = _pdf_with_pages(pages)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "cdn.shopify.com":
            return httpx.Response(200, content=pdf, headers={"content-type": "application/pdf"})
        if request.url.path == "/pages/investors":
            return httpx.Response(
                200,
                text=(
                    '<html><body><a href="https://cdn.shopify.com/annual-report-2026.pdf">'
                    "Annual Report 2026</a></body></html>"
                ),
            )
        return httpx.Response(200, text="<html><body>PNGJL official</body></html>")

    with FreeSourceCollector(
        transport=httpx.MockTransport(handler),
        max_documents_per_company=1,
        max_pdf_pages=4,
        max_excerpt_chars=2_000,
    ) as collector:
        result = collector.collect(["PNGJL"])

    document = next(
        record for record in result.records if record.source_type == "official_document"
    )
    assert "NET CASH FLOWS FROM OPERATING ACTIVITIES" in document.excerpt
    assert "PROFIT FOR THE PERIOD" in document.excerpt
    assert "CORPORATE GOVERNANCE" in document.excerpt


def test_main_results_table_beats_repeated_auditor_profit_mentions() -> None:
    """Keep the real PAT table when an auditor paragraph repeats 'net profit'."""

    pdf = _pdf_with_pages(
        [
            "Auditor report subsidiary net profit net profit net profit",
            "Consolidated cash flows NET CASH FLOW FROM OPERATING ACTIVITIES (1056.23)",
            (
                "Statement of Consolidated Financial Results Particulars "
                "Revenue from operations 38921.84 Tax Expense 532.25 "
                "Profit for the period net of tax 1486.54"
            ),
            "CORPORATE GOVERNANCE audit committee independent directors",
        ]
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "cdn.shopify.com":
            return httpx.Response(200, content=pdf, headers={"content-type": "application/pdf"})
        if request.url.path == "/pages/investors":
            return httpx.Response(
                200,
                text=(
                    '<a href="https://cdn.shopify.com/annual-report-2026.pdf">'
                    "Annual Report 2026</a>"
                ),
            )
        return httpx.Response(200, text="<html><body>PNGJL official</body></html>")

    with FreeSourceCollector(
        transport=httpx.MockTransport(handler),
        max_documents_per_company=1,
        max_pdf_pages=3,
    ) as collector:
        result = collector.collect(["PNGJL"])

    document = next(
        record for record in result.records if record.source_type == "official_document"
    )
    assert "NET CASH FLOW FROM OPERATING ACTIVITIES" in document.excerpt
    assert "Revenue from operations" in document.excerpt
    assert "Profit for the period" in document.excerpt
    assert "CORPORATE GOVERNANCE" in document.excerpt
    assert "Auditor report subsidiary" not in document.excerpt


def test_failures_return_blank_references_and_warnings_instead_of_raising() -> None:
    requested_hosts: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested_hosts.append(request.url.host)
        if request.url.path == "/pages/investors":
            return httpx.Response(302, headers={"location": "https://evil.example/takeover"})
        raise httpx.ConnectError("offline", request=request)

    with FreeSourceCollector(
        transport=httpx.MockTransport(handler), max_documents_per_company=2
    ) as collector:
        result = collector.collect(["PNGJL", "NOTREAL"])

    assert len(result.records) == 3
    assert all(not record.excerpt for record in result.records)
    assert len(result.warnings) == 4
    assert any("Unsupported ticker" in warning for warning in result.warnings)
    assert "evil.example" not in requested_hosts


def test_screener_csv_and_xlsx_are_local_user_exports_only(tmp_path: Path, monkeypatch) -> None:
    exports = tmp_path / "exports"
    exports.mkdir()
    (exports / "PNGJL.csv").write_text("Company,Sales\nPNG,125\n", encoding="utf-8")
    senco = exports / "SENCO"
    senco.mkdir()
    xlsx = senco / "screen.xlsx"
    _write_minimal_xlsx(xlsx)
    (exports / "unmatched.csv").write_text("Company,Sales\nIgnore,1\n", encoding="utf-8")

    # Exercise the stdlib fallback so XLSX works even though openpyxl is not a
    # project dependency.
    def no_excel_engine(*_args: object, **_kwargs: object) -> pd.DataFrame:
        raise ImportError("no optional Excel engine")

    monkeypatch.setattr(pd, "read_excel", no_excel_engine)
    with FreeSourceCollector(
        transport=httpx.MockTransport(_generic_official_handler),
        max_documents_per_company=0,
    ) as collector:
        result = collector.collect(["PNGJL", "SENCO"], screener_export_dir=exports)

    exports_found = [record for record in result.records if record.source_type == "screener_export"]
    assert {record.ticker for record in exports_found} == {"PNGJL", "SENCO"}
    assert all(record.is_private for record in exports_found)
    assert all(record.metadata["authority"] == "secondary" for record in exports_found)
    assert any("PNG,125" in record.excerpt for record in exports_found)
    assert any("Senco,456" in record.excerpt for record in exports_found)
    assert all("unmatched.csv" not in record.title for record in exports_found)


def test_ingests_ticker_scoped_private_txt_and_csv_documents(tmp_path: Path) -> None:
    documents = tmp_path / "company_documents"
    pngjl = documents / "PNGJL"
    pngjl.mkdir(parents=True)
    (pngjl / "management-note.txt").write_text("Store count increased safely.", encoding="utf-8")
    (pngjl / "metrics.csv").write_text("Metric,Value\nRevenue,900\n", encoding="utf-8")
    (documents / "loose.txt").write_text("must not be ingested", encoding="utf-8")

    with FreeSourceCollector(
        transport=httpx.MockTransport(_generic_official_handler),
        max_documents_per_company=0,
    ) as collector:
        result = collector.collect(["PNGJL"], company_documents_dir=documents)

    uploads = [record for record in result.records if record.source_type == "user_upload"]
    assert len(uploads) == 2
    assert all(record.is_private for record in uploads)
    assert any("Store count increased safely" in record.excerpt for record in uploads)
    assert any("Revenue,900" in record.excerpt for record in uploads)
    assert all("loose.txt" not in record.title for record in uploads)


def test_et_and_moneycontrol_are_disabled_by_default_and_manual_only_when_enabled() -> None:
    requested_hosts: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested_hosts.append(request.url.host)
        return _generic_official_handler(request)

    with FreeSourceCollector(
        transport=httpx.MockTransport(handler), max_documents_per_company=0
    ) as collector:
        default_result = collector.collect(["PNGJL"])
        manual_result = collector.collect(
            ["PNGJL"],
            include_et_references=True,
            include_moneycontrol_references=True,
        )

    assert not any(
        record.source_type.startswith("manual_reference") for record in default_result.records
    )
    manual = [
        record
        for record in manual_result.records
        if record.source_type.startswith("manual_reference")
    ]
    assert len(manual) == 2
    assert all(record.excerpt == "" for record in manual)
    assert all(record.metadata["retrieval_status"] == "not_fetched" for record in manual)
    assert set(requested_hosts) <= {"www.nseindia.com", "www.pngjewellers.com"}


def test_missing_local_directories_are_non_fatal(tmp_path: Path) -> None:
    missing = tmp_path / "does-not-exist"
    with FreeSourceCollector(
        transport=httpx.MockTransport(_generic_official_handler),
        max_documents_per_company=0,
    ) as collector:
        result = collector.collect(
            ["PNGJL"], screener_export_dir=missing, company_documents_dir=missing
        )

    assert len(result.records) == 3
    assert len(result.warnings) == 2
    assert result.for_ticker("pngjl") == result.records


def _write_minimal_xlsx(path: Path) -> None:
    workbook = """<?xml version="1.0" encoding="UTF-8"?>
        <workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"
          xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
          <sheets><sheet name="Data" sheetId="1" r:id="rId1"/></sheets>
        </workbook>"""
    relationships = """<?xml version="1.0" encoding="UTF-8"?>
        <Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
          <Relationship Id="rId1"
            Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet"
            Target="worksheets/sheet1.xml"/>
        </Relationships>"""
    shared_strings = """<?xml version="1.0" encoding="UTF-8"?>
        <sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
          <si><t>Company</t></si><si><t>Sales</t></si><si><t>Senco</t></si>
        </sst>"""
    worksheet = """<?xml version="1.0" encoding="UTF-8"?>
        <worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
          <sheetData>
            <row r="1"><c r="A1" t="s"><v>0</v></c><c r="B1" t="s"><v>1</v></c></row>
            <row r="2"><c r="A2" t="s"><v>2</v></c><c r="B2"><v>456</v></c></row>
          </sheetData>
        </worksheet>"""
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("xl/workbook.xml", workbook)
        archive.writestr("xl/_rels/workbook.xml.rels", relationships)
        archive.writestr("xl/sharedStrings.xml", shared_strings)
        archive.writestr("xl/worksheets/sheet1.xml", worksheet)
