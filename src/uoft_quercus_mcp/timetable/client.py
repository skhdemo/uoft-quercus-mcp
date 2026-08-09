"""Async HTTP client for the public Timetable Builder API."""

from __future__ import annotations

import logging
import time
from collections.abc import Mapping, Sequence
from typing import Any
from urllib.parse import quote

import httpx

from uoft_quercus_mcp.common.http_retry import (
    RETRYABLE_STATUS_CODES,
    Sleeper,
    backoff_seconds,
    default_sleeper,
    retry_delay_seconds,
)
from uoft_quercus_mcp.timetable.errors import (
    TimetableNetworkError,
    TimetableRateLimitError,
    TimetableTimeoutError,
    TimetableUpstreamError,
    TimetableValidationError,
)
from uoft_quercus_mcp.timetable.settings import Settings

logger = logging.getLogger(__name__)


class TimetableClient:
    """Reusable async client for Timetable Builder endpoints.

    Construct once and reuse. Call ``aclose()`` (or use as an async context
    manager) to close the underlying ``httpx.AsyncClient``.
    """

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        http_client: httpx.AsyncClient | None = None,
        sleeper: Sleeper | None = None,
    ) -> None:
        self.settings = settings or Settings.from_env()
        self._sleeper: Sleeper = sleeper or default_sleeper
        self._owns_client = http_client is None
        timeout = httpx.Timeout(
            connect=self.settings.connect_timeout_seconds,
            read=self.settings.read_timeout_seconds,
            write=self.settings.read_timeout_seconds,
            pool=self.settings.connect_timeout_seconds,
        )
        self._client = http_client or httpx.AsyncClient(
            base_url=self.settings.base_url,
            timeout=timeout,
            headers=self._default_headers(),
        )

    def _default_headers(self) -> dict[str, str]:
        return {
            "Accept": "application/json",
            "Origin": "https://ttb.utoronto.ca",
            "Referer": "https://ttb.utoronto.ca/",
            "User-Agent": f"uoft-quercus-mcp/{self.settings.version}",
        }

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def __aenter__(self) -> TimetableClient:
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.aclose()

    async def get_reference_data(self) -> dict[str, Any]:
        """GET ``/reference-data`` and return the validated payload object."""
        data = await self._request_json("GET", "/reference-data")
        payload = self._require_payload_dict(data)
        return payload

    async def search_courses(
        self,
        *,
        sessions: Sequence[str],
        divisions: Sequence[str],
        course_code: str = "",
        course_title: str = "",
        search_course_description: bool = False,
        campuses: Sequence[str] | None = None,
        instructor: str = "",
        course_levels: Sequence[str] | None = None,
        delivery_modes: Sequence[str] | None = None,
        available_space: bool = False,
        wait_listable: bool = False,
        page: int = 1,
        page_size: int = 10,
    ) -> dict[str, Any]:
        """POST ``/getPageableCourses`` and return ``pageableCourse``."""
        if page < 1:
            raise TimetableValidationError("page must be >= 1")
        bounded_page_size = min(max(page_size, 1), self.settings.max_page_size)
        body = {
            "courseCodeAndTitleProps": {
                "courseCode": course_code,
                "courseTitle": course_title,
                "courseSectionCode": "",
                "searchCourseDescription": search_course_description,
            },
            "departmentProps": [],
            "campuses": list(campuses or []),
            "sessions": list(sessions),
            "requirementProps": [],
            "instructor": instructor or "",
            "courseLevels": list(course_levels or []),
            "deliveryModes": list(delivery_modes or []),
            "dayPreferences": [],
            "timePreferences": [],
            "divisions": list(divisions),
            "creditWeights": [],
            "availableSpace": available_space,
            "waitListable": wait_listable,
            "page": page,
            "pageSize": bounded_page_size,
            "direction": "asc",
        }
        data = await self._request_json("POST", "/getPageableCourses", json_body=body)
        payload = self._require_payload_dict(data)
        pageable = payload.get("pageableCourse")
        if not isinstance(pageable, dict):
            raise TimetableUpstreamError(
                "Timetable Builder search response was missing pageableCourse.",
                retryable=False,
            )
        if "courses" not in pageable or "total" not in pageable:
            raise TimetableUpstreamError(
                "Timetable Builder search response was missing courses or total.",
                retryable=False,
            )
        if not isinstance(pageable.get("courses"), list):
            raise TimetableUpstreamError(
                "Timetable Builder search response had an incompatible courses value.",
                retryable=False,
            )
        if not isinstance(pageable.get("total"), int) or isinstance(
            pageable.get("total"), bool
        ):
            raise TimetableUpstreamError(
                "Timetable Builder search response had an incompatible total value.",
                retryable=False,
            )
        return pageable

    async def get_courses_by_code(self, course_code: str) -> list[dict[str, Any]]:
        """GET code lookup and return the list of upstream course records.

        Prefer the code-only form. Local session / term-half filtering belongs
        in the tool layer, not here.
        """
        encoded = quote(course_code, safe="")
        path = f"/getCoursesByCodeAndSectionCode/{encoded}"
        data = await self._request_json("GET", path)
        payload = self._require_payload_dict(data)
        pageable = payload.get("pageableCourse")
        if not isinstance(pageable, dict):
            raise TimetableUpstreamError(
                "Timetable Builder course lookup response was missing pageableCourse.",
                retryable=False,
            )
        courses = pageable.get("courses")
        if not isinstance(courses, list):
            raise TimetableUpstreamError(
                "Timetable Builder course lookup response had incompatible courses.",
                retryable=False,
            )
        return [course for course in courses if isinstance(course, dict)]

    async def _request_json(
        self,
        method: str,
        path: str,
        *,
        json_body: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        attempt = 0
        last_error: Exception | None = None

        while attempt < self.settings.max_attempts:
            attempt += 1
            started = time.perf_counter()
            try:
                headers: dict[str, str] = {}
                if json_body is not None:
                    headers["Content-Type"] = "application/json"
                response = await self._client.request(
                    method,
                    path,
                    json=json_body,
                    headers=headers or None,
                )
            except httpx.TimeoutException as exc:
                last_error = TimetableTimeoutError(
                    "Timed out while contacting Timetable Builder.",
                )
                logger.info(
                    "timetable_request method=%s path=%s attempt=%s error=timeout",
                    method,
                    path,
                    attempt,
                )
                if attempt >= self.settings.max_attempts:
                    raise last_error from exc
                await self._sleeper(backoff_seconds(attempt))
                continue
            except httpx.TransportError as exc:
                last_error = TimetableNetworkError(
                    "Network error while contacting Timetable Builder.",
                )
                logger.info(
                    "timetable_request method=%s path=%s attempt=%s error=network",
                    method,
                    path,
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
                    "timetable_request method=%s path=%s status=%s duration_ms=%s "
                    "attempt=%s retryable=true",
                    method,
                    path,
                    status_code,
                    duration_ms,
                    attempt,
                )
                if attempt >= self.settings.max_attempts:
                    if status_code == 429:
                        raise TimetableRateLimitError(
                            "Timetable Builder rate-limited the request.",
                        )
                    raise TimetableUpstreamError(
                        "Timetable Builder is temporarily unavailable.",
                        retryable=True,
                    )
                await self._sleeper(retry_delay_seconds(response, attempt))
                continue

            if status_code >= 400:
                logger.info(
                    "timetable_request method=%s path=%s status=%s duration_ms=%s "
                    "attempt=%s retryable=false",
                    method,
                    path,
                    status_code,
                    duration_ms,
                    attempt,
                )
                raise TimetableUpstreamError(
                    f"Timetable Builder returned HTTP {status_code}.",
                    retryable=False,
                )

            try:
                data = response.json()
            except ValueError as exc:
                raise TimetableUpstreamError(
                    "Timetable Builder returned a non-JSON response.",
                    retryable=False,
                ) from exc

            if not isinstance(data, dict):
                raise TimetableUpstreamError(
                    "Timetable Builder returned an unexpected JSON shape.",
                    retryable=False,
                )

            self._raise_for_application_status(data)
            logger.info(
                "timetable_request method=%s path=%s status=%s duration_ms=%s "
                "attempt=%s ok=true",
                method,
                path,
                status_code,
                duration_ms,
                attempt,
            )
            return data

        assert last_error is not None
        raise last_error

    def _require_payload_dict(self, data: dict[str, Any]) -> dict[str, Any]:
        payload = data.get("payload")
        if payload is None:
            raise TimetableUpstreamError(
                "Timetable Builder did not return course data. Try again shortly.",
                retryable=True,
            )
        if not isinstance(payload, dict):
            raise TimetableUpstreamError(
                "Timetable Builder returned an incompatible payload.",
                retryable=False,
            )
        return payload

    def _raise_for_application_status(self, data: dict[str, Any]) -> None:
        status = data.get("status")
        if status is None:
            return
        if not isinstance(status, list):
            raise TimetableUpstreamError(
                "Timetable Builder returned an incompatible status field.",
                retryable=False,
            )
        errors = [item for item in status if self._status_item_is_error(item)]
        if not errors:
            return
        message = self._format_status_errors(errors)
        raise TimetableUpstreamError(message, retryable=False)

    @staticmethod
    def _status_item_is_error(item: Any) -> bool:
        if not isinstance(item, dict):
            return True
        code = item.get("code")
        if isinstance(code, str) and "ERROR" in code.upper():
            return True
        level = item.get("level") or item.get("severity") or item.get("type")
        if isinstance(level, str) and level.upper() in {"ERROR", "FATAL", "FAILURE"}:
            return True
        # Non-empty status objects without an explicit success marker are treated
        # as application errors so malformed envelopes are never silent.
        if code is None and item.get("message"):
            return True
        return False

    @staticmethod
    def _format_status_errors(errors: list[Any]) -> str:
        messages: list[str] = []
        for item in errors:
            if isinstance(item, dict):
                message = item.get("message")
                if isinstance(message, str) and message.strip():
                    messages.append(message.strip())
        if messages:
            return messages[0]
        return "Timetable Builder reported an application error."
