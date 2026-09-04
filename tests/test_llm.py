"""Offline tests for the OpenAI and Nebius structured-output boundaries.

The fake client below has the same tiny surface that ``StructuredLLM`` uses, so
these tests never make a network request and never require a real API key.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from types import SimpleNamespace
from typing import Self

import pytest
from openai import OpenAIError
from pydantic import BaseModel

from competitive_scoring.config import DEFAULT_NEBIUS_BASE_URL
from competitive_scoring.llm import (
    REPAIR_INSTRUCTION,
    StructuredLLM,
    StructuredOutputError,
    _schema_instruction,
)
from competitive_scoring.models import AnalysisBlock, Metric


class ExampleAnswer(BaseModel):
    """Small schema that makes success and validation failure easy to see."""

    company: str
    score: int


@dataclass
class FakeResponse:
    """Minimal substitute for the SDK's response object."""

    status: str = "completed"
    output_text: str | None = '{"company": "PNGJL", "score": 82}'
    output_parsed: BaseModel | dict[str, object] | None = None
    incomplete_details: object | None = None


class FakeResponsesAPI:
    """Mimic the SDK's strict ``responses.parse`` boundary without a network."""

    def __init__(self, *outcomes: FakeResponse | Exception) -> None:
        self.outcomes = list(outcomes)
        self.calls: list[dict[str, object]] = []

    def parse(self, **kwargs: object) -> FakeResponse:
        self.calls.append(kwargs)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        if outcome.status != "completed":
            return outcome
        if not isinstance(outcome.output_text, str) or not outcome.output_text.strip():
            return outcome

        # The real SDK uses the class passed as ``text_format`` to parse and
        # validate the provider's JSON. Doing the same here makes malformed and
        # schema-invalid output exercise our retry path realistically.
        schema = kwargs["text_format"]
        assert isinstance(schema, type) and issubclass(schema, BaseModel)
        outcome.output_parsed = schema.model_validate_json(outcome.output_text)
        return outcome


def make_llm(*outcomes: FakeResponse | Exception, max_attempts: int = 2) -> tuple:
    """Build the wrapper around a fake ``client.responses.parse`` method."""

    responses_api = FakeResponsesAPI(*outcomes)
    fake_client = SimpleNamespace(responses=responses_api)
    llm = StructuredLLM(
        api_key="test-key",
        model="test-model",
        provider="openai",
        client=fake_client,
        max_attempts=max_attempts,
    )
    return llm, responses_api


def make_nebius_responses_llm(
    *outcomes: FakeResponse | Exception,
    max_attempts: int = 2,
) -> tuple[StructuredLLM, FakeResponsesAPI]:
    """Build the tested Qwen + Nebius Responses configuration."""

    responses_api = FakeResponsesAPI(*outcomes)
    fake_client = SimpleNamespace(responses=responses_api)
    llm = StructuredLLM(
        api_key="test-key",
        model="Qwen/Qwen3-30B-A3B-Instruct-2507",
        provider="nebius",
        client=fake_client,
        max_attempts=max_attempts,
    )
    return llm, responses_api


@dataclass
class FakeChatMessage:
    """Minimal Chat Completions message returned by the SDK."""

    content: str | None = '{"company": "PNGJL", "score": 82}'
    refusal: str | None = None
    parsed: BaseModel | dict[str, object] | None = None


@dataclass
class FakeChatChoice:
    """Minimal Chat Completions choice returned by the SDK."""

    message: FakeChatMessage
    finish_reason: str | None = "stop"


@dataclass
class FakeChatResponse:
    """Minimal top-level Chat Completions response."""

    choices: list[FakeChatChoice]


class FakeChatStream:
    """Context manager returned by the SDK's Chat Completions stream helper."""

    def __init__(
        self,
        outcome: FakeChatResponse | Exception,
        schema: type[BaseModel],
    ) -> None:
        self.outcome = outcome
        self.schema = schema
        self.entered = False
        self.exited = False

    def __enter__(self) -> Self:
        self.entered = True
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self

    def __exit__(self, *args: object) -> None:
        self.exited = True

    def get_final_completion(self) -> FakeChatResponse:
        assert self.entered
        assert not isinstance(self.outcome, Exception)
        if self.outcome.choices:
            choice = self.outcome.choices[0]
            message = choice.message
            if (
                choice.finish_reason in {None, "stop"}
                and not message.refusal
                and isinstance(message.content, str)
                and message.content.strip()
            ):
                message.parsed = self.schema.model_validate_json(message.content)
        return self.outcome


