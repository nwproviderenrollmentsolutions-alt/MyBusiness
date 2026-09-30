"""HeyGenVideoGenProvider, tested entirely against a stubbed httpx transport — no network
call ever happens in this suite. What's being verified is the adapter's own logic: request
shape, response mapping, and error-to-core-exception translation, not the HeyGen API itself.
"""

from __future__ import annotations

import httpx
import pytest

from core.errors import PermanentError, RetryableError
from providers.heygen_video_gen import HeyGenVideoGenProvider


def _provider(*, transport: httpx.MockTransport) -> HeyGenVideoGenProvider:
    provider = HeyGenVideoGenProvider(api_key="hg-test-key", avatar_id="avatar-123")
    provider._client = httpx.Client(
        base_url="https://api.heygen.com", transport=transport, headers=provider._client.headers
    )
    return provider


def test_missing_api_key_fails_at_construction():
    with pytest.raises(PermanentError, match="HEYGEN_API_KEY"):
        HeyGenVideoGenProvider(api_key="", avatar_id="avatar-123")


def test_missing_avatar_id_fails_at_construction():
    with pytest.raises(PermanentError, match="HEYGEN_AVATAR_ID"):
        HeyGenVideoGenProvider(api_key="hg-test-key", avatar_id="")


def test_generate_shot_maps_fields_from_the_real_response_shape():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": {"video_id": "vid_abc123"}})

    provider = _provider(transport=httpx.MockTransport(handler))
    result = provider.generate_shot(
        prompt="Say hi", avatar_reference_url=None, duration_seconds=6.0
    )

    assert result.provider == "heygen"
    assert result.is_mock is False
    assert result.status == "queued"
    assert result.asset_url is None
    assert result.duration_seconds == 6.0
    assert result.provider_job_id == "vid_abc123"


def test_avatar_id_from_construction_is_sent_not_the_reference_url():
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = request.read()
        return httpx.Response(200, json={"data": {"video_id": "vid_1"}})

    provider = _provider(transport=httpx.MockTransport(handler))
    provider.generate_shot(prompt="x", avatar_reference_url="https://example.invalid/someone-else.png")

    import json

    body = json.loads(seen["body"])
    assert body["video_inputs"][0]["character"]["avatar_id"] == "avatar-123"


def test_missing_video_id_is_permanent():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": {}})

    provider = _provider(transport=httpx.MockTransport(handler))
    with pytest.raises(PermanentError, match="no video_id"):
        provider.generate_shot(prompt="x", avatar_reference_url=None)


def test_server_error_is_retryable():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    provider = _provider(transport=httpx.MockTransport(handler))
    with pytest.raises(RetryableError):
        provider.generate_shot(prompt="x", avatar_reference_url=None)


def test_rate_limit_is_retryable():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, text="slow down")

    provider = _provider(transport=httpx.MockTransport(handler))
    with pytest.raises(RetryableError):
        provider.generate_shot(prompt="x", avatar_reference_url=None)


def test_bad_request_is_permanent_not_retried():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, text="nope")

    provider = _provider(transport=httpx.MockTransport(handler))
    with pytest.raises(PermanentError):
        provider.generate_shot(prompt="x", avatar_reference_url=None)


def test_connection_error_is_retryable():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    provider = _provider(transport=httpx.MockTransport(handler))
    with pytest.raises(RetryableError):
        provider.generate_shot(prompt="x", avatar_reference_url=None)
