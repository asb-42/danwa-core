"""Tests for the Cloud Whisper API STT provider."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from backend.services.stt_service import STTService


class _FakeProfile:
    """Minimal profile for testing whisper-api provider."""

    def __init__(
        self,
        provider: str = "whisper-api",
        model: str = "whisper-1",
        api_key: str | None = "test-key",
        api_key_env: str = "OPENAI_API_KEY",
        api_base: str | None = None,
        timeout: int = 60,
    ) -> None:
        self.provider = provider
        self.model = model
        self.api_key = api_key
        self.api_key_env = api_key_env
        self.api_base = api_base
        self.timeout = timeout


@pytest.mark.asyncio
async def test_whisper_api_uses_profile_api_key():
    """The API key from the profile is sent in the Authorization header."""
    captured_headers: dict = {}

    mock_response = AsyncMock()
    mock_response.status_code = 200
    mock_response.text = "Hallo Welt"
    mock_response.raise_for_status = lambda: None

    async def fake_post(*args, **kwargs):
        captured_headers.update(kwargs.get("headers", {}))
        return mock_response

    mock_client = AsyncMock()
    mock_client.post = fake_post
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("httpx.AsyncClient", return_value=mock_client):
        svc = STTService()
        profile = _FakeProfile(api_key="sk-test-123")
        result = await svc._transcribe_whisper_api(b"fake-audio", "whisper-1", "de", profile=profile)

    assert result == "Hallo Welt"
    assert captured_headers.get("Authorization") == "Bearer sk-test-123"


@pytest.mark.asyncio
async def test_whisper_api_falls_back_to_env():
    """When profile.api_key is None, falls back to api_key_env."""
    import os

    os.environ["TEST_STT_KEY_ENV"] = "env-key-456"

    captured_headers: dict = {}

    mock_response = AsyncMock()
    mock_response.status_code = 200
    mock_response.text = "Env result"
    mock_response.raise_for_status = lambda: None

    async def fake_post(*args, **kwargs):
        captured_headers.update(kwargs.get("headers", {}))
        return mock_response

    mock_client = AsyncMock()
    mock_client.post = fake_post
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("httpx.AsyncClient", return_value=mock_client):
        svc = STTService()
        profile = _FakeProfile(api_key=None, api_key_env="TEST_STT_KEY_ENV")
        result = await svc._transcribe_whisper_api(b"fake-audio", "whisper-1", "de", profile=profile)

    assert result == "Env result"
    assert captured_headers.get("Authorization") == "Bearer env-key-456"

    del os.environ["TEST_STT_KEY_ENV"]


@pytest.mark.asyncio
async def test_whisper_api_no_key_raises():
    """RuntimeError when no API key is available."""
    import os

    os.environ.pop("OPENAI_API_KEY", None)

    svc = STTService()
    profile = _FakeProfile(api_key=None, api_key_env="NONEXISTENT_KEY")
    with pytest.raises(RuntimeError, match="No API key configured"):
        await svc._transcribe_whisper_api(b"fake-audio", "whisper-1", "de", profile=profile)


@pytest.mark.asyncio
async def test_whisper_api_custom_base_url():
    """Custom api_base is used instead of OpenAI default."""
    captured_url: str = ""

    mock_response = AsyncMock()
    mock_response.status_code = 200
    mock_response.text = "Custom OK"
    mock_response.raise_for_status = lambda: None

    async def fake_post(url, *args, **kwargs):
        nonlocal captured_url
        captured_url = url
        return mock_response

    mock_client = AsyncMock()
    mock_client.post = fake_post
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("httpx.AsyncClient", return_value=mock_client):
        svc = STTService()
        profile = _FakeProfile(api_base="https://custom-stt.example.com/v1/")
        result = await svc._transcribe_whisper_api(b"audio", "whisper-1", "en", profile=profile)

    assert result == "Custom OK"
    assert captured_url == "https://custom-stt.example.com/v1/audio/transcriptions"


@pytest.mark.asyncio
async def test_whisper_api_http_error():
    """HTTP errors from the API are wrapped in RuntimeError."""
    import httpx

    mock_response = AsyncMock()
    mock_response.status_code = 401
    mock_response.text = "Unauthorized"

    http_err = httpx.HTTPStatusError(
        "401 Unauthorized",
        request=AsyncMock(),
        response=mock_response,
    )

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=mock_response)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    def raise_side_effect():
        raise http_err

    mock_response.raise_for_status = raise_side_effect

    with patch("httpx.AsyncClient", return_value=mock_client):
        svc = STTService()
        profile = _FakeProfile(api_key="bad-key")
        with pytest.raises(RuntimeError, match="401"):
            await svc._transcribe_whisper_api(b"audio", "whisper-1", "de", profile=profile)
