"""Async HTTP client for the Quercus (Canvas) API."""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Mapping, Sequence
from typing import Any
from urllib.parse import urlparse, urlunparse

import httpx

from uoft_timetable_mcp.common.http_retry import (
    RETRYABLE_STATUS_CODES,
    Sleeper,
    backoff_seconds,
    default_sleeper,
    retry_delay_seconds,
)
from uoft_timetable_mcp.quercus.auth import AuthProvider, PersonalTokenAuth
from uoft_timetable_mcp.quercus.errors import (
    QuercusAuthRejectedError,
    QuercusForbiddenError,
    QuercusNetworkError,
    QuercusRateLimitError,
    QuercusTimeoutError,
    QuercusUpstreamError,
)
from uoft_timetable_mcp.quercus.settings import QuercusSettings

logger = logging.getLogger(__name__)

_MAX_PAGINATION_PAGES = 50
_LINK_PART_RE = re.compile(r'<([^>]+)>\s*;\s*rel="([^"]+)"', re.IGNORECASE)


def parse_link_header(value: str | None) -> dict[str, str]:
    """Parse a Canvas/RFC 5988 ``Link`` header into ``rel -> url`` mapping."""
    if not value:
        return {}
    links: dict[str, str] = {}
    for match in _LINK_PART_RE.finditer(value):
        url, rel = match.group(1).strip(), match.group(2).strip().lower()
        if url and rel:
            links[rel] = url
    return links


