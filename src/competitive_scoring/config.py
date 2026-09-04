"""Application configuration.

Keeping settings in one small module makes it obvious which values are secrets,
which values are fixed policy, and which values a user may safely change.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv

Mode = Literal["demo", "live"]
LLMProvider = Literal["nebius", "openai"]
ReasoningEffort = Literal["low", "medium", "high"]
NebiusAPIStyle = Literal["responses", "chat_completions"]

DEFAULT_NEBIUS_BASE_URL = "https://api.tokenfactory.nebius.com/v1/"
DEFAULT_NEBIUS_API_STYLE: NebiusAPIStyle = "responses"
DEFAULT_NEBIUS_MAX_TOKENS = 16_384
DEFAULT_NEBIUS_REASONING_EFFORT: ReasoningEffort = "low"
DEFAULT_OPENAI_MODEL = "gpt-5-mini"


def _project_root() -> Path:
    """Return the repository root regardless of the current shell directory."""

    return Path(__file__).resolve().parents[2]


def _project_path_from_env(variable: str, default: str) -> Path:
    """Resolve a configurable local path relative to the project root."""

    value = Path(os.getenv(variable, default).strip()).expanduser()
    if not value.is_absolute():
        value = _project_root() / value
    return value.resolve()


def _env_flag(variable: str, default: bool) -> bool:
    """Read a beginner-friendly yes/no environment switch."""

    raw = os.getenv(variable)
    if raw is None:
        return default
    normalized = raw.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{variable} must be true/false (or yes/no).")


def _default_book_path() -> Path:
    """Find the attached private book without copying it into the repository."""

    configured = os.getenv("BOOK_PDF_PATH", "").strip()
    if configured:
        return Path(configured).expanduser().resolve()

    # This is the location of the PDF attached in the current workspace session.
    attached = Path.home() / "Documents" / "DrVijay" / "Ebook-Peaceful-Investing.pdf"
    if attached.exists():
        return attached

    # A collaborator can instead put their own licensed copy here.  The folder
    # is ignored by Git, so neither the PDF nor its derived index is published.
    return _project_root() / "data" / "private" / "Ebook-Peaceful-Investing.pdf"


@dataclass(frozen=True, slots=True)
class Settings:
    """Runtime settings shared by the Streamlit app, CLI, and tests."""

    mode: Mode = "demo"
    target_symbol: str = "PNGJL"
    target_name: str = "P N Gadgil Jewellers Limited"
    peer_count: int = 3
    horizon: str = "2-3 years"
    research_as_of: date = date(2026, 9, 2)
    book_pdf_path: Path = Path("data/private/Ebook-Peaceful-Investing.pdf")
    book_index_dir: Path = Path("data/private/peaceful_investing_chroma")
    screener_export_dir: Path = Path("data/private/screener_exports")
    company_documents_dir: Path = Path("data/private/company_documents")
    include_et_references: bool = True
    include_moneycontrol_references: bool = True
    ydc_api_key: str | None = None
    llm_provider: LLMProvider = "nebius"
    llm_api_key: str | None = None
    llm_model: str | None = None
    llm_base_url: str | None = DEFAULT_NEBIUS_BASE_URL
    llm_api_style: NebiusAPIStyle = DEFAULT_NEBIUS_API_STYLE
    llm_max_tokens: int = DEFAULT_NEBIUS_MAX_TOKENS
    llm_reasoning_effort: ReasoningEffort = DEFAULT_NEBIUS_REASONING_EFFORT
    llm_max_concurrent_requests: int = 4
    llm_max_attempts: int = 2
    llm_request_timeout_seconds: float = 300.0
    max_audit_retries: int = 2
    max_workers: int = 4

    @property
    def openai_api_key(self) -> str | None:
        """Compatibility alias for the provider-neutral API credential.

        The graph predates provider selection and still reads this attribute.
        Keeping the alias lets it use Nebius without coupling orchestration to
        one vendor.
        """

        return self.llm_api_key

    @property
    def openai_model(self) -> str:
        """Compatibility alias for the provider-neutral model identifier."""

        return self.llm_model or ""

    @classmethod
    def from_env(cls, *, mode: Mode | None = None) -> Settings:
        """Load settings from `.env` plus the process environment.

        Explicit process variables win over values in `.env`, which is the
        conventional and least surprising behavior for local development.
        """

        load_dotenv(override=False)
        project_root = _project_root()
        index_text = os.getenv("BOOK_INDEX_DIR", "data/private/peaceful_investing_chroma")
        index_path = Path(index_text).expanduser()
        if not index_path.is_absolute():
            index_path = project_root / index_path

        chosen_mode = mode or os.getenv("APP_MODE", "demo").lower().strip()
        if chosen_mode not in {"demo", "live"}:
            raise ValueError("APP_MODE must be either 'demo' or 'live'.")

        provider_text = os.getenv("LLM_PROVIDER", "nebius").lower().strip()
        if provider_text not in {"nebius", "openai"}:
            raise ValueError("LLM_PROVIDER must be either 'nebius' or 'openai'.")

        if provider_text == "nebius":
            llm_api_key = os.getenv("NEBIUS_API_KEY", "").strip() or None
            llm_model = os.getenv("NEBIUS_MODEL", "").strip() or None
            llm_base_url = os.getenv("NEBIUS_BASE_URL", "").strip() or DEFAULT_NEBIUS_BASE_URL
            api_style_text = os.getenv("NEBIUS_API_STYLE", DEFAULT_NEBIUS_API_STYLE).lower().strip()
            if api_style_text not in {"responses", "chat_completions"}:
                raise ValueError("NEBIUS_API_STYLE must be 'responses' or 'chat_completions'.")
            llm_max_tokens = int(
                os.getenv(
                    "NEBIUS_MAX_TOKENS",
                    str(DEFAULT_NEBIUS_MAX_TOKENS),
                )
            )
            if llm_max_tokens < 1:
                raise ValueError("NEBIUS_MAX_TOKENS must be greater than zero.")
            reasoning_text = (
                os.getenv(
                    "NEBIUS_REASONING_EFFORT",
                    DEFAULT_NEBIUS_REASONING_EFFORT,
                )
                .lower()
                .strip()
            )
            if reasoning_text not in {"low", "medium", "high"}:
                raise ValueError("NEBIUS_REASONING_EFFORT must be 'low', 'medium', or 'high'.")
        else:
            llm_api_key = os.getenv("OPENAI_API_KEY", "").strip() or None
            llm_model = os.getenv("OPENAI_MODEL", "").strip() or DEFAULT_OPENAI_MODEL
            llm_base_url = os.getenv("OPENAI_BASE_URL", "").strip() or None
            # OpenAI uses its native Responses API. This field is only
            # configurable for Nebius because endpoint support varies by model.
            api_style_text = DEFAULT_NEBIUS_API_STYLE
            # These values apply only to Nebius Chat Completions. Keeping safe
            # defaults here avoids optional fields while using OpenAI.
            llm_max_tokens = DEFAULT_NEBIUS_MAX_TOKENS
            reasoning_text = DEFAULT_NEBIUS_REASONING_EFFORT

        llm_max_concurrent_requests = int(os.getenv("LLM_MAX_CONCURRENT_REQUESTS", "4"))
        if llm_max_concurrent_requests < 1:
            raise ValueError("LLM_MAX_CONCURRENT_REQUESTS must be greater than zero.")
        llm_max_attempts = int(os.getenv("LLM_MAX_ATTEMPTS", "2"))
        if llm_max_attempts < 1:
            raise ValueError("LLM_MAX_ATTEMPTS must be greater than zero.")
        llm_request_timeout_seconds = float(os.getenv("LLM_REQUEST_TIMEOUT_SECONDS", "300"))
        if llm_request_timeout_seconds <= 0:
            raise ValueError("LLM_REQUEST_TIMEOUT_SECONDS must be greater than zero.")

        # Demo output is intentionally frozen so screenshots and tests remain
        # reproducible.  Live research defaults to today and can be pinned to a
        # historical cutoff with RESEARCH_AS_OF.
        configured_as_of = os.getenv("RESEARCH_AS_OF", "").strip()
        default_as_of = date(2026, 9, 2) if chosen_mode == "demo" else datetime.now(UTC).date()

        return cls(
            mode=chosen_mode,  # type: ignore[arg-type]
            target_symbol=os.getenv("TARGET_SYMBOL", "PNGJL").upper().strip(),
            target_name=os.getenv("TARGET_NAME", "P N Gadgil Jewellers Limited").strip(),
            research_as_of=(
                date.fromisoformat(configured_as_of) if configured_as_of else default_as_of
            ),
            book_pdf_path=_default_book_path(),
            book_index_dir=index_path.resolve(),
            screener_export_dir=_project_path_from_env(
                "SCREENER_EXPORT_DIR", "data/private/screener_exports"
            ),
            company_documents_dir=_project_path_from_env(
                "COMPANY_DOCUMENTS_DIR", "data/private/company_documents"
            ),
            include_et_references=_env_flag("SHOW_ET_REFERENCE_LINKS", True),
            include_moneycontrol_references=_env_flag("SHOW_MONEYCONTROL_REFERENCE_LINKS", True),
            ydc_api_key=os.getenv("YDC_API_KEY", "").strip() or None,
            llm_provider=provider_text,  # type: ignore[arg-type]
            llm_api_key=llm_api_key,
            llm_model=llm_model,
            llm_base_url=llm_base_url,
            llm_api_style=api_style_text,  # type: ignore[arg-type]
            llm_max_tokens=llm_max_tokens,
            llm_reasoning_effort=reasoning_text,  # type: ignore[arg-type]
            llm_max_concurrent_requests=llm_max_concurrent_requests,
            llm_max_attempts=llm_max_attempts,
            llm_request_timeout_seconds=llm_request_timeout_seconds,
            max_audit_retries=int(os.getenv("MAX_AUDIT_RETRIES", "2")),
            max_workers=max(1, int(os.getenv("MAX_WORKERS", "4"))),
        )

    def validate_for_run(self) -> None:
        """Fail early with a useful message instead of failing deep in an agent."""

        if not self.book_pdf_path.exists():
            raise FileNotFoundError(
                "Peaceful Investing PDF was not found. Set BOOK_PDF_PATH in .env "
                f"(checked: {self.book_pdf_path})."
            )
        if self.mode == "live":
            missing = []
            if not self.ydc_api_key:
                missing.append("YDC_API_KEY")
            if not self.llm_api_key:
                missing.append(
                    "NEBIUS_API_KEY" if self.llm_provider == "nebius" else "OPENAI_API_KEY"
                )
            if not self.llm_model:
                missing.append("NEBIUS_MODEL" if self.llm_provider == "nebius" else "OPENAI_MODEL")
            if self.llm_provider == "nebius" and not self.llm_base_url:
                missing.append("NEBIUS_BASE_URL")
            if missing:
                raise ValueError("Live mode needs: " + ", ".join(missing))