class FakeChatCompletionsAPI:
    """Mimic the SDK's strict ``chat.completions.stream`` method."""

    def __init__(self, *outcomes: FakeChatResponse | Exception) -> None:
        self.outcomes = list(outcomes)
        self.calls: list[dict[str, object]] = []
        self.streams: list[FakeChatStream] = []

    def stream(self, **kwargs: object) -> FakeChatStream:
        self.calls.append(kwargs)
        outcome = self.outcomes.pop(0)
        schema = kwargs["response_format"]
        assert isinstance(schema, type) and issubclass(schema, BaseModel)
        stream = FakeChatStream(outcome, schema)
        self.streams.append(stream)
        return stream


def chat_response(
    content: str | None = '{"company": "PNGJL", "score": 82}',
    *,
    finish_reason: str | None = "stop",
    refusal: str | None = None,
) -> FakeChatResponse:
    """Build one compact fake Chat Completions response."""

    return FakeChatResponse(
        choices=[
            FakeChatChoice(
                message=FakeChatMessage(content=content, refusal=refusal),
                finish_reason=finish_reason,
            )
        ]
    )


def make_nebius_llm(
    *outcomes: FakeChatResponse | Exception,
    max_attempts: int = 2,
) -> tuple[StructuredLLM, FakeChatCompletionsAPI]:
    """Build the wrapper around a fake Nebius Chat Completions method."""

    completions_api = FakeChatCompletionsAPI(*outcomes)
    fake_client = SimpleNamespace(
        chat=SimpleNamespace(completions=completions_api),
        # If production routing accidentally uses Responses, the missing
        # ``parse`` method makes the test fail immediately.
        responses=SimpleNamespace(),
    )
    llm = StructuredLLM(
        api_key="test-key",
        model="moonshotai/Kimi-K3",
        provider="nebius",
        nebius_api_style="chat_completions",
        client=fake_client,
        max_attempts=max_attempts,
    )
    return llm, completions_api


def test_nebius_uses_chat_completions_with_json_schema() -> None:
    llm, api = make_nebius_llm(chat_response())

    answer = llm.generate(
        schema=ExampleAnswer,
        instructions="Return an investment score.",
        prompt="Score PNGJL.",
    )

    assert answer == ExampleAnswer(company="PNGJL", score=82)
    assert len(api.calls) == 1
    request = api.calls[0]
    assert request["model"] == "moonshotai/Kimi-K3"
    assert request["store"] is False
    assert request["max_tokens"] == 16_384
    assert request["reasoning_effort"] == "low"
    assert request["temperature"] == 1.0
    assert request["top_p"] == 0.95
    messages = request["messages"]
    assert messages[0]["role"] == "system"
    assert str(messages[0]["content"]).startswith("Return an investment score.")
    assert "The response must match this JSON schema exactly" in str(messages[0]["content"])
    assert messages[1] == {"role": "user", "content": "Score PNGJL."}
    # Passing the Pydantic class asks the SDK to generate and enforce its strict
    # JSON schema instead of trusting a raw model string.
    assert request["response_format"] is ExampleAnswer
    assert api.streams[0].entered is True
    assert api.streams[0].exited is True


def test_nebius_responses_is_the_default_and_uses_the_pydantic_parser() -> None:
    llm, api = make_nebius_responses_llm(FakeResponse())

    answer = llm.generate(
        schema=ExampleAnswer,
        instructions="Return an investment score.",
        prompt="Score PNGJL.",
    )

    assert answer == ExampleAnswer(company="PNGJL", score=82)
    request = api.calls[0]
    assert request["model"] == "Qwen/Qwen3-30B-A3B-Instruct-2507"
    assert request["store"] is False
    assert request["max_output_tokens"] == 16_384
    assert request["text_format"] is ExampleAnswer
    assert "The response must match this JSON schema exactly" in str(request["instructions"])


