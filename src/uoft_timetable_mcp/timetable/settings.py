"""Configurable settings for the Timetable Builder client and tools."""

from __future__ import annotations

import os
from dataclasses import dataclass

from uoft_timetable_mcp import __version__
from uoft_timetable_mcp.common.env import env_float, env_int

DEFAULT_BASE_URL = "https://api.easi.utoronto.ca/ttb"
DEFAULT_CONNECT_TIMEOUT_SECONDS = 5.0
DEFAULT_READ_TIMEOUT_SECONDS = 20.0
DEFAULT_REFERENCE_CACHE_TTL_SECONDS = 900
DEFAULT_MAX_PAGE_SIZE = 50
DEFAULT_MAX_CONCURRENCY = 5
DEFAULT_MAX_ATTEMPTS = 3


@dataclass(frozen=True, slots=True)
class Settings:
    """Runtime settings loaded from environment variables with safe defaults."""

    base_url: str = DEFAULT_BASE_URL
    connect_timeout_seconds: float = DEFAULT_CONNECT_TIMEOUT_SECONDS
    read_timeout_seconds: float = DEFAULT_READ_TIMEOUT_SECONDS
    reference_cache_ttl_seconds: int = DEFAULT_REFERENCE_CACHE_TTL_SECONDS
    max_page_size: int = DEFAULT_MAX_PAGE_SIZE
    max_concurrency: int = DEFAULT_MAX_CONCURRENCY
    max_attempts: int = DEFAULT_MAX_ATTEMPTS
    version: str = __version__

    @classmethod
    def from_env(cls) -> Settings:
        return cls(
            base_url=os.environ.get("TIMETABLE_BASE_URL", DEFAULT_BASE_URL).rstrip("/"),
            connect_timeout_seconds=env_float(
                "TIMETABLE_CONNECT_TIMEOUT_SECONDS",
                DEFAULT_CONNECT_TIMEOUT_SECONDS,
            ),
            read_timeout_seconds=env_float(
                "TIMETABLE_READ_TIMEOUT_SECONDS",
                DEFAULT_READ_TIMEOUT_SECONDS,
            ),
            reference_cache_ttl_seconds=env_int(
                "TIMETABLE_REFERENCE_CACHE_TTL_SECONDS",
                DEFAULT_REFERENCE_CACHE_TTL_SECONDS,
            ),
            max_page_size=env_int("TIMETABLE_MAX_PAGE_SIZE", DEFAULT_MAX_PAGE_SIZE),
            max_concurrency=env_int(
                "TIMETABLE_MAX_CONCURRENCY",
                DEFAULT_MAX_CONCURRENCY,
            ),
            version=__version__,
        )
