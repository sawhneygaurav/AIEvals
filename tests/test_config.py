"""Focused tests for environment-backed application settings."""

from __future__ import annotations

from pathlib import Path

import pytest

import competitive_scoring.config as config_module
from competitive_scoring.config import (
    DEFAULT_NEBIUS_API_STYLE,
    DEFAULT_NEBIUS_BASE_URL,
    DEFAULT_NEBIUS_MAX_TOKENS,
    DEFAULT_NEBIUS_REASONING_EFFORT,
    DEFAULT_OPENAI_MODEL,
    Settings,
)


@pytest.fixture(autouse=True)
def isolated_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep a developer's real shell or `.env` out of these unit tests."""

    for name in (
        "APP_MODE",
        "BOOK_INDEX_DIR",
        "BOOK_PDF_PATH",
        "LLM_PROVIDER",
        "NEBIUS_API_KEY",
        "NEBIUS_MODEL",
        "NEBIUS_BASE_URL",
        "NEBIUS_API_STYLE",
        "NEBIUS_MAX_TOKENS",
        "NEBIUS_REASONING_EFFORT",
        "LLM_MAX_CONCURRENT_REQUESTS",
        "LLM_MAX_ATTEMPTS",
        "LLM_REQUEST_TIMEOUT_SECONDS",
        "OPENAI_API_KEY",
        "OPENAI_MODEL",
        "OPENAI_BASE_URL",
        "RESEARCH_AS_OF",
        "SCREENER_EXPORT_DIR",
        "COMPANY_DOCUMENTS_DIR",
        "SHOW_ET_REFERENCE_LINKS",
        "SHOW_MONEYCONTROL_REFERENCE_LINKS",
        "YDC_API_KEY",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(config_module, "load_dotenv", lambda **_kwargs: None)


def test_nebius_is_the_default_llm_provider() -> None:
    settings = Settings.from_env(mode="live")

    assert settings.llm_provider == "nebius"
    assert settings.llm_api_key is None
    assert settings.llm_model is None
    assert settings.llm_base_url == DEFAULT_NEBIUS_BASE_URL
    assert settings.llm_api_style == DEFAULT_NEBIUS_API_STYLE
    assert settings.llm_max_tokens == DEFAULT_NEBIUS_MAX_TOKENS
    assert settings.llm_reasoning_effort == DEFAULT_NEBIUS_REASONING_EFFORT
    assert settings.llm_max_concurrent_requests == 4
    assert settings.llm_max_attempts == 2
    assert settings.llm_request_timeout_seconds == 300


def test_nebius_environment_selects_credentials_model_and_base_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NEBIUS_API_KEY", "  token-factory-key  ")
    monkeypatch.setenv("NEBIUS_MODEL", "  vendor/model-id  ")
    monkeypatch.setenv("NEBIUS_BASE_URL", "  https://nebius.example/v1/  ")
    monkeypatch.setenv("NEBIUS_API_STYLE", " RESPONSES ")
    monkeypatch.setenv("NEBIUS_MAX_TOKENS", "12000")
    monkeypatch.setenv("NEBIUS_REASONING_EFFORT", " HIGH ")
    monkeypatch.setenv("LLM_MAX_CONCURRENT_REQUESTS", "3")
    monkeypatch.setenv("LLM_MAX_ATTEMPTS", "4")
    monkeypatch.setenv("LLM_REQUEST_TIMEOUT_SECONDS", "450.5")

    settings = Settings.from_env(mode="live")

    assert settings.llm_api_key == "token-factory-key"
    assert settings.llm_model == "vendor/model-id"
    assert settings.llm_base_url == "https://nebius.example/v1/"
    assert settings.llm_api_style == "responses"
    assert settings.llm_max_tokens == 12_000
    assert settings.llm_reasoning_effort == "high"
    assert settings.llm_max_concurrent_requests == 3
    assert settings.llm_max_attempts == 4
    assert settings.llm_request_timeout_seconds == 450.5
    # The unchanged graph reads these compatibility aliases.
    assert settings.openai_api_key == settings.llm_api_key
    assert settings.openai_model == settings.llm_model


def test_openai_remains_an_explicit_provider_option(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LLM_PROVIDER", " OPENAI ")
    monkeypatch.setenv("OPENAI_API_KEY", "openai-key")

    settings = Settings.from_env(mode="live")

    assert settings.llm_provider == "openai"
    assert settings.llm_api_key == "openai-key"
    assert settings.llm_model == DEFAULT_OPENAI_MODEL
    assert settings.llm_base_url is None


def test_invalid_llm_provider_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "not-a-provider")

    with pytest.raises(ValueError, match="LLM_PROVIDER"):
        Settings.from_env()


@pytest.mark.parametrize(
    ("name", "value", "message"),
    [
        ("NEBIUS_MAX_TOKENS", "0", "NEBIUS_MAX_TOKENS"),
        ("NEBIUS_REASONING_EFFORT", "max", "NEBIUS_REASONING_EFFORT"),
        ("NEBIUS_API_STYLE", "auto", "NEBIUS_API_STYLE"),
        ("LLM_MAX_CONCURRENT_REQUESTS", "0", "LLM_MAX_CONCURRENT_REQUESTS"),
        ("LLM_MAX_ATTEMPTS", "0", "LLM_MAX_ATTEMPTS"),
        ("LLM_REQUEST_TIMEOUT_SECONDS", "0", "LLM_REQUEST_TIMEOUT_SECONDS"),
    ],
)
def test_invalid_nebius_generation_settings_are_rejected(
    monkeypatch: pytest.MonkeyPatch,
    name: str,
    value: str,
    message: str,
) -> None:
    monkeypatch.setenv(name, value)

    with pytest.raises(ValueError, match=message):
        Settings.from_env()


def test_live_nebius_validation_names_the_missing_provider_values(
    tmp_path: Path,
) -> None:
    book = tmp_path / "book.pdf"
    book.touch()
    settings = Settings(
        mode="live",
        book_pdf_path=book,
        ydc_api_key="you-key",
        llm_provider="nebius",
        llm_api_key=None,
        llm_model=None,
    )

    with pytest.raises(ValueError, match="NEBIUS_API_KEY, NEBIUS_MODEL"):
        settings.validate_for_run()


def test_live_openai_validation_names_its_credential(tmp_path: Path) -> None:
    book = tmp_path / "book.pdf"
    book.touch()
    settings = Settings(
        mode="live",
        book_pdf_path=book,
        ydc_api_key="you-key",
        llm_provider="openai",
        llm_api_key=None,
        llm_model=DEFAULT_OPENAI_MODEL,
        llm_base_url=None,
    )

    with pytest.raises(ValueError, match="OPENAI_API_KEY"):
        settings.validate_for_run()


def test_live_mode_requires_the_primary_you_search_key(tmp_path: Path) -> None:
    book = tmp_path / "book.pdf"
    book.touch()
    settings = Settings(
        mode="live",
        book_pdf_path=book,
        llm_provider="nebius",
        llm_api_key="nebius-key",
        llm_model="provider/model-id",
    )

    with pytest.raises(ValueError, match="YDC_API_KEY"):
        settings.validate_for_run()


def test_live_mode_accepts_you_and_nebius_credentials(tmp_path: Path) -> None:
    book = tmp_path / "book.pdf"
    book.touch()
    settings = Settings(
        mode="live",
        book_pdf_path=book,
        ydc_api_key="you-key",
        llm_provider="nebius",
        llm_api_key="nebius-key",
        llm_model="provider/model-id",
    )

    settings.validate_for_run()


def test_private_input_directories_resolve_under_project(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings.from_env(mode="demo")

    assert settings.screener_export_dir.is_absolute()
    assert settings.screener_export_dir.name == "screener_exports"
    assert settings.company_documents_dir.name == "company_documents"
    assert settings.include_et_references is True
    assert settings.include_moneycontrol_references is True
