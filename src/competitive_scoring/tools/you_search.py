"""Typed adapter for the You.com Web Search API.

The research agents use the normalized dataclasses in this module rather than
depending on You.com's loosely typed JSON directly.  Keeping authentication,
HTTP retries, validation, and response parsing here also makes the rest of the
application easier for a beginner to follow and straightforward to test
without making real network calls.
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from ..tracing import event, span

# You.com documents POST as the current endpoint.  GET remains backwards
# compatible, but newer features such as ``extraction`` are POST-only.
YOU_SEARCH_ENDPOINT = "https://ydc-index.io/v1/search"
YOU_API_KEY_ENV_VAR = "YDC_API_KEY"


class YouSearchError(RuntimeError):
    """Raised when a You.com request cannot produce a valid response."""


class YouSearchConfigurationError(YouSearchError):
    """Raised for missing credentials or invalid request options."""


@dataclass(frozen=True)
class SourceTrace:
    """Request-level provenance shared by every normalized result."""

    provider: str
    endpoint: str
    query: str
    search_uuid: str | None
    latency_seconds: float | None
    retrieved_at: str


@dataclass(frozen=True)
class SearchContents:
    """Optional extracted content attached to a web or news result."""

    highlights: tuple[str, ...] = ()
    markdown: str | None = None
    html: str | None = None


@dataclass(frozen=True)
class YouSearchResult:
    """One normalized web page or news article."""

    source_type: str
    rank: int
    url: str
    title: str
    description: str | None
    snippets: tuple[str, ...]
    page_age: str | None
    thumbnail_url: str | None
    favicon_url: str | None
    contents: SearchContents | None
    trace: SourceTrace


@dataclass(frozen=True)
class YouSearchResponse:
    """Normalized result sections plus request-level provenance."""

    web: tuple[YouSearchResult, ...]
    news: tuple[YouSearchResult, ...]
    trace: SourceTrace

    @property
    def all_results(self) -> tuple[YouSearchResult, ...]:
        """Return web and news items in one tuple when separation is unneeded."""

        return self.web + self.news


# Injectable boundaries keep the adapter's tests fully offline and instant.
Opener = Callable[..., Any]
Clock = Callable[[], datetime]
Sleeper = Callable[[float], None]


class YouSearchClient:
    """Call and normalize the You.com Web Search API.

    Args:
        api_key: Explicit key. When omitted, ``YDC_API_KEY`` is read from the
            environment.
        timeout_seconds: Timeout for each HTTP attempt.
        max_retries: Extra attempts after a transient HTTP/network failure.
        retry_backoff_seconds: Base delay for exponential retry backoff.
        opener: Injectable ``urlopen``-compatible callable for offline tests.
        clock: Injectable clock for deterministic provenance timestamps.
        sleeper: Injectable sleep function so retry tests do not actually wait.
    """

    def __init__(
        self,
        api_key: str | None = None,
        *,
        timeout_seconds: float = 20.0,
        max_retries: int = 2,
        retry_backoff_seconds: float = 0.25,
        opener: Opener | None = None,
        clock: Clock | None = None,
        sleeper: Sleeper | None = None,
    ) -> None:
        resolved_key = api_key if api_key is not None else os.getenv(YOU_API_KEY_ENV_VAR)
        if not isinstance(resolved_key, str) or not resolved_key.strip():
            raise YouSearchConfigurationError(
                f"A You.com API key is required. Pass api_key or set {YOU_API_KEY_ENV_VAR}."
            )
        if isinstance(timeout_seconds, bool) or timeout_seconds <= 0:
            raise YouSearchConfigurationError("timeout_seconds must be greater than zero.")
        if isinstance(max_retries, bool) or not isinstance(max_retries, int) or max_retries < 0:
            raise YouSearchConfigurationError("max_retries must be a non-negative integer.")
        if (
            isinstance(retry_backoff_seconds, bool)
            or not isinstance(retry_backoff_seconds, (int, float))
            or retry_backoff_seconds < 0
        ):
            raise YouSearchConfigurationError(
                "retry_backoff_seconds must be a non-negative number."
            )

        # The key remains private and is never included in errors or traces.
        self._api_key = resolved_key.strip()
        self._timeout_seconds = float(timeout_seconds)
        self._max_retries = max_retries
        self._retry_backoff_seconds = float(retry_backoff_seconds)
        self._opener = opener or urlopen
        self._clock = clock or (lambda: datetime.now(UTC))
        self._sleeper = sleeper or time.sleep

    def search(
        self,
        query: str,
        *,
        count: int = 10,
        freshness: str | None = None,
        offset: int = 0,
        country: str | None = None,
        language: str | None = None,
        safesearch: str | None = None,
        knowledge: str | None = None,
        include_domains: Sequence[str] | str | None = None,
        exclude_domains: Sequence[str] | str | None = None,
        boost_domains: Sequence[str] | str | None = None,
        extraction_mode: str | None = None,
        extraction_formats: Sequence[str] | str | None = None,
        crawl_timeout: int | None = None,
    ) -> YouSearchResponse:
        """Search web and news, returning one predictable response object."""

        clean_query = _require_non_empty_string(query, "query")
        _require_int_range(count, "count", minimum=1, maximum=100)
        _require_int_range(offset, "offset", minimum=0, maximum=9)

        included = _normalize_string_list(include_domains, "include_domains")
        excluded = _normalize_string_list(exclude_domains, "exclude_domains")
        boosted = _normalize_string_list(boost_domains, "boost_domains")
        for name, domains in (
            ("include_domains", included),
            ("exclude_domains", excluded),
            ("boost_domains", boosted),
        ):
            if len(domains) > 500:
                raise YouSearchConfigurationError(f"{name} accepts at most 500 domains.")
        if included and excluded:
            raise YouSearchConfigurationError(
                "include_domains cannot be combined with exclude_domains."
            )
        if included and boosted:
            raise YouSearchConfigurationError(
                "include_domains cannot be combined with boost_domains."
            )
        if safesearch is not None and safesearch not in {"off", "moderate", "strict"}:
            raise YouSearchConfigurationError("safesearch must be 'off', 'moderate', or 'strict'.")
        if crawl_timeout is not None:
            _require_int_range(crawl_timeout, "crawl_timeout", minimum=1, maximum=60)

        payload: dict[str, Any] = {"query": clean_query, "count": count, "offset": offset}
        for key, value in (
            ("freshness", freshness),
            ("country", country),
            ("language", language),
            ("safesearch", safesearch),
            ("knowledge", knowledge),
        ):
            _add_optional_text(payload, key, value)
        if included:
            payload["include_domains"] = list(included)
        if excluded:
            payload["exclude_domains"] = list(excluded)
        if boosted:
            payload["boost_domains"] = list(boosted)
        extraction = _build_extraction(extraction_mode, extraction_formats)
        if extraction is not None:
            payload["extraction"] = extraction
        if crawl_timeout is not None:
            payload["crawl_timeout"] = crawl_timeout

        document = self._post_json(payload)
        return _parse_response(document, requested_query=clean_query, clock=self._clock)

    def _post_json(self, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        """POST JSON, retry transient failures, and return a top-level object."""

        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        for attempt in range(self._max_retries + 1):
            request = Request(
                YOU_SEARCH_ENDPOINT,
                data=body,
                headers={
                    "X-API-Key": self._api_key,
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                },
                method="POST",
            )
            try:
                with (
                    span("search.http", attempt=attempt + 1,
                         max_attempts=self._max_retries + 1,
                         timeout_seconds=self._timeout_seconds) as details,
                    self._opener(request, timeout=self._timeout_seconds) as response,
                ):
                    status = getattr(response, "status", 200)
                    details["status_code"] = status
                    raw_body = response.read()
                    details["bytes"] = len(raw_body)
            except HTTPError as exc:
                if _is_retryable_status(exc.code) and attempt < self._max_retries:
                    self._wait_before_retry(attempt)
                    continue
                detail = _safe_error_body(exc)
                suffix = f": {detail}" if detail else ""
                raise YouSearchError(f"You.com search returned HTTP {exc.code}{suffix}") from exc
            except (URLError, OSError) as exc:
                if attempt < self._max_retries:
                    self._wait_before_retry(attempt)
                    continue
                reason = getattr(exc, "reason", exc)
                raise YouSearchError(f"Could not reach You.com search: {reason}") from exc

            if not isinstance(status, int):
                raise YouSearchError("You.com search returned an invalid HTTP status.")
            if not 200 <= status < 300:
                if _is_retryable_status(status) and attempt < self._max_retries:
                    self._wait_before_retry(attempt)
                    continue
                raise YouSearchError(f"You.com search returned HTTP {status}.")
            break
        else:  # pragma: no cover - the loop always returns, breaks, or raises
            raise YouSearchError("You.com search exhausted all retry attempts.")

        if isinstance(raw_body, bytes):
            text = raw_body.decode("utf-8", errors="replace")
        elif isinstance(raw_body, str):
            text = raw_body
        else:
            raise YouSearchError("You.com search returned an unreadable response body.")
        try:
            decoded = json.loads(text)
        except json.JSONDecodeError as exc:
            raise YouSearchError("You.com search returned invalid JSON.") from exc
        if not isinstance(decoded, Mapping):
            raise YouSearchError("You.com search returned JSON that was not an object.")
        return decoded

    def _wait_before_retry(self, attempt: int) -> None:
        """Apply bounded exponential backoff between transient failures."""

        delay = self._retry_backoff_seconds * (2**attempt)
        event("search.retry", attempt=attempt + 2, retry_delay_seconds=delay)
        if delay:
            with span("search.backoff", retry_delay_seconds=delay):
                self._sleeper(delay)


def _parse_response(
    document: Mapping[str, Any], *, requested_query: str, clock: Clock
) -> YouSearchResponse:
    metadata = document.get("metadata")
    if not isinstance(metadata, Mapping):
        metadata = {}
    trace = SourceTrace(
        provider="you.com",
        endpoint=YOU_SEARCH_ENDPOINT,
        query=_optional_text(metadata.get("query")) or requested_query,
        search_uuid=_optional_text(metadata.get("search_uuid")),
        latency_seconds=_optional_number(metadata.get("latency")),
        retrieved_at=_utc_timestamp(clock()),
    )
    results = document.get("results")
    if not isinstance(results, Mapping):
        results = {}
    return YouSearchResponse(
        web=_parse_section(results.get("web"), source_type="web", trace=trace),
        news=_parse_section(results.get("news"), source_type="news", trace=trace),
        trace=trace,
    )


def _parse_section(
    value: Any, *, source_type: str, trace: SourceTrace
) -> tuple[YouSearchResult, ...]:
    if not isinstance(value, list):
        return ()
    parsed: list[YouSearchResult] = []
    for item in value:
        if not isinstance(item, Mapping):
            continue
        parsed.append(
            YouSearchResult(
                source_type=source_type,
                rank=len(parsed) + 1,
                url=_optional_text(item.get("url")) or "",
                title=_optional_text(item.get("title")) or "",
                description=_optional_text(item.get("description")),
                snippets=_string_tuple(item.get("snippets")),
                page_age=_optional_text(item.get("page_age")),
                thumbnail_url=_optional_text(item.get("thumbnail_url")),
                favicon_url=_optional_text(item.get("favicon_url")),
                contents=_parse_contents(item.get("contents")),
                trace=trace,
            )
        )
    return tuple(parsed)


def _parse_contents(value: Any) -> SearchContents | None:
    if not isinstance(value, Mapping):
        return None
    result = SearchContents(
        highlights=_string_tuple(value.get("highlights")),
        markdown=_optional_text(value.get("markdown")),
        html=_optional_text(value.get("html")),
    )
    return (
        result
        if result.highlights or result.markdown is not None or result.html is not None
        else None
    )


def _build_extraction(
    mode: str | None, formats: Sequence[str] | str | None
) -> dict[str, Any] | None:
    normalized = _normalize_string_list(formats, "extraction_formats")
    if mode is None:
        if normalized:
            raise YouSearchConfigurationError(
                "extraction_formats requires extraction_mode='full_page'."
            )
        return None
    clean_mode = _require_non_empty_string(mode, "extraction_mode")
    if clean_mode not in {"highlights", "full_page"}:
        raise YouSearchConfigurationError("extraction_mode must be 'highlights' or 'full_page'.")
    if clean_mode != "full_page" and normalized:
        raise YouSearchConfigurationError(
            "extraction_formats can only be used with extraction_mode='full_page'."
        )
    if any(item not in {"markdown", "html"} for item in normalized):
        raise YouSearchConfigurationError(
            "extraction_formats may contain only 'markdown' and 'html'."
        )
    result: dict[str, Any] = {"extraction_mode": clean_mode}
    if clean_mode == "full_page" and normalized:
        result["full_page"] = {"extraction_formats": list(normalized)}
    return result


def _normalize_string_list(value: Sequence[str] | str | None, field_name: str) -> tuple[str, ...]:
    if value is None:
        return ()
    candidates: Any = (value,) if isinstance(value, str) else value
    try:
        iterator = iter(candidates)
    except TypeError as exc:
        raise YouSearchConfigurationError(
            f"{field_name} must be a string or a sequence of strings."
        ) from exc
    normalized: list[str] = []
    for candidate in iterator:
        clean = _require_non_empty_string(candidate, field_name)
        if clean not in normalized:
            normalized.append(clean)
    return tuple(normalized)


def _require_non_empty_string(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise YouSearchConfigurationError(f"{field_name} must be a non-empty string.")
    return value.strip()


def _require_int_range(value: Any, field_name: str, *, minimum: int, maximum: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise YouSearchConfigurationError(
            f"{field_name} must be an integer from {minimum} through {maximum}."
        )


def _add_optional_text(payload: dict[str, Any], key: str, value: str | None) -> None:
    if value is not None:
        payload[key] = _require_non_empty_string(value, key)


def _optional_text(value: Any) -> str | None:
    return value if isinstance(value, str) else None


def _optional_number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _string_tuple(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        return (value,)
    if not isinstance(value, list):
        return ()
    return tuple(item for item in value if isinstance(item, str))


def _utc_timestamp(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _is_retryable_status(status: int) -> bool:
    return status in {408, 425, 429} or 500 <= status <= 599


def _safe_error_body(error: HTTPError) -> str:
    try:
        body = error.read(2_000)
    except (OSError, AttributeError):
        return ""
    if isinstance(body, bytes):
        return body.decode("utf-8", errors="replace").strip()
    return body.strip() if isinstance(body, str) else ""


__all__ = [
    "YOU_API_KEY_ENV_VAR",
    "YOU_SEARCH_ENDPOINT",
    "SearchContents",
    "SourceTrace",
    "YouSearchClient",
    "YouSearchConfigurationError",
    "YouSearchError",
    "YouSearchResponse",
    "YouSearchResult",
]
