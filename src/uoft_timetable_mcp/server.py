"""FastMCP server instance and V1 data tools."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, NoReturn

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from pydantic import ValidationError

from uoft_timetable_mcp import __version__
from uoft_timetable_mcp.client import TimetableClient
from uoft_timetable_mcp.errors import (
    CourseNotFoundError,
    TimetableError,
    TimetableValidationError,
    to_mcp_error,
)
from uoft_timetable_mcp.models import (
    CourseDetailsInput,
    CourseDetailsResult,
    ReferenceData,
    SearchCoursesInput,
    SearchCoursesResult,
    model_to_public_dict,
)
from uoft_timetable_mcp.normalize import (
    classify_search_query,
    normalize_course,
    normalize_reference_data,
    normalize_search_course,
    session_matches,
    utc_now,
)
from uoft_timetable_mcp.settings import Settings

logger = logging.getLogger(__name__)

Clock = Callable[[], datetime]
AsyncFetcher = Callable[[], Awaitable[ReferenceData]]

_DATA_DISCLAIMER = (
    "Unofficial Timetable Builder data; values may change and this project is "
    "not affiliated with the University of Toronto."
)


class ReferenceDataCache:
    """In-memory TTL cache with single-flight loading for reference data."""

    def __init__(self, *, ttl_seconds: int, clock: Clock) -> None:
        self._ttl_seconds = ttl_seconds
        self._clock = clock
        self._lock = asyncio.Lock()
        self._data: ReferenceData | None = None
        self._expires_at: datetime | None = None
        self._inflight: asyncio.Future[ReferenceData] | None = None

    def clear(self) -> None:
        self._data = None
        self._expires_at = None
        self._inflight = None

    async def get_or_fetch(self, fetcher: AsyncFetcher) -> ReferenceData:
        async with self._lock:
            now = self._clock()
            if (
                self._data is not None
                and self._expires_at is not None
                and now < self._expires_at
            ):
                return self._data
            if self._inflight is not None:
                wait_for = self._inflight
                owner = False
            else:
                wait_for = asyncio.get_running_loop().create_future()
                self._inflight = wait_for
                owner = True

        if not owner:
            return await wait_for

        try:
            data = await fetcher()
        except Exception as exc:
            async with self._lock:
                # Failures are never cached.
                if not wait_for.done():
                    wait_for.set_exception(exc)
                self._inflight = None
            # Await so the future exception is retrieved (owner and waiters).
            return await wait_for

        async with self._lock:
            # TTL starts at fetch completion time.
            self._data = data
            self._expires_at = self._clock() + timedelta(seconds=self._ttl_seconds)
            if not wait_for.done():
                wait_for.set_result(data)
            self._inflight = None
        return data


@dataclass
class RuntimeState:
    client: TimetableClient
    settings: Settings
    clock: Clock
    reference_cache: ReferenceDataCache
    owns_client: bool = True


_state: RuntimeState | None = None


def configure_runtime(
    *,
    client: TimetableClient | None = None,
    settings: Settings | None = None,
    clock: Clock | None = None,
    owns_client: bool | None = None,
) -> RuntimeState:
    """Configure process-wide runtime dependencies (tests may call this)."""
    global _state
    resolved_settings = settings or Settings.from_env()
    resolved_clock = clock or utc_now
    resolved_client = client or TimetableClient(resolved_settings)
    resolved_owns = owns_client if owns_client is not None else client is None
    cache = ReferenceDataCache(
        ttl_seconds=resolved_settings.reference_cache_ttl_seconds,
        clock=resolved_clock,
    )
    _state = RuntimeState(
        client=resolved_client,
        settings=resolved_settings,
        clock=resolved_clock,
        reference_cache=cache,
        owns_client=resolved_owns,
    )
    return _state


def reset_runtime() -> None:
    """Clear runtime state without closing clients (tests own cleanup)."""
    global _state
    _state = None


def get_state() -> RuntimeState:
    if _state is None:
        return configure_runtime()
    return _state


def raise_tool_error(exc: TimetableError) -> NoReturn:
    """Raise a FastMCP ToolError carrying the stable MCP error payload."""
    raise ToolError(json.dumps(to_mcp_error(exc))) from exc


@asynccontextmanager
async def _lifespan(_server: FastMCP) -> AsyncIterator[dict[str, Any]]:
    global _state
    created_here = _state is None
    if created_here:
        configure_runtime()
    try:
        yield {}
    finally:
        if created_here and _state is not None:
            if _state.owns_client:
                await _state.client.aclose()
            _state = None


mcp = FastMCP(
    name="uoft-timetable-mcp",
    version=__version__,
    instructions=(
        "Unofficial University of Toronto Timetable Builder data tools. "
        "Discover sessions and filters, search courses, fetch section details, "
        "and deterministically check schedule conflicts. Data may change; "
        "this project is not affiliated with the University of Toronto."
    ),
    lifespan=_lifespan,
    mask_error_details=True,
)


@mcp.tool(
    description=(
        "Return currently valid Timetable Builder sessions, divisions, campuses, "
        "delivery modes, and course levels. Call this before searching if you do "
        "not already know valid filter values. Sessions are not hard-coded and "
        f"change over time. {_DATA_DISCLAIMER}"
    )
)
async def get_reference_data() -> dict[str, Any]:
    state = get_state()

    async def _fetch() -> ReferenceData:
        payload = await state.client.get_reference_data()
        return normalize_reference_data(payload, fetched_at=state.clock())

    try:
        data = await state.reference_cache.get_or_fetch(_fetch)
    except TimetableError as exc:
        raise_tool_error(exc)
    return model_to_public_dict(data)


@mcp.tool(
    description=(
        "Search courses by code or title with required session and division "
        "filters. Returns concise summaries and pagination metadata; use "
        "get_course_details for full section/meeting data. "
        "Requires at least one session and one division from get_reference_data. "
        "page is one-based; page_size must be between 1 and 50. "
        f"{_DATA_DISCLAIMER}"
    )
)
async def search_courses(
    sessions: list[str],
    divisions: list[str],
    query: str = "",
    campuses: list[str] | None = None,
    instructor: str | None = None,
    course_levels: list[str] | None = None,
    delivery_modes: list[str] | None = None,
    available_space_only: bool = False,
    waitlistable_only: bool = False,
    search_description: bool = False,
    page: int = 1,
    page_size: int = 10,
) -> dict[str, Any]:
    try:
        params = SearchCoursesInput(
            query=query,
            sessions=sessions,
            divisions=divisions,
            campuses=campuses or [],
            instructor=instructor,
            course_levels=course_levels or [],
            delivery_modes=delivery_modes or [],
            available_space_only=available_space_only,
            waitlistable_only=waitlistable_only,
            search_description=search_description,
            page=page,
            page_size=page_size,
        )
    except ValidationError as exc:
        message = "; ".join(error.get("msg", "Invalid input") for error in exc.errors())
        raise_tool_error(TimetableValidationError(message))

    course_code, course_title, is_code = classify_search_query(params.query)
    state = get_state()
    try:
        pageable = await state.client.search_courses(
            sessions=params.sessions,
            divisions=params.divisions,
            course_code=course_code,
            course_title=course_title,
            search_course_description=(
                params.search_description if not is_code else False
            ),
            campuses=params.campuses,
            instructor=params.instructor or "",
            course_levels=params.course_levels,
            delivery_modes=params.delivery_modes,
            available_space=params.available_space_only,
            wait_listable=params.waitlistable_only,
            page=params.page,
            page_size=params.page_size,
        )
    except TimetableError as exc:
        raise_tool_error(exc)

    fetched_at = state.clock()
    courses = [
        normalize_search_course(raw)
        for raw in pageable["courses"]
        if isinstance(raw, dict)
    ]
    total = pageable["total"]
    result = SearchCoursesResult(
        courses=courses,
        page=params.page,
        page_size=params.page_size,
        total=total,
        has_next_page=params.page * params.page_size < total,
        fetched_at=fetched_at,
    )
    return model_to_public_dict(result)


@mcp.tool(
    description=(
        "Fetch normalized course and section details for a course code in a "
        "required session. Optionally filter by section_code term half "
        "(F, S, or Y) — not a LEC/TUT component name. Returns all matching "
        "upstream records for that course/session. Raises course_not_found "
        f"when nothing matches. {_DATA_DISCLAIMER}"
    )
)
async def get_course_details(
    course_code: str,
    session: str,
    section_code: str | None = None,
) -> dict[str, Any]:
    try:
        params = CourseDetailsInput(
            course_code=course_code,
            session=session,
            section_code=section_code,
        )
    except ValidationError as exc:
        message = "; ".join(error.get("msg", "Invalid input") for error in exc.errors())
        raise_tool_error(TimetableValidationError(message))

    state = get_state()
    try:
        raw_courses = await state.client.get_courses_by_code(params.course_code)
    except TimetableError as exc:
        raise_tool_error(exc)

    fetched_at = state.clock()
    matched = []
    for raw in raw_courses:
        sessions = raw.get("sessions")
        if not session_matches(params.session, sessions):
            continue
        if params.section_code is not None:
            raw_section_code = raw.get("sectionCode")
            if not (
                isinstance(raw_section_code, str)
                and raw_section_code.upper() == params.section_code
            ):
                continue
        matched.append(normalize_course(raw, fetched_at=fetched_at))

    if not matched:
        raise_tool_error(
            CourseNotFoundError(
                f"No course records matched {params.course_code} in session "
                f"{params.session}."
            )
        )

    result = CourseDetailsResult(courses=matched, found=True, fetched_at=fetched_at)
    return model_to_public_dict(result)
