"""Configurable settings for the Quercus (Canvas) client."""

from __future__ import annotations

import os
from dataclasses import dataclass

from uoft_timetable_mcp import __version__
from uoft_timetable_mcp.common.env import env_float, env_int

DEFAULT_BASE_URL = "https://q.utoronto.ca"
DEFAULT_API_PREFIX = "/api/v1"
DEFAULT_CONNECT_TIMEOUT_SECONDS = 5.0
DEFAULT_READ_TIMEOUT_SECONDS = 20.0
DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_MAX_PAGE_SIZE = 100
DEFAULT_ACCESS_TOKEN_ENV = "QUERCUS_ACCESS_TOKEN"


@dataclass(frozen=True, slots=True)
class QuercusSettings:
    """Runtime Quercus settings. Token values are never stored here."""

    base_url: str = DEFAULT_BASE_URL
    api_prefix: str = DEFAULT_API_PREFIX
    connect_timeout_seconds: float = DEFAULT_CONNECT_TIMEOUT_SECONDS
    read_timeout_seconds: float = DEFAULT_READ_TIMEOUT_SECONDS
    max_attempts: int = DEFAULT_MAX_ATTEMPTS
    max_page_size: int = DEFAULT_MAX_PAGE_SIZE
    access_token_env: str = DEFAULT_ACCESS_TOKEN_ENV
    version: str = __version__

    @property
    def api_root(self) -> str:
        """Effective Canvas API root, e.g. ``https://q.utoronto.ca/api/v1``."""
        if self.api_prefix.startswith("/"):
            prefix = self.api_prefix
        else:
            prefix = f"/{self.api_prefix}"
        return f"{self.base_url.rstrip('/')}{prefix}"

    @classmethod
    def from_env(cls) -> QuercusSettings:
        return cls(
            base_url=os.environ.get("QUERCUS_BASE_URL", DEFAULT_BASE_URL).rstrip("/"),
            api_prefix=DEFAULT_API_PREFIX,
            connect_timeout_seconds=env_float(
                "QUERCUS_CONNECT_TIMEOUT_SECONDS",
                DEFAULT_CONNECT_TIMEOUT_SECONDS,
            ),
            read_timeout_seconds=env_float(
                "QUERCUS_READ_TIMEOUT_SECONDS",
                DEFAULT_READ_TIMEOUT_SECONDS,
            ),
            max_attempts=env_int("QUERCUS_MAX_ATTEMPTS", DEFAULT_MAX_ATTEMPTS),
            max_page_size=env_int("QUERCUS_MAX_PAGE_SIZE", DEFAULT_MAX_PAGE_SIZE),
            access_token_env=os.environ.get(
                "QUERCUS_ACCESS_TOKEN_ENV",
                DEFAULT_ACCESS_TOKEN_ENV,
            ).strip()
            or DEFAULT_ACCESS_TOKEN_ENV,
            version=__version__,
        )
