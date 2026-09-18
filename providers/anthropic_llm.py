"""Real LLM provider: Anthropic's Claude API.

Selected via ``LLM_PROVIDER=anthropic`` (see providers/factory.py). Requires
``ANTHROPIC_API_KEY``. Mirrors providers/mock/llm.py's interface exactly — same
``LLMProvider`` protocol, same ``complete()`` signature — but this one spends real money
and returns real text, so it must never be reachable except by the CEO's own deliberate
config change.

Two deliberate departures from a naive ``messages.create(**kwargs)`` call, both because
this provider drafts short, simple, single-turn copy (a cold email, a proposal), not an
open-ended agentic task:

* ``temperature`` is accepted (the ``LLMProvider`` protocol requires it) but never
  forwarded — Claude Opus 5 and Sonnet 5 reject explicit sampling parameters outright
  (400), and every caller in this codebase leaves it at the protocol default anyway.
* ``output_config.effort`` is set to ``"low"`` only for models known to support tunable
  effort. Cheap, simple, non-agentic generation is exactly the workload low effort is
  for; a model that doesn't support the parameter (Haiku 4.5) simply doesn't get it,
  rather than erroring.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import anthropic

from core.errors import PermanentError, RetryableError
from providers.base import LLMResponse

#: Anthropic's published per-model pricing, dollars per million tokens (input, output).
#: Kept as a static table, not fetched live, so a cost figure is always available even
#: if the pricing page is unreachable. A model not listed here is priced at the Opus 5
#: rate — the conservative (highest) assumption among current models, so an unrecognized
#: model can never silently under-report spend against the daily cost cap.
_PRICING_PER_MILLION_TOKENS: dict[str, tuple[Decimal, Decimal]] = {
    "claude-opus-5": (Decimal("5.00"), Decimal("25.00")),
    "claude-opus-4-8": (Decimal("5.00"), Decimal("25.00")),
    "claude-opus-4-7": (Decimal("5.00"), Decimal("25.00")),
    "claude-opus-4-6": (Decimal("5.00"), Decimal("25.00")),
    "claude-sonnet-5": (Decimal("2.00"), Decimal("10.00")),
    "claude-sonnet-4-6": (Decimal("3.00"), Decimal("15.00")),
    "claude-haiku-4-5": (Decimal("1.00"), Decimal("5.00")),
    "claude-fable-5": (Decimal("10.00"), Decimal("50.00")),
    "claude-fable-5-1": (Decimal("10.00"), Decimal("50.00")),
}
_DEFAULT_PRICING = _PRICING_PER_MILLION_TOKENS["claude-opus-5"]

#: Models that accept output_config.effort. Haiku 4.5 and older models error on it.
_EFFORT_CAPABLE_MODELS = frozenset(
    {
        "claude-opus-5",
        "claude-opus-4-8",
        "claude-opus-4-7",
        "claude-opus-4-6",
        "claude-sonnet-5",
        "claude-sonnet-4-6",
        "claude-fable-5",
        "claude-fable-5-1",
    }
)


def _cost_usd(*, model: str, tokens_in: int, tokens_out: int) -> Decimal:
    input_price, output_price = _PRICING_PER_MILLION_TOKENS.get(model, _DEFAULT_PRICING)
    million = Decimal(1_000_000)
    return (Decimal(tokens_in) * input_price + Decimal(tokens_out) * output_price) / million


class AnthropicLLMProvider:
    name = "anthropic"
    is_mock = False

    def __init__(self, *, api_key: str, model: str) -> None:
        if not api_key:
            raise PermanentError("LLM_PROVIDER=anthropic requires ANTHROPIC_API_KEY to be set")
        self._client = anthropic.Anthropic(api_key=api_key)
        self._model = model

    def complete(
        self,
        *,
        prompt: str,
        system: str | None = None,
        max_tokens: int = 1024,
        temperature: float = 0.2,
    ) -> LLMResponse:
        kwargs: dict[str, Any] = {
            "model": self._model,
            "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": prompt}],
        }
        if system is not None:
            kwargs["system"] = system
        if self._model in _EFFORT_CAPABLE_MODELS:
            kwargs["output_config"] = {"effort": "low"}

        try:
            response = self._client.messages.create(**kwargs)
        except anthropic.RateLimitError as exc:
            raise RetryableError(f"Anthropic rate limit: {exc}") from exc
        except anthropic.APIConnectionError as exc:
            raise RetryableError(f"Anthropic connection error: {exc}") from exc
        except anthropic.APIStatusError as exc:
            if exc.status_code >= 500:
                raise RetryableError(f"Anthropic server error ({exc.status_code}): {exc}") from exc
            raise PermanentError(f"Anthropic request rejected ({exc.status_code}): {exc}") from exc
        except anthropic.AnthropicError as exc:
            raise PermanentError(f"Anthropic client error: {exc}") from exc

        text = "".join(block.text for block in response.content if block.type == "text")
        tokens_in = response.usage.input_tokens
        tokens_out = response.usage.output_tokens

        return LLMResponse(
            provider=self.name,
            is_mock=False,
            text=text,
            model=str(response.model),
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            cost_usd=_cost_usd(
                model=str(response.model), tokens_in=tokens_in, tokens_out=tokens_out
            ),
            stop_reason=response.stop_reason,
        )
