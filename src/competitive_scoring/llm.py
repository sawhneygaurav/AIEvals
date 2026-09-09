"""Structured-output wrapper for OpenAI and Nebius language models.

Only live mode uses this class.  Demo mode deliberately runs without an API key
so a learner can inspect the full orchestration before paying for API calls.

OpenAI uses the Responses API. Nebius endpoint support is model-specific, so
its endpoint is explicit configuration rather than something inferred from the
provider name. The recommended Qwen model uses Responses; Kimi K3 needs Chat
Completions.
"""

from __future__ import annotations

import json
import os
import threading
from typing import Any, TypeVar, cast

from openai import OpenAI, OpenAIError
from openai.lib._pydantic import to_strict_json_schema
from pydantic import BaseModel, ValidationError

from .config import (
    DEFAULT_NEBIUS_API_STYLE,
    DEFAULT_NEBIUS_BASE_URL,
    DEFAULT_NEBIUS_MAX_TOKENS,
    DEFAULT_NEBIUS_REASONING_EFFORT,
    LLMProvider,
    NebiusAPIStyle,
    ReasoningEffort,
)
from .tracing import event, span, traced

SchemaT = TypeVar("SchemaT", bound=BaseModel)


def _record_completion(response: Any) -> None:
    """Read SDK operational fields only; never record completion content."""
    usage = getattr(response, "usage", None)
    choices = getattr(response, "choices", None)
    reason = getattr(choices[0], "finish_reason", None) if choices else None
    details = (getattr(usage, "completion_tokens_details", None)
               or getattr(usage, "output_tokens_details", None))
    event(
        "llm.completion",
        request_id=getattr(response, "_request_id", None) or getattr(response, "id", None),
        finish_reason=reason or getattr(response, "status", None),
        input_tokens=getattr(usage, "prompt_tokens", None)
        if hasattr(usage, "prompt_tokens") else getattr(usage, "input_tokens", None),
        output_tokens=getattr(usage, "completion_tokens", None)
        if hasattr(usage, "completion_tokens") else getattr(usage, "output_tokens", None),
        reasoning_tokens=getattr(details, "reasoning_tokens", None),
    )


# A retry is useful for two different transient failures:
#
# 1. The HTTP request can fail (for example, a brief connection problem).
# 2. The model can finish without producing JSON that our Pydantic model accepts.
#
# We deliberately keep this to two attempts.  It gives a transient problem one
# chance to recover without allowing one graph node to loop indefinitely or run
# up an unexpected API bill.
DEFAULT_MAX_ATTEMPTS = 2
DEFAULT_REQUEST_TIMEOUT_SECONDS = 300.0

REPAIR_INSTRUCTION = """\
A previous attempt did not produce a complete response that matched the required
JSON schema. Return exactly one complete JSON object. Include every required
field, use the declared data types, and do not add prose outside the JSON object.
"""


class StructuredOutputError(RuntimeError):
    """Safe, contextual error raised after all structured-output attempts fail.

    The raw model response is deliberately not retained. It can contain long
    evidence excerpts, and showing it in Streamlit would be both noisy and a
    privacy risk. The provider, model, schema, graph context, and a compact
    validation reason are enough to diagnose the failed boundary.
    """

    def __init__(
        self,
        *,
        provider: LLMProvider,
        model: str,
        schema_name: str,
        context: str | None,
        attempts: int,
        feedback: str,
    ) -> None:
        self.provider = provider
        self.model = model
        self.schema_name = schema_name
        self.context = context
        self.attempts = attempts
        self.feedback = feedback
        location = f" for {context}" if context else ""
        super().__init__(
            f"{provider.title()} model '{model}' could not produce valid "
            f"{schema_name} data{location} after {attempts} attempts. {feedback}"
        )


def _validation_feedback(error: Exception) -> str:
    """Turn a failure into short feedback that is safe to send on a retry."""

    if isinstance(error, json.JSONDecodeError):
        return f"JSON syntax error: {error.msg} at line {error.lineno}, column {error.colno}."
    if isinstance(error, ValidationError):
        # Pydantic messages name the bad fields but do not include the model's
        # raw response. Limit the list so a pathological response cannot make
        # the retry prompt or the UI enormous.
        issues = []
        for item in error.errors(include_url=False)[:6]:
            path = ".".join(str(part) for part in item["loc"]) or "root"
            issues.append(f"{path}: {item['msg']}")
        return "Schema validation error: " + "; ".join(issues)
    if isinstance(error, OpenAIError):
        return f"Provider request error ({type(error).__name__})."
    return str(error).strip() or type(error).__name__


