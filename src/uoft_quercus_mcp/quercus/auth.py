"""Access-token providers for Quercus (Canvas) API calls."""

from __future__ import annotations

import os
from typing import Protocol

from uoft_quercus_mcp.quercus.errors import QuercusAuthMissingError
from uoft_quercus_mcp.quercus.settings import QuercusSettings

_AUTH_MISSING_MESSAGE = (
    "Quercus personal access token is not configured. Set the environment "
    "variable named by Quercus settings (default QUERCUS_ACCESS_TOKEN) and "
    "restart the MCP server."
)


class AuthProvider(Protocol):
    """Thin auth seam so tools/client never read the token env var directly."""

    async def get_access_token(self) -> str: ...


class PersonalTokenAuth:
    """Personal access token from env/config. No refresh in Phase 1."""

    def __init__(self, token: str | None) -> None:
        if token is None:
            self._token: str | None = None
        else:
            stripped = token.strip()
            self._token = stripped or None

    async def get_access_token(self) -> str:
        if self._token is None:
            raise QuercusAuthMissingError(_AUTH_MISSING_MESSAGE)
        return self._token

    @classmethod
    def from_env(cls, settings: QuercusSettings) -> PersonalTokenAuth:
        """Build from env without raising — missing tokens fail at request time."""
        return cls(os.environ.get(settings.access_token_env))
