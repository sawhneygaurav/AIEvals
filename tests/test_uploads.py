"""Tests for private upload path and size guardrails."""

from pathlib import Path

import pytest

from competitive_scoring.uploads import save_private_upload


def test_screener_export_gets_a_ticker_filename(tmp_path: Path) -> None:
    saved = save_private_upload(
        root=tmp_path,
        ticker="pngjl",
        original_name="../../Screener Export.CSV",
        content=b"metric,value\nROE,18.2\n",
        kind="screener",
    )

    assert saved == tmp_path / "PNGJL.csv"
    assert saved.read_bytes().startswith(b"metric")


def test_company_document_stays_inside_ticker_folder(tmp_path: Path) -> None:
    saved = save_private_upload(
        root=tmp_path,
        ticker="SENCO",
        original_name="../Q1 results (final).pdf",
        content=b"%PDF-placeholder",
        kind="company_document",
    )

    assert saved == tmp_path / "SENCO" / "Q1-results-final.pdf"


def test_upload_rejects_unknown_extension(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="must use one of"):
        save_private_upload(
            root=tmp_path,
            ticker="PNGJL",
            original_name="payload.exe",
            content=b"not allowed",
            kind="company_document",
        )