def _schema_instruction(schema: type[BaseModel]) -> str:
    """Return the compact schema text Nebius recommends including in the prompt."""

    # Use the exact recursively-required schema generated by the OpenAI SDK's
    # parse helper. A plain Pydantic schema treats fields with defaults as
    # optional, which previously let Kimi omit ``score`` while saying that an
    # analysis was complete.
    compact_schema = json.dumps(to_strict_json_schema(schema), separators=(",", ":"))
    return (
        "The response must match this JSON schema exactly. Also obey any "
        f"cross-field rules stated above. Schema: {compact_schema}"
    )


def _validate_analysis_semantics(result: SchemaT) -> SchemaT:
    """Reject incomplete metric metadata before it reaches the final audit.

    JSON Schema checks shapes and data types, but it cannot express all of this
    app's financial rules. ``AnalysisBlock``-like results contain a ``metrics``
    list, so we perform the few evidence rules that caused the prior live run's
    late audit failures here. Raising now lets the same agent repair its answer
    on attempt two instead of discovering the problem after all 16 calls.
    """

    metrics = getattr(result, "metrics", None)
    if not isinstance(metrics, list):
        return result

    financial_codes = {
        "revenue",
        "pat",
        "shareholders_equity",
        "borrowings",
        "ebitda_margin",
        "pat_margin",
        "roe",
        "roce",
        "operating_cash_flow",
        "free_cash_flow",
        "cfo_pat",
        "debt_equity",
        "net_debt_equity",
        "interest_coverage",
        "current_ratio",
        "inventory_days",
        "working_capital_days",
        "revenue_growth",
        "profit_growth",
    }
    issues: list[str] = []
    for index, metric in enumerate(metrics):
        if getattr(metric, "value", None) is None:
            continue
        label = getattr(metric, "code", "metric")
        prefix = f"metrics.{index} ({label})"
        if not getattr(metric, "source_ids", None):
            issues.append(f"{prefix} needs at least one source_id")
        if getattr(metric, "as_of_date", None) is None:
            issues.append(f"{prefix} needs as_of_date")
        if getattr(metric, "period_type", "unknown") == "unknown":
            issues.append(f"{prefix} needs a normalized period_type")
        if label in financial_codes and getattr(metric, "accounting_basis", "unknown") == "unknown":
            issues.append(f"{prefix} needs accounting_basis")
        if (
            getattr(metric, "measurement_type", "reported") == "derived"
            and not getattr(metric, "formula", "").strip()
        ):
            issues.append(f"{prefix} needs a derivation formula")

    if issues:
        raise RuntimeError("Semantic validation error: " + "; ".join(issues[:8]))
    return result


def _resolve_provider(provider: str | None) -> LLMProvider:
    """Return the selected provider, defaulting live inference to Nebius."""

    provider_text = (provider or os.getenv("LLM_PROVIDER", "nebius")).lower().strip()
    if provider_text not in {"nebius", "openai"}:
        raise ValueError("LLM_PROVIDER must be either 'nebius' or 'openai'.")
    return cast(LLMProvider, provider_text)


def _resolve_base_url(provider: LLMProvider, base_url: str | None) -> str | None:
    """Resolve the SDK base URL without sending a credential to the wrong host."""

    if base_url is not None:
        return base_url.strip() or None
    if provider == "nebius":
        return os.getenv("NEBIUS_BASE_URL", "").strip() or DEFAULT_NEBIUS_BASE_URL
    return os.getenv("OPENAI_BASE_URL", "").strip() or None


