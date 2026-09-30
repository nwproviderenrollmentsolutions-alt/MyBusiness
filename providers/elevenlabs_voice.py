"""Real voice-synthesis provider: ElevenLabs.

Selected via ``VOICE_PROVIDER=elevenlabs`` (see providers/factory.py). Requires
``ELEVENLABS_API_KEY`` and ``ELEVENLABS_VOICE_ID``.

Recommended alongside HeyGen (see providers/heygen_video_gen.py) because ElevenLabs'
Instant Voice Cloning trains a durable, named voice_id from the CEO's own voice sample —
the same durable-identity model the avatar lock relies on — rather than picking a
stock voice per call. Voice *cloning* itself (uploading the sample, training the voice)
is a one-time step done in the ElevenLabs dashboard or via ElevenLabs' own voice-cloning
API; ``ELEVENLABS_VOICE_ID`` is filled in once that voice exists.
"""

from __future__ import annotations

import httpx

from core.errors import PermanentError, RetryableError
from providers.base import VoiceAsset

_BASE_URL = "https://api.elevenlabs.io"


class ElevenLabsVoiceProvider:
    name = "elevenlabs"
    is_mock = False

    def __init__(
        self, *, api_key: str, default_voice_id: str, timeout_seconds: float = 30.0
    ) -> None:
        if not api_key:
            raise PermanentError(
                "VOICE_PROVIDER=elevenlabs requires ELEVENLABS_API_KEY to be set"
            )
        if not default_voice_id:
            raise PermanentError(
                "VOICE_PROVIDER=elevenlabs requires ELEVENLABS_VOICE_ID to be set — clone the "
                "CEO's voice in ElevenLabs first, then set its voice_id here"
            )
        self._default_voice_id = default_voice_id
        self._client = httpx.Client(
            base_url=_BASE_URL,
            headers={"xi-api-key": api_key},
            timeout=timeout_seconds,
        )

    def synthesize(self, *, text: str, voice_id: str | None) -> VoiceAsset:
        resolved_voice_id = voice_id or self._default_voice_id

        try:
            response = self._client.post(
                f"/v1/text-to-speech/{resolved_voice_id}",
                json={"text": text, "model_id": "eleven_multilingual_v2"},
            )
        except httpx.TimeoutException as exc:
            raise RetryableError(f"ElevenLabs request timed out: {exc}") from exc
        except httpx.TransportError as exc:
            raise RetryableError(f"ElevenLabs connection error: {exc}") from exc

        if response.status_code >= 500:
            raise RetryableError(
                f"ElevenLabs server error ({response.status_code}): {response.text}"
            )
        if response.status_code == 429:
            raise RetryableError(f"ElevenLabs rate limit: {response.text}")
        if response.status_code >= 400:
            raise PermanentError(
                f"ElevenLabs request rejected ({response.status_code}): {response.text}"
            )

        # ElevenLabs returns raw audio bytes synchronously (no polling), but this
        # pipeline has no blob storage wired up yet to persist them under a fetchable
        # URL — so asset_url stays None even though the synthesis itself is real and
        # complete. Wiring storage is the natural next step once a render actually needs
        # to be handed to an editor rather than just proven end-to-end.
        request_id = response.headers.get("request-id")
        return VoiceAsset(
            provider=self.name,
            is_mock=False,
            status="ready",
            asset_url=None,
            duration_seconds=None,
            provider_job_id=request_id,
            error=None,
        )
