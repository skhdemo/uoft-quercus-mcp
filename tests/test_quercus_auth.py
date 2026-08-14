"""Unit tests for Quercus personal-token auth."""

from __future__ import annotations

import pytest

from uoft_quercus_mcp.quercus.auth import PersonalTokenAuth
from uoft_quercus_mcp.quercus.errors import QuercusAuthMissingError
from uoft_quercus_mcp.quercus.settings import QuercusSettings


@pytest.fixture
def settings() -> QuercusSettings:
    return QuercusSettings(access_token_env="QUERCUS_ACCESS_TOKEN")


@pytest.mark.asyncio
async def test_get_access_token_success() -> None:
    auth = PersonalTokenAuth("secret-token-value")
    assert await auth.get_access_token() == "secret-token-value"


@pytest.mark.asyncio
async def test_from_env_success(
    monkeypatch: pytest.MonkeyPatch, settings: QuercusSettings
) -> None:
    monkeypatch.setenv("QUERCUS_ACCESS_TOKEN", "env-token-abc")
    auth = PersonalTokenAuth.from_env(settings)
    assert await auth.get_access_token() == "env-token-abc"


@pytest.mark.asyncio
async def test_missing_env_raises_on_get_access_token(
    monkeypatch: pytest.MonkeyPatch,
    settings: QuercusSettings,
) -> None:
    monkeypatch.delenv("QUERCUS_ACCESS_TOKEN", raising=False)
    auth = PersonalTokenAuth.from_env(settings)
    with pytest.raises(QuercusAuthMissingError) as exc_info:
        await auth.get_access_token()
    assert exc_info.value.code == "quercus_auth_missing"
    assert exc_info.value.retryable is False


@pytest.mark.asyncio
async def test_whitespace_only_token_is_missing() -> None:
    auth = PersonalTokenAuth("   \t  ")
    with pytest.raises(QuercusAuthMissingError):
        await auth.get_access_token()


@pytest.mark.asyncio
async def test_token_is_stripped() -> None:
    auth = PersonalTokenAuth("  padded-token  ")
    assert await auth.get_access_token() == "padded-token"


@pytest.mark.asyncio
async def test_error_message_does_not_contain_token() -> None:
    secret = "super-secret-token-xyz"
    auth = PersonalTokenAuth(None)
    with pytest.raises(QuercusAuthMissingError) as exc_info:
        await auth.get_access_token()
    assert secret not in exc_info.value.message
    assert secret not in str(exc_info.value)