class StructuredLLM:
    """Ask a model for JSON and validate it against a Pydantic schema."""

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        provider: LLMProvider | None = None,
        base_url: str | None = None,
        nebius_api_style: NebiusAPIStyle = DEFAULT_NEBIUS_API_STYLE,
        client: Any | None = None,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
        max_concurrent_requests: int = 4,
        request_timeout_seconds: float = DEFAULT_REQUEST_TIMEOUT_SECONDS,
        max_tokens: int = DEFAULT_NEBIUS_MAX_TOKENS,
        reasoning_effort: ReasoningEffort = DEFAULT_NEBIUS_REASONING_EFFORT,
    ) -> None:
        """Create the small API wrapper.

        Both providers use the OpenAI Python SDK. OpenAI always uses Responses;
        Nebius uses the endpoint selected for the configured model. ``client``
        is optional in the application, but accepting it makes this network
        boundary easy to test with a fake client.
        """

        if max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        if max_concurrent_requests < 1:
            raise ValueError("max_concurrent_requests must be at least 1")
        if request_timeout_seconds <= 0:
            raise ValueError("request_timeout_seconds must be greater than zero")
        if max_tokens < 1:
            raise ValueError("max_tokens must be at least 1")
        if reasoning_effort not in {"low", "medium", "high"}:
            raise ValueError("reasoning_effort must be 'low', 'medium', or 'high'")
        if nebius_api_style not in {"responses", "chat_completions"}:
            raise ValueError("nebius_api_style must be 'responses' or 'chat_completions'")
        if not model.strip():
            raise ValueError("model must not be blank")

        self.provider = _resolve_provider(provider)
        self.base_url = _resolve_base_url(self.provider, base_url)
        if client is not None:
            self._client = client
        else:
            client_options: dict[str, Any] = {
                "api_key": api_key,
                # This wrapper already owns a visible two-attempt retry. Turning
                # off hidden SDK retries keeps cost and wall time predictable.
                "max_retries": 0,
                "timeout": request_timeout_seconds,
            }
            if self.base_url is not None:
                client_options["base_url"] = self.base_url
            self._client = OpenAI(**client_options)
        self.model = model
        self.nebius_api_style = nebius_api_style
        self.max_attempts = max_attempts
        self.max_tokens = max_tokens
        self.reasoning_effort = reasoning_effort
        # LangGraph can run four company subgraphs and four analysts per company.
        # This shared gate preserves that logical parallelism while preventing a
        # nested fan-out from sending sixteen model requests at the same instant.
        self._request_slots = threading.BoundedSemaphore(max_concurrent_requests)

    def _generate_with_responses(
        self,
        *,
        schema: type[SchemaT],
        instructions: str,
        prompt: str,
        max_tokens: int,
    ) -> SchemaT:
        """Call Responses and return the SDK-validated Pydantic object.

        ``responses.parse`` converts the Pydantic model into a strict JSON
        schema, sends that schema to the provider, and parses the reply back
        into the same model. This removes the permissive ``strict=False`` plus
        raw ``json.loads`` combination that allowed malformed Kimi JSON through.
        """

        # `store=False` is intentional on every attempt. The response is used
        # only for this graph run and is not retained as conversation state.
        request: dict[str, Any] = {
            "model": self.model,
            "instructions": instructions,
            "input": prompt,
            "store": False,
            "text_format": schema,
        }
        if self.provider == "nebius":
            # Nebius accepts the Responses API spelling for this limit. It caps
            # cost and prevents an unexpectedly long response from blocking a
            # company worker indefinitely.
            request["max_output_tokens"] = max_tokens
        response = self._client.responses.parse(
            **request,
        )
        _record_completion(response)

        status = getattr(response, "status", None)
        if status != "completed":
            details = getattr(response, "incomplete_details", None)
            detail_suffix = f" Details: {details!r}." if details is not None else ""
            raise RuntimeError(
                f"The model response status was {status!r}, not 'completed'.{detail_suffix}"
            )

        parsed = getattr(response, "output_parsed", None)
        if parsed is None:
            raise RuntimeError("The model returned no validated structured output.")
        if isinstance(parsed, schema):
            return _validate_analysis_semantics(parsed)
        # Some compatible providers expose a plain mapping rather than the SDK's
        # parsed model instance. Validate it once more at our own trust boundary.
        return _validate_analysis_semantics(schema.model_validate(parsed))

    def _generate_with_chat_completions(
        self,
        *,
        schema: type[SchemaT],
        instructions: str,
        prompt: str,
        max_tokens: int,
    ) -> SchemaT:
        """Stream Nebius Chat Completions and return strict parsed output.

        Nebius is OpenAI-compatible at the SDK level, but endpoint support is
        model-specific. Kimi K3 supports Chat Completions. The SDK's ``stream``
        helper converts our Pydantic class into the same strict JSON schema as
        ``parse`` and parses the accumulated reply when the stream finishes.

        Streaming matters for Kimi's longer analytical answers: reasoning and
        content chunks keep the HTTP connection active while the model works.
        A non-streaming request can otherwise wait for the entire answer before
        receiving data and exhaust the client's read timeout even though the
        provider is still generating a valid completion.
        """

        # The stream helper is a context manager so the underlying connection
        # is always closed, including when JSON/Pydantic validation raises.
        with self._client.chat.completions.stream(
            model=self.model,
            # Nebius accepts the OpenAI-compatible privacy flag here too. The
            # completion is needed only for this graph run.
            store=False,
            # The limit includes Kimi's hidden reasoning plus visible JSON.
            # Low effort preserves enough of the budget for the actual answer.
            max_tokens=max_tokens,
            reasoning_effort=self.reasoning_effort,
            # Kimi's model card recommends its native training temperature.
            temperature=1.0,
            top_p=0.95,
            messages=[
                {"role": "system", "content": instructions},
                {"role": "user", "content": prompt},
            ],
            # Passing the class (rather than a hand-written dictionary) makes
            # the SDK send its recursively-required strict JSON schema and
            # deserialize the final accumulated content into the same class.
            response_format=schema,
        ) as stream:
            response = stream.get_final_completion()

        _record_completion(response)

        choices = getattr(response, "choices", None)
        if not choices:
            raise RuntimeError("The model returned no completion choices.")

        choice = choices[0]
        finish_reason = getattr(choice, "finish_reason", None)
        if finish_reason not in {None, "stop"}:
            raise RuntimeError(f"The model completion stopped with reason {finish_reason!r}.")

        message = getattr(choice, "message", None)
        if getattr(message, "refusal", None):
            raise RuntimeError("The model refused to produce the requested JSON.")
        parsed = getattr(message, "parsed", None)
        if parsed is None:
            raise RuntimeError("The model returned no validated structured output.")
        if isinstance(parsed, schema):
            return _validate_analysis_semantics(parsed)
        return _validate_analysis_semantics(schema.model_validate(parsed))

    @traced("llm.generate")
    def generate(
        self,
        *,
        schema: type[SchemaT],
        instructions: str,
        prompt: str,
        context: str | None = None,
        max_tokens: int | None = None,
    ) -> SchemaT:
        """Return validated structured output, retrying once when necessary.

        A response is usable only when the API marks it ``completed``, it has
        non-blank text, the text is valid JSON, and Pydantic accepts the JSON.
        Failed API calls and all four output failures receive the same bounded
        retry treatment.
        """

        token_limit = self.max_tokens if max_tokens is None else max_tokens
        if token_limit < 1 or token_limit > self.max_tokens:
            raise ValueError(
                f"Per-call max_tokens must be between 1 and the configured {self.max_tokens}."
            )

        last_error: Exception | None = None
        last_feedback = ""

        for attempt_number in range(1, self.max_attempts + 1):
            attempt_instructions = instructions.rstrip()
            if self.provider == "nebius":
                # Nebius recommends placing the schema in both the API parameter
                # and the prompt. The API still enforces the schema; this copy
                # helps the model understand defaults and cross-field rules.
                attempt_instructions += f"\n\n{_schema_instruction(schema)}"
            if attempt_number > 1:
                # A precise field/path or JSON location gives the second attempt
                # something concrete to fix. We never send the raw failed output
                # back or try to silently patch financial data ourselves.
                attempt_instructions += (
                    f"\n\n{REPAIR_INSTRUCTION}\nPrevious failure: {last_feedback}"
                )

            try:
                with span(
                    "llm.attempt", provider=self.provider, model=self.model,
                    schema=schema.__name__, attempt=attempt_number,
                    max_attempts=self.max_attempts, max_tokens=token_limit,
                    prompt_chars=len(prompt) + len(attempt_instructions),
                ):
                    with span("llm.queue_wait"):
                        self._request_slots.acquire()
                    try:
                        with span("llm.provider"):
                            use_chat = (
                                self.provider == "nebius"
                                and self.nebius_api_style == "chat_completions"
                            )
                            if use_chat:
                                return self._generate_with_chat_completions(
                                    schema=schema,
                                    instructions=attempt_instructions,
                                    prompt=prompt,
                                    max_tokens=token_limit,
                                )
                            return self._generate_with_responses(
                                schema=schema,
                                instructions=attempt_instructions,
                                prompt=prompt,
                                max_tokens=token_limit,
                            )
                    finally:
                        self._request_slots.release()

            except (OpenAIError, json.JSONDecodeError, ValidationError, RuntimeError) as error:
                last_error = error
                last_feedback = _validation_feedback(error)
                if attempt_number == self.max_attempts:
                    break
                event("llm.retry", attempt=attempt_number + 1, schema=schema.__name__)

        # ``max_attempts`` is validated above, so reaching this line guarantees
        # that ``last_error`` was set during the final failed attempt.
        assert last_error is not None
        raise StructuredOutputError(
            provider=self.provider,
            model=self.model,
            schema_name=schema.__name__,
            context=context,
            attempts=self.max_attempts,
            feedback=last_feedback,
        ) from last_error
