"""Configurable settings for the Quercus (Canvas) client."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from uoft_timetable_mcp import __version__
from uoft_timetable_mcp.common.env import env_float, env_int

DEFAULT_BASE_URL = "https://q.utoronto.ca"
DEFAULT_API_PREFIX = "/api/v1"
DEFAULT_CONNECT_TIMEOUT_SECONDS = 5.0
DEFAULT_READ_TIMEOUT_SECONDS = 20.0
DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_MAX_PAGE_SIZE = 100
DEFAULT_MAX_CONCURRENCY = 5
DEFAULT_ACCESS_TOKEN_ENV = "QUERCUS_ACCESS_TOKEN"
DEFAULT_DOWNLOAD_DIR = "~/.cache/uoft-timetable-mcp/quercus-files"
DEFAULT_COURSE_CACHE_TTL_SECONDS = 120
DEFAULT_MAX_DOWNLOAD_BYTES = 25 * 1024 * 1024
DEFAULT_MAX_TEXT_CHARS = 200_000


@dataclass(frozen=True, slots=True)
class QuercusSettings:
    """Runtime Quercus settings. Token values are never stored here."""

    base_url: str = DEFAULT_BASE_URL
    api_prefix: str = DEFAULT_API_PREFIX
    connect_timeout_seconds: float = DEFAULT_CONNECT_TIMEOUT_SECONDS
    read_timeout_seconds: float = DEFAULT_READ_TIMEOUT_SECONDS
    max_attempts: int = DEFAULT_MAX_ATTEMPTS
    max_page_size: int = DEFAULT_MAX_PAGE_SIZE
    max_concurrency: int = DEFAULT_MAX_CONCURRENCY
    access_token_env: str = DEFAULT_ACCESS_TOKEN_ENV
    download_dir: str = DEFAULT_DOWNLOAD_DIR
    course_cache_ttl_seconds: int = DEFAULT_COURSE_CACHE_TTL_SECONDS
    max_download_bytes: int = DEFAULT_MAX_DOWNLOAD_BYTES
    max_text_chars: int = DEFAULT_MAX_TEXT_CHARS
    version: str = __version__

    @property
    def api_root(self) -> str:
        """Effective Canvas API root, e.g. ``https://q.utoronto.ca/api/v1``."""
        if self.api_prefix.startswith("/"):
            prefix = self.api_prefix
        else:
            prefix = f"/{self.api_prefix}"
        return f"{self.base_url.rstrip('/')}{prefix}"

    @property
    def resolved_download_dir(self) -> Path:
        return Path(self.download_dir).expanduser()

    @classmethod
    def from_env(cls) -> QuercusSettings:
        download_dir = os.environ.get("QUERCUS_DOWNLOAD_DIR", DEFAULT_DOWNLOAD_DIR)
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
            max_concurrency=env_int(
                "QUERCUS_MAX_CONCURRENCY",
                DEFAULT_MAX_CONCURRENCY,
            ),
            access_token_env=os.environ.get(
                "QUERCUS_ACCESS_TOKEN_ENV",
                DEFAULT_ACCESS_TOKEN_ENV,
            ).strip()
            or DEFAULT_ACCESS_TOKEN_ENV,
            download_dir=download_dir.strip() or DEFAULT_DOWNLOAD_DIR,
            course_cache_ttl_seconds=env_int(
                "QUERCUS_COURSE_CACHE_TTL_SECONDS",
                DEFAULT_COURSE_CACHE_TTL_SECONDS,
            ),
            max_download_bytes=env_int(
                "QUERCUS_MAX_DOWNLOAD_BYTES",
                DEFAULT_MAX_DOWNLOAD_BYTES,
            ),
            max_text_chars=env_int(
                "QUERCUS_MAX_TEXT_CHARS",
                DEFAULT_MAX_TEXT_CHARS,
            ),
            version=__version__,
        )
