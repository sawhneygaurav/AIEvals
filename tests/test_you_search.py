"""Offline contract tests for the You.com Search API adapter."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from io import BytesIO
from typing import Any, Self
from urllib.error import HTTPError, URLError
from urllib.request import Request

import pytest

from competitive_scoring.tools.you_search import (
    YOU_SEARCH_ENDPOINT,
    YouSearchClient,
    YouSearchConfigurationError,
)


class FakeResponse:
    """Minimal context-manager response matching what ``urlopen`` returns."""

    def __init__(self, document: dict[str, Any], *, status: int = 200) -> None:
        self.status = status
        self._body = json.dumps(document).encode("utf-8")

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self) -> bytes:
        return self._body


def _empty_document() -> dict[str, Any]:
    return {"results": {"web": [], "news": []}, "metadata": {}}


def test_posts_to_current_endpoint_with_api_key_and_json_payload() -> None:
    captured: dict[str, Any] = {}

    def opener(request: Request, *, timeout: float) -> FakeResponse:
        captured["request"] = request
        captured["timeout"] = timeout
        return FakeResponse(_empty_document())

    client = YouSearchClient(api_key="secret-test-key", opener=opener)
    client.search(
        "PNGJL competitors",
        count=6,
        country="IN",
        extraction_mode="highlights",
    )

    request = captured["request"]
    headers = {key.lower(): value for key, value in request.header_items()}
    assert request.full_url == YOU_SEARCH_ENDPOINT == "https://ydc-index.io/v1/search"
    assert request.get_method() == "POST"
    assert headers["x-api-key"] == "secret-test-key"
    assert headers["content-type"] == "application/json"
    assert captured["timeout"] == 20.0
    assert json.loads(request.data) == {
        "query": "PNGJL competitors",
        "count": 6,
        "offset": 0,
        "country": "IN",
        "extraction": {"extraction_mode": "highlights"},
    }


def test_normalizes_web_news_content_and_request_trace() -> None:
    document = {
        "results": {
            "web": [
                {
                    "url": "https://example.com/company",
                    "title": "Company result",
                    "description": "A useful description",
                    "snippets": ["first", "second"],
                    "contents": {"highlights": ["important passage"]},
                },
                "malformed item is ignored",
            ],
            "news": [
                {
                    "url": "https://example.com/news",
                    "title": "Recent news",
                    "page_age": "2026-09-03T09:00:00Z",
                }
            ],
        },
        "metadata": {
            "query": "normalized query",
            "search_uuid": "search-123",
            "latency": 0.42,
        },
    }
    client = YouSearchClient(
        api_key="test-key",
        opener=lambda *_args, **_kwargs: FakeResponse(document),
        clock=lambda: datetime(2026, 9, 4, 10, 30, tzinfo=UTC),
    )

    response = client.search("original query")

    assert len(response.web) == 1
    assert len(response.news) == 1
    assert response.web[0].rank == 1
    assert response.web[0].source_type == "web"
    assert response.web[0].snippets == ("first", "second")
    assert response.web[0].contents is not None
    assert response.web[0].contents.highlights == ("important passage",)
    assert response.news[0].source_type == "news"
    assert response.news[0].page_age == "2026-09-03T09:00:00Z"
    assert response.all_results == response.web + response.news
    assert response.trace.query == "normalized query"
    assert response.trace.search_uuid == "search-123"
    assert response.trace.latency_seconds == 0.42
    assert response.trace.retrieved_at == "2026-09-04T10:30:00Z"
    assert all(result.trace is response.trace for result in response.all_results)


def test_retries_rate_limit_and_network_failure_with_exponential_backoff() -> None:
    attempts = 0
    delays: list[float] = []

    def opener(request: Request, *, timeout: float) -> FakeResponse:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise HTTPError(
                request.full_url,
                429,
                "Too Many Requests",
                hdrs=None,
                fp=BytesIO(b'{"error":"rate limited"}'),
            )
        if attempts == 2:
            raise URLError("temporary DNS failure")
        return FakeResponse(_empty_document())

    client = YouSearchClient(
        api_key="test-key",
        opener=opener,
        max_retries=2,
        retry_backoff_seconds=0.1,
        sleeper=delays.append,
    )

    response = client.search("PNGJL news")

    assert response.all_results == ()
    assert attempts == 3
    assert delays == pytest.approx([0.1, 0.2])


def test_missing_api_key_raises_clear_configuration_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("YDC_API_KEY", raising=False)

    with pytest.raises(YouSearchConfigurationError, match="YDC_API_KEY"):
        YouSearchClient()
