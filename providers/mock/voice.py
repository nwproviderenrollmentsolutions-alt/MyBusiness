"""MOCK voice synthesis. Synthesizes nothing, spends nothing."""

from __future__ import annotations

import hashlib

from providers.base import VoiceAsset


class MockVoiceProvider:
    """Deterministic stand-in for a real text-to-speech vendor."""

    name = "mock_voice"
    is_mock = True

    def __init__(self, *, seed: int = 1337) -> None:
        self._seed = seed

    def synthesize(self, *, text: str, voice_id: str | None) -> VoiceAsset:
        digest = hashlib.sha256(f"{self._seed}|{voice_id or ''}|{text}".encode()).hexdigest()[:12]
        return VoiceAsset(
            provider=self.name,
            is_mock=True,
            status="queued",
            asset_url=None,
            duration_seconds=None,
            provider_job_id=f"mock-voice-{digest}",
            error=None,
        )
