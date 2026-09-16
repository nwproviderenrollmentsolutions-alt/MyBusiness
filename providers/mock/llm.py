"""MOCK LLM provider. Returns deterministic text and spends no money."""

from __future__ import annotations

import hashlib
from decimal import Decimal

from providers.base import LLMResponse

#: Rough token estimate. Good enough for exercising cost plumbing, and the reported cost
#: is zero regardless, because a mock genuinely spends nothing.
_CHARS_PER_TOKEN = 4

MOCK_MARKER = "[MOCK LLM OUTPUT — NOT FOR EXTERNAL USE]"


class MockLLMProvider:
    """Deterministic stand-in for a real model.

    Output always carries a visible marker so mock-generated copy cannot be mistaken for
    a finished message, and ``cost_usd`` is zero because no API call was made.
    """

    name = "mock_llm"
    is_mock = True

    def __init__(self, *, seed: int = 1337) -> None:
        self._seed = seed

    def complete(
        self,
        *,
        prompt: str,
        system: str | None = None,
        max_tokens: int = 1024,
        temperature: float = 0.2,
    ) -> LLMResponse:
        digest = hashlib.sha256(f"{self._seed}|{system or ''}|{prompt}".encode()).hexdigest()[:12]
        text = (
            f"{MOCK_MARKER}\n"
            f"deterministic response {digest} to a {len(prompt)}-character prompt."
        )
        return LLMResponse(
            provider=self.name,
            is_mock=True,
            text=text,
            model="mock-model-v1",
            tokens_in=max(1, len(prompt) // _CHARS_PER_TOKEN),
            tokens_out=max(1, len(text) // _CHARS_PER_TOKEN),
            cost_usd=Decimal("0"),
            stop_reason="end_turn",
        )