def test_prompt_schema_matches_the_sdk_strict_required_fields() -> None:
    schema_text = _schema_instruction(AnalysisBlock).split("Schema: ", 1)[1]
    wire_schema = json.loads(schema_text)

    assert set(wire_schema["required"]) == set(wire_schema["properties"])
    metric_schema = wire_schema["$defs"]["Metric"]
    assert set(metric_schema["required"]) == set(metric_schema["properties"])


@pytest.mark.parametrize(
    "bad_response",
    [
        FakeChatResponse(choices=[]),
        chat_response(content="   "),
        chat_response(content=None, refusal="cannot comply"),
        chat_response(finish_reason="length"),
        chat_response(content="not JSON"),
        chat_response(content='{"company": "PNGJL", "score": "not-a-number"}'),
    ],
    ids=["no-choices", "empty", "refusal", "length", "malformed-json", "schema-invalid"],
)
def test_nebius_invalid_output_is_retried(
    bad_response: FakeChatResponse,
) -> None:
    llm, api = make_nebius_llm(bad_response, chat_response())

    answer = llm.generate(
        schema=ExampleAnswer,
        instructions="Return an investment score.",
        prompt="Score PNGJL.",
    )

    assert answer.score == 82
    assert len(api.calls) == 2
    first_system_message = api.calls[0]["messages"][0]["content"]
    retry_system_message = api.calls[1]["messages"][0]["content"]
    assert REPAIR_INSTRUCTION.strip() not in str(first_system_message)
    assert REPAIR_INSTRUCTION.strip() in str(retry_system_message)
    assert all(call["store"] is False for call in api.calls)
    # The context manager closes even when parsing/validation of the completed
    # first stream fails, so retries cannot leak HTTP connections.
    assert all(stream.exited for stream in api.streams)


def test_nebius_api_exception_is_retried() -> None:
    llm, api = make_nebius_llm(OpenAIError("temporary API problem"), chat_response())

    answer = llm.generate(
        schema=ExampleAnswer,
        instructions="Return an investment score.",
        prompt="Score PNGJL.",
    )

    assert answer.company == "PNGJL"
    assert len(api.calls) == 2


def test_nebius_retry_receives_the_exact_json_error_location() -> None:
    llm, api = make_nebius_llm(
        chat_response(content='{"company": "PNGJL" "score": 82}'),
        chat_response(),
    )

    answer = llm.generate(
        schema=ExampleAnswer,
        instructions="Return an investment score.",
        prompt="Score PNGJL.",
    )

    assert answer.score == 82
    retry_instructions = str(api.calls[1]["messages"][0]["content"])
    assert "Invalid JSON" in retry_instructions
    assert "line 1" in retry_instructions
    assert "column" in retry_instructions


def test_final_error_names_the_company_and_agent_without_raw_output() -> None:
    malformed = '{"company": "PNGJL" "score": 82}'
    llm, _api = make_nebius_llm(
        chat_response(content=malformed),
        chat_response(content=malformed),
    )

    with pytest.raises(StructuredOutputError) as caught:
        llm.generate(
            schema=ExampleAnswer,
            instructions="Return an investment score.",
            prompt="Score PNGJL.",
            context="PNGJL — Fundamentals Agent",
        )

    error = caught.value
    assert error.context == "PNGJL — Fundamentals Agent"
    assert error.provider == "nebius"
    assert error.model == "moonshotai/Kimi-K3"
    assert "PNGJL — Fundamentals Agent" in str(error)
    assert malformed not in str(error)


def test_semantic_metric_failure_is_retried_before_the_final_audit() -> None:
    missing_date = AnalysisBlock(
        agent="Fundamentals Agent",
        status="complete",
        score=60,
        summary="First attempt",
        metrics=[
            Metric(
                code="roe",
                label="ROE",
                value=18.2,
                unit="%",
                period="FY 2026",
                period_type="FY",
                accounting_basis="consolidated",
                source_ids=["source-1"],
            )
        ],
    )
    corrected = missing_date.model_copy(
        update={
            "summary": "Corrected attempt",
            "metrics": [
                missing_date.metrics[0].model_copy(update={"as_of_date": date(2026, 3, 31)})
            ],
        }
    )
    llm, api = make_nebius_responses_llm(
        FakeResponse(output_text=missing_date.model_dump_json()),
        FakeResponse(output_text=corrected.model_dump_json()),
    )

    answer = llm.generate(
        schema=AnalysisBlock,
        instructions="Return a fundamentals analysis.",
        prompt="Use source-1.",
    )

    assert answer.summary == "Corrected attempt"
    assert len(api.calls) == 2
    assert "metrics.0 (roe) needs as_of_date" in str(api.calls[1]["instructions"])


