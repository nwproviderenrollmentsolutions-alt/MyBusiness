"""MOCK video generation. Queues nothing, renders nothing, spends nothing."""

from __future__ import annotations

import hashlib

from providers.base import VideoAsset


class MockVideoGenProvider:
    """Deterministic stand-in for a real video-generation vendor.

    Always reports ``status="queued"`` with no ``asset_url`` — a mock render is never
    "ready", because no render happened. Any caller that treats a mock asset as a
    deliverable file is broken by construction, not just by convention.
    """

    name = "mock_video_gen"
    is_mock = True

    def __init__(self, *, seed: int = 1337) -> None:
        self._seed = seed

    def generate_shot(
        self,
        *,
        prompt: str,
        avatar_reference_url: str | None,
        duration_seconds: float = 8.0,
    ) -> VideoAsset:
        digest = hashlib.sha256(f"{self._seed}|{prompt}".encode()).hexdigest()[:12]
        return VideoAsset(
            provider=self.name,
            is_mock=True,
            status="queued",
            asset_url=None,
            duration_seconds=duration_seconds,
            provider_job_id=f"mock-video-{digest}",
            error=None,
        )
