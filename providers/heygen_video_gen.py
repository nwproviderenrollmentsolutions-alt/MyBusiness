"""Real video-generation provider: HeyGen.

Selected via ``VIDEO_GEN_PROVIDER=heygen`` (see providers/factory.py). Requires
``HEYGEN_API_KEY`` and ``HEYGEN_AVATAR_ID``.

HeyGen is the recommended vendor for this pipeline specifically because it treats an
avatar as a durable, named asset (a "photo avatar" or "instant avatar" trained once from
the CEO's own reference images) rather than re-describing a person from a text prompt on
every call, the way pure text-to-video models (Kling, Veo, Sora) do. That matches the
spec's own model: one locked identity, reused verbatim across every generated shot.

Avatar *creation* — turning the CEO's uploaded reference images into a HeyGen avatar_id —
is a one-time step done in the HeyGen dashboard (or via HeyGen's own avatar-training API),
not something this per-shot method does. ``HEYGEN_AVATAR_ID`` is filled in once that
avatar exists; until it is, ``VIDEO_GEN_PROVIDER`` stays ``mock``. ``avatar_reference_url``
is accepted (the ``VideoGenProvider`` protocol requires it) but not forwarded — HeyGen
resolves the avatar from ``HEYGEN_AVATAR_ID``, not from a per-call reference image, so a
generation can never accidentally point at a different face than the one already trained.
"""

from __future__ import annotations

import httpx

from core.errors import PermanentError, RetryableError
from providers.base import VideoAsset

_BASE_URL = "https://api.heygen.com"
_GENERATE_PATH = "/v2/video/generate"


class HeyGenVideoGenProvider:
    name = "heygen"
    is_mock = False

    def __init__(self, *, api_key: str, avatar_id: str, timeout_seconds: float = 30.0) -> None:
        if not api_key:
            raise PermanentError("VIDEO_GEN_PROVIDER=heygen requires HEYGEN_API_KEY to be set")
        if not avatar_id:
            raise PermanentError(
                "VIDEO_GEN_PROVIDER=heygen requires HEYGEN_AVATAR_ID to be set — train the "
                "CEO's avatar in HeyGen first, then set its avatar_id here"
            )
        self._avatar_id = avatar_id
        self._client = httpx.Client(
            base_url=_BASE_URL,
            headers={"X-Api-Key": api_key, "Content-Type": "application/json"},
            timeout=timeout_seconds,
        )

    def generate_shot(
        self,
        *,
        prompt: str,
        avatar_reference_url: str | None,
        duration_seconds: float = 8.0,
    ) -> VideoAsset:
        payload = {
            "video_inputs": [
                {
                    "character": {"type": "avatar", "avatar_id": self._avatar_id},
                    "voice": {"type": "text", "input_text": prompt},
                }
            ],
            "dimension": {"width": 1080, "height": 1920},
        }

        try:
            response = self._client.post(_GENERATE_PATH, json=payload)
        except httpx.TimeoutException as exc:
            raise RetryableError(f"HeyGen request timed out: {exc}") from exc
        except httpx.TransportError as exc:
            raise RetryableError(f"HeyGen connection error: {exc}") from exc

        if response.status_code >= 500:
            raise RetryableError(f"HeyGen server error ({response.status_code}): {response.text}")
        if response.status_code == 429:
            raise RetryableError(f"HeyGen rate limit: {response.text}")
        if response.status_code >= 400:
            raise PermanentError(
                f"HeyGen request rejected ({response.status_code}): {response.text}"
            )

        body = response.json()
        data = body.get("data") or {}
        job_id = data.get("video_id")
        if not job_id:
            raise PermanentError(f"HeyGen response had no video_id: {body}")

        return VideoAsset(
            provider=self.name,
            is_mock=False,
            status="queued",
            asset_url=None,
            duration_seconds=duration_seconds,
            provider_job_id=job_id,
            error=None,
        )