def test_nebius_stream_preserves_semantic_validation_and_precise_retry() -> None:
    missing_date = AnalysisBlock(
        agent="Fundamentals Agent",
        status="complete",
        score=60,
        summary="First attempt",
        metrics=[
            Metric(
                code="roe",
                label="ROE",
                value=18.2,
                unit="%",
                period="FY 2026",
                period_type="FY",
                accounting_basis="consolidated",
                source_ids=["source-1"],
            )
        ],
    )
    corrected = missing_date.model_copy(
        update={
            "summary": "Corrected attempt",
            "metrics": [
                missing_date.metrics[0].model_copy(update={"as_of_date": date(2026, 3, 31)})
            ],
        }
    )
    llm, api = make_nebius_llm(
        chat_response(content=missing_date.model_dump_json()),
        chat_response(content=corrected.model_dump_json()),
    )

    answer = llm.generate(
        schema=AnalysisBlock,
        instructions="Return a fundamentals analysis.",
        prompt="Use source-1.",
    )

    assert answer.summary == "Corrected attempt"
    assert len(api.calls) == 2
    retry_system_message = str(api.calls[1]["messages"][0]["content"])
    assert "metrics.0 (roe) needs as_of_date" in retry_system_message
    assert all(stream.exited for stream in api.streams)


def test_success_uses_strict_parser_and_never_stores_the_response() -> None:
    llm, api = make_llm(FakeResponse())

    answer = llm.generate(
        schema=ExampleAnswer,
        instructions="Return an investment score.",
        prompt="Score PNGJL.",
    )

    assert answer == ExampleAnswer(company="PNGJL", score=82)
    assert len(api.calls) == 1
    request = api.calls[0]
    assert request["model"] == "test-model"
    assert request["input"] == "Score PNGJL."
    assert request["store"] is False
    assert request["text_format"] is ExampleAnswer


@pytest.mark.parametrize(
    "bad_response",
    [
        FakeResponse(status="incomplete", incomplete_details={"reason": "max_output_tokens"}),
        FakeResponse(output_text="   "),
        FakeResponse(output_text="not JSON"),
        FakeResponse(output_text='{"company": "PNGJL", "score": "not-a-number"}'),
    ],
    ids=["incomplete", "empty", "malformed-json", "schema-invalid"],
)
def test_invalid_output_is_retried_with_a_repair_instruction(
    bad_response: FakeResponse,
) -> None:
    llm, api = make_llm(bad_response, FakeResponse())

    answer = llm.generate(
        schema=ExampleAnswer,
        instructions="Return an investment score.",
        prompt="Score PNGJL.",
    )

    assert answer.score == 82
    assert len(api.calls) == 2
    assert REPAIR_INSTRUCTION.strip() not in str(api.calls[0]["instructions"])
    assert REPAIR_INSTRUCTION.strip() in str(api.calls[1]["instructions"])
    # Privacy is not weakened during a retry.
    assert all(call["store"] is False for call in api.calls)


def test_api_exception_is_retried_and_can_recover() -> None:
    llm, api = make_llm(OpenAIError("temporary API problem"), FakeResponse())

    answer = llm.generate(
        schema=ExampleAnswer,
        instructions="Return an investment score.",
        prompt="Score PNGJL.",
    )

    assert answer.company == "PNGJL"
    assert len(api.calls) == 2
    assert REPAIR_INSTRUCTION.strip() in str(api.calls[1]["instructions"])


