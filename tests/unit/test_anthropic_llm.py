"""AnthropicLLMProvider, tested entirely against a stubbed SDK client — no network call
ever happens in this suite. What's being verified is the adapter's own logic: field
mapping, cost calculation, and error-to-core-exception translation, not the Anthropic API
itself.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

import anthropic
import httpx2
import pytest

from core.errors import PermanentError, RetryableError
from providers.anthropic_llm import AnthropicLLMProvider

_REQUEST = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")


def _response(status_code: int) -> httpx2.Response:
    return httpx2.Response(status_code, request=_REQUEST)


@dataclass
class _FakeTextBlock:
    text: str
    type: str = "text"


@dataclass
class _FakeUsage:
    input_tokens: int
    output_tokens: int


@dataclass
class _FakeMessage:
    content: list[_FakeTextBlock]
    usage: _FakeUsage
    model: str = "claude-opus-5"
    stop_reason: str = "end_turn"


def _provider(*, model: str = "claude-opus-5") -> AnthropicLLMProvider:
    return AnthropicLLMProvider(api_key="sk-test-key", model=model)


def test_missing_api_key_fails_at_construction():
    with pytest.raises(PermanentError, match="ANTHROPIC_API_KEY"):
        AnthropicLLMProvider(api_key="", model="claude-opus-5")


def test_complete_maps_fields_from_the_real_response_shape(monkeypatch):
    provider = _provider()
    fake_message = _FakeMessage(
        content=[_FakeTextBlock(text="Hi there")],
        usage=_FakeUsage(input_tokens=50, output_tokens=10),
    )
    monkeypatch.setattr(provider._client.messages, "create", lambda **kwargs: fake_message)

    result = provider.complete(prompt="Say hi", system="Be brief")

    assert result.provider == "anthropic"
    assert result.is_mock is False
    assert result.text == "Hi there"
    assert result.model == "claude-opus-5"
    assert result.tokens_in == 50
    assert result.tokens_out == 10
    assert result.stop_reason == "end_turn"


def test_complete_joins_multiple_text_blocks():
    provider = _provider()
    fake_message = _FakeMessage(
        content=[_FakeTextBlock(text="Part one. "), _FakeTextBlock(text="Part two.")],
        usage=_FakeUsage(input_tokens=1, output_tokens=1),
    )
    provider._client.messages.create = lambda **kwargs: fake_message

    result = provider.complete(prompt="x")
    assert result.text == "Part one. Part two."


def test_cost_is_computed_from_the_opus_5_price_table():
    provider = _provider(model="claude-opus-5")
    fake_message = _FakeMessage(
        content=[_FakeTextBlock(text="x")],
        usage=_FakeUsage(input_tokens=1_000_000, output_tokens=1_000_000),
        model="claude-opus-5",
    )
    provider._client.messages.create = lambda **kwargs: fake_message

    result = provider.complete(prompt="x")
    assert result.cost_usd == Decimal("30.00")  # $5 in + $25 out per million tokens


def test_an_unrecognized_model_is_priced_at_the_conservative_opus_5_rate():
    provider = _provider(model="claude-opus-5")
    fake_message = _FakeMessage(
        content=[_FakeTextBlock(text="x")],
        usage=_FakeUsage(input_tokens=1_000_000, output_tokens=0),
        model="some-future-model-not-in-the-table",
    )
    provider._client.messages.create = lambda **kwargs: fake_message

    result = provider.complete(prompt="x")
    assert result.cost_usd == Decimal("5.00")


def test_effort_is_only_sent_for_models_known_to_support_it():
    seen: dict[str, object] = {}

    def _capture(**kwargs: object) -> _FakeMessage:
        seen.update(kwargs)
        return _FakeMessage(
            content=[_FakeTextBlock(text="x")], usage=_FakeUsage(input_tokens=1, output_tokens=1)
        )

    opus = _provider(model="claude-opus-5")
    opus._client.messages.create = _capture
    opus.complete(prompt="x")
    assert seen.get("output_config") == {"effort": "low"}

    seen.clear()
    haiku = _provider(model="claude-haiku-4-5")
    haiku._client.messages.create = _capture
    haiku.complete(prompt="x")
    assert "output_config" not in seen


def test_temperature_is_never_forwarded_to_the_api():
    """Opus/Sonnet 5 reject explicit sampling params with a 400 — the parameter exists
    only to satisfy the LLMProvider protocol every caller shares with the mock."""
    seen: dict[str, object] = {}

    def _capture(**kwargs: object) -> _FakeMessage:
        seen.update(kwargs)
        return _FakeMessage(
            content=[_FakeTextBlock(text="x")], usage=_FakeUsage(input_tokens=1, output_tokens=1)
        )

    provider = _provider()
    provider._client.messages.create = _capture
    provider.complete(prompt="x", temperature=0.9)
    assert "temperature" not in seen


def test_system_is_omitted_entirely_when_not_given():
    seen: dict[str, object] = {}

    def _capture(**kwargs: object) -> _FakeMessage:
        seen.update(kwargs)
        return _FakeMessage(
            content=[_FakeTextBlock(text="x")], usage=_FakeUsage(input_tokens=1, output_tokens=1)
        )

    provider = _provider()
    provider._client.messages.create = _capture
    provider.complete(prompt="x")
    assert "system" not in seen


def test_rate_limit_error_is_retryable():
    provider = _provider()

    def _raise(**kwargs: object) -> _FakeMessage:
        raise anthropic.RateLimitError("slow down", response=_response(429), body=None)

    provider._client.messages.create = _raise
    with pytest.raises(RetryableError):
        provider.complete(prompt="x")


def test_connection_error_is_retryable():
    provider = _provider()

    def _raise(**kwargs: object) -> _FakeMessage:
        raise anthropic.APIConnectionError(request=_REQUEST)

    provider._client.messages.create = _raise
    with pytest.raises(RetryableError):
        provider.complete(prompt="x")


def test_server_error_is_retryable():
    provider = _provider()

    def _raise(**kwargs: object) -> _FakeMessage:
        raise anthropic.InternalServerError("oops", response=_response(500), body=None)

    provider._client.messages.create = _raise
    with pytest.raises(RetryableError):
        provider.complete(prompt="x")


def test_bad_request_is_permanent_not_retried():
    provider = _provider()

    def _raise(**kwargs: object) -> _FakeMessage:
        raise anthropic.BadRequestError("nope", response=_response(400), body=None)

    provider._client.messages.create = _raise
    with pytest.raises(PermanentError):
        provider.complete(prompt="x")


def test_authentication_error_is_permanent_not_retried():
    provider = _provider()

    def _raise(**kwargs: object) -> _FakeMessage:
        raise anthropic.AuthenticationError("bad key", response=_response(401), body=None)

    provider._client.messages.create = _raise
    with pytest.raises(PermanentError):
        provider.complete(prompt="x")