class QuercusClient:
    """Reusable async client for Quercus Canvas endpoints.

    Construct once and reuse. Call ``aclose()`` (or use as an async context
    manager) to close the underlying ``httpx.AsyncClient`` when owned.
    """

    def __init__(
        self,
        settings: QuercusSettings | None = None,
        *,
        auth: AuthProvider | None = None,
        http_client: httpx.AsyncClient | None = None,
        sleeper: Sleeper | None = None,
    ) -> None:
        self.settings = settings or QuercusSettings.from_env()
        self._auth: AuthProvider = auth or PersonalTokenAuth.from_env(self.settings)
        self._sleeper: Sleeper = sleeper or default_sleeper
        self._owns_client = http_client is None
        timeout = httpx.Timeout(
            connect=self.settings.connect_timeout_seconds,
            read=self.settings.read_timeout_seconds,
            write=self.settings.read_timeout_seconds,
            pool=self.settings.connect_timeout_seconds,
        )
        self._client = http_client or httpx.AsyncClient(
            base_url=self.settings.api_root,
            timeout=timeout,
            headers=self._default_headers(),
        )

    def _default_headers(self) -> dict[str, str]:
        return {
            "Accept": "application/json",
            "User-Agent": f"uoft-timetable-mcp/{self.settings.version}",
        }

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def __aenter__(self) -> QuercusClient:
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.aclose()

    async def get_self(self) -> dict[str, Any]:
        """GET ``/users/self`` and return the user object."""
        data = await self._request_json("GET", "/users/self")
        if not isinstance(data, dict):
            raise QuercusUpstreamError(
                "Quercus returned an unexpected user payload.",
                retryable=False,
            )
        return data

    async def list_courses(
        self,
        *,
        enrollment_state: str | None = "active",
        include: Sequence[str] | None = None,
    ) -> list[dict[str, Any]]:
        """GET ``/courses`` with Canvas pagination."""
        params: dict[str, Any] = {}
        if enrollment_state is not None:
            params["enrollment_state"] = enrollment_state
        include_values = list(include) if include is not None else ["term"]
        if include_values:
            params["include[]"] = include_values
        items = await self.get_paginated("/courses", params=params)
        return [item for item in items if isinstance(item, dict)]

    async def get_paginated(
        self,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
    ) -> list[Any]:
        """Fetch all pages for a Canvas list endpoint via ``Link`` headers."""
        request_params = dict(params or {})
        per_page = request_params.get("per_page")
        if not isinstance(per_page, int) or isinstance(per_page, bool) or per_page < 1:
            request_params["per_page"] = self.settings.max_page_size
        else:
            request_params["per_page"] = min(per_page, self.settings.max_page_size)

        collected: list[Any] = []
        next_url: str | None = None
        page_count = 0

        while True:
            page_count += 1
            if page_count > _MAX_PAGINATION_PAGES:
                raise QuercusUpstreamError(
                    "Quercus pagination exceeded the safety page limit.",
                    retryable=False,
                )

            if next_url is None:
                response = await self._request("GET", path, params=request_params)
            else:
                response = await self._request("GET", next_url)

            try:
                data = response.json()
            except ValueError as exc:
                raise QuercusUpstreamError(
                    "Quercus returned a non-JSON response.",
                    retryable=False,
                ) from exc

            if not isinstance(data, list):
                raise QuercusUpstreamError(
                    "Quercus returned an unexpected list payload.",
                    retryable=False,
                )
            collected.extend(data)

            links = parse_link_header(response.headers.get("Link"))
            next_url = links.get("next")
            if not next_url:
                break

        return collected

    async def _request_json(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
    ) -> Any:
        response = await self._request(method, path, params=params)
        try:
            return response.json()
        except ValueError as exc:
            raise QuercusUpstreamError(
                "Quercus returned a non-JSON response.",
                retryable=False,
            ) from exc

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
    ) -> httpx.Response:
        attempt = 0
        last_error: Exception | None = None
        token = await self._auth.get_access_token()
        auth_headers = {"Authorization": f"Bearer {token}"}

        while attempt < self.settings.max_attempts:
            attempt += 1
            started = time.perf_counter()
            try:
                response = await self._client.request(
                    method,
                    path,
                    params=dict(params) if params else None,
                    headers=auth_headers,
                )
            except httpx.TimeoutException as exc:
                last_error = QuercusTimeoutError(
                    "Timed out while contacting Quercus.",
                )
                logger.info(
                    "quercus_request method=%s path=%s attempt=%s error=timeout",
                    method,
                    self._log_path(path),
                    attempt,
                )
                if attempt >= self.settings.max_attempts:
                    raise last_error from exc
                await self._sleeper(backoff_seconds(attempt))
                continue
            except httpx.TransportError as exc:
                last_error = QuercusNetworkError(
                    "Network error while contacting Quercus.",
                )
                logger.info(
                    "quercus_request method=%s path=%s attempt=%s error=network",
                    method,
                    self._log_path(path),
                    attempt,
                )
                if attempt >= self.settings.max_attempts:
                    raise last_error from exc
                await self._sleeper(backoff_seconds(attempt))
                continue

            duration_ms = int((time.perf_counter() - started) * 1000)
            status_code = response.status_code

            if status_code in RETRYABLE_STATUS_CODES:
                logger.info(
                    "quercus_request method=%s path=%s status=%s duration_ms=%s "
                    "attempt=%s retryable=true",
                    method,
                    self._log_path(path),
                    status_code,
                    duration_ms,
                    attempt,
                )
                if attempt >= self.settings.max_attempts:
                    if status_code == 429:
                        raise QuercusRateLimitError(
                            "Quercus rate-limited the request.",
                        )
                    raise QuercusUpstreamError(
                        "Quercus is temporarily unavailable.",
                        retryable=True,
                    )
                await self._sleeper(retry_delay_seconds(response, attempt))
                continue

            if status_code == 401:
                logger.info(
                    "quercus_request method=%s path=%s status=%s duration_ms=%s "
                    "attempt=%s retryable=false",
                    method,
                    self._log_path(path),
                    status_code,
                    duration_ms,
                    attempt,
                )
                raise QuercusAuthRejectedError(
                    "Quercus rejected the personal access token "
                    "(expired, revoked, or invalid). Create a new token in "
                    "Quercus Account Settings and update QUERCUS_ACCESS_TOKEN.",
                )

            if status_code == 403:
                logger.info(
                    "quercus_request method=%s path=%s status=%s duration_ms=%s "
                    "attempt=%s retryable=false",
                    method,
                    self._log_path(path),
                    status_code,
                    duration_ms,
                    attempt,
                )
                raise QuercusForbiddenError(
                    "Quercus denied access to this resource.",
                )

            if status_code >= 400:
                logger.info(
                    "quercus_request method=%s path=%s status=%s duration_ms=%s "
                    "attempt=%s retryable=false",
                    method,
                    self._log_path(path),
                    status_code,
                    duration_ms,
                    attempt,
                )
                raise QuercusUpstreamError(
                    f"Quercus returned HTTP {status_code}.",
                    retryable=False,
                )

            logger.info(
                "quercus_request method=%s path=%s status=%s duration_ms=%s "
                "attempt=%s ok=true",
                method,
                self._log_path(path),
                status_code,
                duration_ms,
                attempt,
            )
            return response

        assert last_error is not None
        raise last_error

    @staticmethod
    def _log_path(path: str) -> str:
        """Return a log-safe path/url without query credentials."""
        parsed = urlparse(path)
        if not parsed.scheme:
            return path.split("?", 1)[0]
        # Drop query/fragment from absolute next-page URLs in logs.
        return urlunparse((parsed.scheme, parsed.netloc, parsed.path, "", "", ""))