def test_failure_stops_at_the_configured_attempt_limit() -> None:
    llm, api = make_llm(
        FakeResponse(output_text="bad JSON"),
        RuntimeError("service unavailable"),
    )

    with pytest.raises(StructuredOutputError, match="after 2 attempts") as caught:
        llm.generate(
            schema=ExampleAnswer,
            instructions="Return an investment score.",
            prompt="Score PNGJL.",
        )

    assert len(api.calls) == 2
    assert isinstance(caught.value.__cause__, RuntimeError)
    assert "service unavailable" in str(caught.value.__cause__)


def test_max_attempts_must_be_positive() -> None:
    with pytest.raises(ValueError, match="at least 1"):
        StructuredLLM(
            api_key="test-key",
            model="test-model",
            client=SimpleNamespace(),
            max_attempts=0,
        )


def test_max_concurrent_requests_must_be_positive() -> None:
    with pytest.raises(ValueError, match="max_concurrent_requests"):
        StructuredLLM(
            api_key="test-key",
            model="test-model",
            client=SimpleNamespace(),
            max_concurrent_requests=0,
        )


def test_request_timeout_must_be_positive() -> None:
    with pytest.raises(ValueError, match="request_timeout_seconds"):
        StructuredLLM(
            api_key="test-key",
            model="test-model",
            client=SimpleNamespace(),
            request_timeout_seconds=0,
        )


def test_nebius_generation_budget_must_be_positive() -> None:
    with pytest.raises(ValueError, match="max_tokens"):
        StructuredLLM(
            api_key="test-key",
            model="moonshotai/Kimi-K3",
            provider="nebius",
            client=SimpleNamespace(),
            max_tokens=0,
        )


def test_nebius_reasoning_effort_is_validated() -> None:
    with pytest.raises(ValueError, match="reasoning_effort"):
        StructuredLLM(
            api_key="test-key",
            model="moonshotai/Kimi-K3",
            provider="nebius",
            client=SimpleNamespace(),
            reasoning_effort="max",  # type: ignore[arg-type]
        )


def test_default_sdk_client_targets_nebius(monkeypatch: pytest.MonkeyPatch) -> None:
    created_with: dict[str, object] = {}
    fake_client = SimpleNamespace(responses=SimpleNamespace())

    def fake_openai(**kwargs: object) -> SimpleNamespace:
        created_with.update(kwargs)
        return fake_client

    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    monkeypatch.delenv("NEBIUS_BASE_URL", raising=False)
    monkeypatch.setattr("competitive_scoring.llm.OpenAI", fake_openai)

    llm = StructuredLLM(api_key="nebius-key", model="provider/model")

    assert llm.provider == "nebius"
    assert llm.base_url == DEFAULT_NEBIUS_BASE_URL
    assert created_with == {
        "api_key": "nebius-key",
        "base_url": DEFAULT_NEBIUS_BASE_URL,
        "max_retries": 0,
        "timeout": 300.0,
    }


def test_nebius_sdk_client_accepts_a_custom_base_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created_with: dict[str, object] = {}

    def fake_openai(**kwargs: object) -> SimpleNamespace:
        created_with.update(kwargs)
        return SimpleNamespace(responses=SimpleNamespace())

    monkeypatch.setenv("LLM_PROVIDER", "nebius")
    monkeypatch.setenv("NEBIUS_BASE_URL", "https://nebius.example/v1/")
    monkeypatch.setattr("competitive_scoring.llm.OpenAI", fake_openai)

    StructuredLLM(api_key="nebius-key", model="provider/model")

    assert created_with["base_url"] == "https://nebius.example/v1/"


def test_openai_sdk_client_uses_its_standard_endpoint_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created_with: dict[str, object] = {}

    def fake_openai(**kwargs: object) -> SimpleNamespace:
        created_with.update(kwargs)
        return SimpleNamespace(responses=SimpleNamespace())

    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    monkeypatch.setattr("competitive_scoring.llm.OpenAI", fake_openai)

    llm = StructuredLLM(api_key="openai-key", model="gpt-test")

    assert llm.provider == "openai"
    assert llm.base_url is None
    assert created_with == {
        "api_key": "openai-key",
        "max_retries": 0,
        "timeout": 300.0,
    }


def test_invalid_provider_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "unknown")

    with pytest.raises(ValueError, match="LLM_PROVIDER"):
        StructuredLLM(
            api_key="test-key",
            model="test-model",
            client=SimpleNamespace(),
        )
