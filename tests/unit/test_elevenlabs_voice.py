"""ElevenLabsVoiceProvider, tested entirely against a stubbed httpx transport — no network
call ever happens in this suite.
"""

from __future__ import annotations

import httpx
import pytest

from core.errors import PermanentError, RetryableError
from providers.elevenlabs_voice import ElevenLabsVoiceProvider


def _provider(*, transport: httpx.MockTransport) -> ElevenLabsVoiceProvider:
    provider = ElevenLabsVoiceProvider(api_key="el-test-key", default_voice_id="voice-123")
    provider._client = httpx.Client(
        base_url="https://api.elevenlabs.io", transport=transport, headers=provider._client.headers
    )
    return provider


def test_missing_api_key_fails_at_construction():
    with pytest.raises(PermanentError, match="ELEVENLABS_API_KEY"):
        ElevenLabsVoiceProvider(api_key="", default_voice_id="voice-123")


def test_missing_voice_id_fails_at_construction():
    with pytest.raises(PermanentError, match="ELEVENLABS_VOICE_ID"):
        ElevenLabsVoiceProvider(api_key="el-test-key", default_voice_id="")


def test_synthesize_maps_fields_from_the_real_response_shape():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"fake-audio-bytes", headers={"request-id": "req_1"})

    provider = _provider(transport=httpx.MockTransport(handler))
    result = provider.synthesize(text="Hello", voice_id=None)

    assert result.provider == "elevenlabs"
    assert result.is_mock is False
    assert result.status == "ready"
    assert result.provider_job_id == "req_1"


def test_default_voice_id_is_used_when_none_given():
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        return httpx.Response(200, content=b"audio")

    provider = _provider(transport=httpx.MockTransport(handler))
    provider.synthesize(text="Hello", voice_id=None)
    assert seen["path"] == "/v1/text-to-speech/voice-123"


def test_explicit_voice_id_overrides_default():
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        return httpx.Response(200, content=b"audio")

    provider = _provider(transport=httpx.MockTransport(handler))
    provider.synthesize(text="Hello", voice_id="voice-override")
    assert seen["path"] == "/v1/text-to-speech/voice-override"


def test_server_error_is_retryable():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    provider = _provider(transport=httpx.MockTransport(handler))
    with pytest.raises(RetryableError):
        provider.synthesize(text="x", voice_id=None)


def test_bad_request_is_permanent_not_retried():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, text="nope")

    provider = _provider(transport=httpx.MockTransport(handler))
    with pytest.raises(PermanentError):
        provider.synthesize(text="x", voice_id=None)


def test_connection_error_is_retryable():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    provider = _provider(transport=httpx.MockTransport(handler))
    with pytest.raises(RetryableError):
        provider.synthesize(text="x", voice_id=None)
