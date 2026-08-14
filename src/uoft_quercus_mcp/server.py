"""FastMCP server instance for Quercus and Timetable Builder tools."""

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

from uoft_quercus_mcp import __version__
from uoft_quercus_mcp.common.errors import DomainError, to_mcp_error
from uoft_quercus_mcp.common.serialize import model_to_public_dict
from uoft_quercus_mcp.quercus.auth import AuthProvider, PersonalTokenAuth
from uoft_quercus_mcp.quercus.client import QuercusClient
from uoft_quercus_mcp.quercus.resolve import CourseListCache
from uoft_quercus_mcp.quercus.settings import QuercusSettings
from uoft_quercus_mcp.quercus.tools import register_quercus_tools
from uoft_quercus_mcp.timetable.client import TimetableClient
from uoft_quercus_mcp.timetable.conflicts import ResolvedSection, analyze_conflicts
from uoft_quercus_mcp.timetable.course_codes import (
    expand_short_course_code,
    is_short_course_code,
    short_code_not_found_message,
)
from uoft_quercus_mcp.timetable.errors import (
    CourseNotFoundError,
    TimetableError,
    TimetableUpstreamError,
    TimetableValidationError,
)
from uoft_quercus_mcp.timetable.models import (
    CheckConflictsInput,
    CheckConflictsResult,
    Course,
    CourseDetailsInput,
    CourseDetailsResult,
    ReferenceData,
    SearchCoursesInput,
    SearchCoursesResult,
    Section,
    SectionSelection,
    UnresolvedSelection,
)
from uoft_quercus_mcp.timetable.normalize import (
    classify_search_query,
    normalize_course,
    normalize_reference_data,
    normalize_search_course,
    session_matches,
    utc_now,
)
from uoft_quercus_mcp.timetable.settings import Settings

_CHECK_CONFLICTS_DESCRIPTION = (
    "Deterministically check whether selected course sections overlap in time "
    "using Timetable Builder (TTB) meeting times — do not rely on your own time "
    "arithmetic. Before telling a student that a proposed schedule is verified, "
    "call this tool with every selected section. Only claim the schedule is "
    "verified when `has_conflicts` is false, `transition_violations` is empty, "
    "and `is_complete` is true. If `is_complete` is false, say the schedule "
    "could not be fully verified. Prefer full parent course codes when "
    "selecting sections (e.g. CSCA08H3, not CSCA08). "
    "Unofficial Timetable Builder data; values may change and this project is "
    "not affiliated with the University of Toronto."
)

_COURSE_CODE_GUIDANCE = (
    "Prefer full UofT course codes when known (e.g. CSC108H1, CSCA08H3). "
    "Students often omit the campus suffix (CSCA08 vs CSCA08H3). Commonly, the "
    "final H/Y is course weight and the final digit is campus — usually 1 St. "
    "George, 3 UTSC, 5 UTM (so H1/Y1, H3/Y3, H5/Y5). Choose the matching "
    "division from ttb_get_reference_data (SCAR for UTSC, ERIN for UTM, ARTSC "
    "for Arts & Science St. George, etc.). If a short code fails, retry with "
    "the full code and correct division rather than guessing randomly."
)

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
    quercus_settings: QuercusSettings
    quercus_auth: AuthProvider
    quercus_client: QuercusClient
    quercus_course_cache: CourseListCache
    owns_client: bool = True
    owns_quercus_client: bool = True


_state: RuntimeState | None = None


def configure_runtime(
    *,
    client: TimetableClient | None = None,
    settings: Settings | None = None,
    clock: Clock | None = None,
    owns_client: bool | None = None,
    quercus_client: QuercusClient | None = None,
    quercus_settings: QuercusSettings | None = None,
    quercus_auth: AuthProvider | None = None,
    owns_quercus_client: bool | None = None,
    quercus_course_cache: CourseListCache | None = None,
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
    resolved_quercus_settings = quercus_settings or QuercusSettings.from_env()
    resolved_quercus_auth: AuthProvider = quercus_auth or PersonalTokenAuth.from_env(
        resolved_quercus_settings
    )
    if quercus_client is not None:
        resolved_quercus_client = quercus_client
        resolved_owns_quercus = (
            owns_quercus_client if owns_quercus_client is not None else False
        )
    else:
        resolved_quercus_client = QuercusClient(
            resolved_quercus_settings,
            auth=resolved_quercus_auth,
        )
        resolved_owns_quercus = (
            owns_quercus_client if owns_quercus_client is not None else True
        )
    resolved_course_cache = quercus_course_cache or CourseListCache(
        ttl_seconds=resolved_quercus_settings.course_cache_ttl_seconds,
        clock=resolved_clock,
    )
    _state = RuntimeState(
        client=resolved_client,
        settings=resolved_settings,
        clock=resolved_clock,
        reference_cache=cache,
        owns_client=resolved_owns,
        quercus_settings=resolved_quercus_settings,
        quercus_auth=resolved_quercus_auth,
        quercus_client=resolved_quercus_client,
        quercus_course_cache=resolved_course_cache,
        owns_quercus_client=resolved_owns_quercus,
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


def raise_tool_error(exc: DomainError) -> NoReturn:
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
            if _state.owns_quercus_client:
                await _state.quercus_client.aclose()
            _state = None


mcp = FastMCP(
    name="uoft-quercus-mcp",
    version=__version__,
    instructions=(
        "Unofficial University of Toronto Quercus (Canvas) MCP tools for students. "
        "With QUERCUS_ACCESS_TOKEN, list courses, todo/upcoming, assignments, "
        "announcements, modules, course files, and file text/download "
        "(e.g. past quiz PDFs via quercus_get_file mode=text). "
        "Also includes public Timetable Builder (TTB) helpers via the ttb_* "
        "tools: ttb_get_reference_data, ttb_search_courses, "
        "ttb_get_course_details, ttb_check_conflicts. "
        f"{_COURSE_CODE_GUIDANCE} "
        "TTB tools do not require a Quercus token. Quercus and Timetable "
        "Builder are separate unofficial data sources. This project is not "
        "affiliated with the University of Toronto."
    ),
    lifespan=_lifespan,
    mask_error_details=True,
)


@mcp.tool(
    description=(
        "Return currently valid Timetable Builder (TTB) sessions, divisions, "
        "campuses, delivery modes, and course levels. Call this before searching "
        "if you do not already know valid filter values. Sessions are not "
        f"hard-coded and change over time. {_DATA_DISCLAIMER}"
    )
)
async def ttb_get_reference_data() -> dict[str, Any]:
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
        "Search Timetable Builder (TTB) courses by code or title with required "
        "session and division filters. Returns concise summaries and pagination "
        "metadata; use ttb_get_course_details for full section/meeting data. "
        "Requires at least one session and one division from "
        "ttb_get_reference_data. page is one-based; page_size must be between 1 "
        f"and 50. {_COURSE_CODE_GUIDANCE} "
        "Short code-like queries are expanded using the selected division "
        "(never sent as a title search). Empty courses means no match — try the "
        f"full code and correct division. {_DATA_DISCLAIMER}"
    )
)
async def ttb_search_courses(
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
        if is_code and is_short_course_code(course_code):
            pageable = await _search_with_short_code_expansion(
                state, params, course_code
            )
        elif is_code:
            pageable = await state.client.search_courses(
                sessions=params.sessions,
                divisions=params.divisions,
                course_code=course_code,
                course_title="",
                search_course_description=False,
                campuses=params.campuses,
                instructor=params.instructor or "",
                course_levels=params.course_levels,
                delivery_modes=params.delivery_modes,
                available_space=params.available_space_only,
                wait_listable=params.waitlistable_only,
                page=params.page,
                page_size=params.page_size,
            )
        else:
            pageable = await state.client.search_courses(
                sessions=params.sessions,
                divisions=params.divisions,
                course_code="",
                course_title=course_title,
                search_course_description=params.search_description,
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
        "Fetch normalized course and section details from Timetable Builder "
        "(TTB) for a course code in a required session. Optionally filter by "
        "section_code term half (F, S, or Y) — not a LEC/TUT component name. "
        "Returns all matching upstream records for that course/session. Raises "
        f"course_not_found when nothing matches. {_COURSE_CODE_GUIDANCE} "
        f"{_DATA_DISCLAIMER}"
    )
)
async def ttb_get_course_details(
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
    fetched_at = state.clock()
    matched, fatal_error = await _lookup_course_details(state, params, fetched_at)

    if not matched and is_short_course_code(params.course_code) and fatal_error is None:
        # No division on this tool — try all common campus/weight suffixes.
        for candidate in expand_short_course_code(params.course_code, divisions=None):
            if candidate == params.course_code:
                continue
            candidate_params = CourseDetailsInput(
                course_code=candidate,
                session=params.session,
                section_code=params.section_code,
            )
            matched, fatal_error = await _lookup_course_details(
                state,
                candidate_params,
                fetched_at,
            )
            if fatal_error is not None:
                raise_tool_error(fatal_error)
            if matched:
                break

    if fatal_error is not None and not matched:
        raise_tool_error(fatal_error)

    if not matched:
        message = (
            short_code_not_found_message(params.course_code, params.session)
            if is_short_course_code(params.course_code)
            else (
                f"No course records matched {params.course_code} in session "
                f"{params.session}."
            )
        )
        raise_tool_error(CourseNotFoundError(message))

    result = CourseDetailsResult(courses=matched, found=True, fetched_at=fetched_at)
    return model_to_public_dict(result)


async def _search_with_short_code_expansion(
    state: RuntimeState,
    params: SearchCoursesInput,
    short_code: str,
) -> dict[str, Any]:
    """Try division-aware full-code candidates; return the first non-empty hit.

    Short codes must never be sent as ``courseTitle`` (that caused catalogue dumps).
    Per-candidate ``TimetableUpstreamError`` (e.g. HTTP 404) is treated as a miss
    so later suffixes can still succeed. Timeouts/network/rate-limit errors propagate.
    """
    candidates = expand_short_course_code(short_code, params.divisions)
    empty: dict[str, Any] = {"total": 0, "courses": []}
    last_pageable = empty
    for candidate in candidates:
        try:
            pageable = await state.client.search_courses(
                sessions=params.sessions,
                divisions=params.divisions,
                course_code=candidate,
                course_title="",
                search_course_description=False,
                campuses=params.campuses,
                instructor=params.instructor or "",
                course_levels=params.course_levels,
                delivery_modes=params.delivery_modes,
                available_space=params.available_space_only,
                wait_listable=params.waitlistable_only,
                page=params.page,
                page_size=params.page_size,
            )
        except TimetableUpstreamError:
            # Wrong suffix / not offered this term — try next candidate.
            continue
        last_pageable = pageable
        if pageable["total"] > 0:
            return pageable
    return last_pageable


async def _lookup_course_details(
    state: RuntimeState,
    params: CourseDetailsInput,
    fetched_at: datetime,
) -> tuple[list[Course], TimetableError | None]:
    """Fetch and filter course details.

    Returns ``(matched, fatal_error)``. ``fatal_error`` is set for non-expandable
    failures (timeouts, network, rate limits). Upstream application errors on a
    single candidate are treated as empty matches so short-code expansion can continue.
    """
    try:
        raw_courses = await state.client.get_courses_by_code(params.course_code)
    except TimetableUpstreamError:
        return [], None
    except TimetableError as exc:
        return [], exc

    matched: list[Course] = []
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
    return matched, None


@mcp.tool(description=_CHECK_CONFLICTS_DESCRIPTION)
async def ttb_check_conflicts(
    session: str,
    selections: list[SectionSelection],
    minimum_transition_minutes: int = 0,
) -> dict[str, Any]:
    try:
        params = CheckConflictsInput(
            session=session,
            selections=selections,
            minimum_transition_minutes=minimum_transition_minutes,
        )
    except ValidationError as exc:
        message = "; ".join(error.get("msg", "Invalid input") for error in exc.errors())
        raise_tool_error(TimetableValidationError(message))

    state = get_state()
    fetched_at = state.clock()
    unresolved: list[UnresolvedSelection] = []
    resolved_sections: list[ResolvedSection] = []
    cancelled_sections: list[str] = []

    course_codes = [selection.course_code for selection in params.selections]
    fetched = await _fetch_courses_bounded(course_codes, state)

    for selection in params.selections:
        fetch_result = fetched.get(selection.course_code)
        if fetch_result is None:
            unresolved.append(
                UnresolvedSelection(
                    course_code=selection.course_code,
                    section_name=None,
                    reason="Course fetch failed.",
                )
            )
            continue
        raw_courses, error = fetch_result
        if error is not None:
            unresolved.append(
                UnresolvedSelection(
                    course_code=selection.course_code,
                    section_name=None,
                    reason=error.message,
                )
            )
            continue

        session_records = [
            raw
            for raw in raw_courses
            if session_matches(params.session, raw.get("sessions"))
        ]
        if not session_records:
            unresolved.append(
                UnresolvedSelection(
                    course_code=selection.course_code,
                    section_name=None,
                    reason=(
                        f"No course records matched {selection.course_code} in "
                        f"session {params.session}."
                    ),
                )
            )
            continue

        normalized_records = [
            normalize_course(raw, fetched_at=fetched_at) for raw in session_records
        ]
        for section_name in selection.section_names:
            matches = _find_section_matches(normalized_records, section_name)
            if not matches:
                unresolved.append(
                    UnresolvedSelection(
                        course_code=selection.course_code,
                        section_name=section_name,
                        reason="Section was not found for this course/session.",
                    )
                )
                continue
            if len(matches) > 1:
                unresolved.append(
                    UnresolvedSelection(
                        course_code=selection.course_code,
                        section_name=section_name,
                        reason=(
                            "Section name is ambiguous across multiple course "
                            "records for this session."
                        ),
                    )
                )
                continue

            section = matches[0]
            resolved = ResolvedSection(
                course_code=selection.course_code,
                section=section,
            )
            resolved_sections.append(resolved)
            if section.cancelled is True:
                cancelled_sections.append(resolved.label)

    analysis = analyze_conflicts(
        resolved_sections,
        minimum_transition_minutes=params.minimum_transition_minutes,
    )
    is_complete = not unresolved and not analysis.unchecked_meetings
    result = CheckConflictsResult(
        has_conflicts=bool(analysis.conflicts),
        is_complete=is_complete,
        conflicts=analysis.conflicts,
        transition_violations=analysis.transition_violations,
        unresolved=unresolved,
        unchecked_meetings=analysis.unchecked_meetings,
        cancelled_sections=sorted(cancelled_sections),
        checked_section_count=len(resolved_sections),
        fetched_at=fetched_at,
    )
    return model_to_public_dict(result)


async def _fetch_courses_bounded(
    course_codes: list[str],
    state: RuntimeState,
) -> dict[str, tuple[list[dict[str, Any]], TimetableError | None]]:
    """Fetch each unique course code once with bounded concurrency."""
    unique_codes = list(dict.fromkeys(course_codes))
    semaphore = asyncio.Semaphore(state.settings.max_concurrency)
    results: dict[str, tuple[list[dict[str, Any]], TimetableError | None]] = {}

    async def _one(code: str) -> None:
        async with semaphore:
            try:
                courses = await state.client.get_courses_by_code(code)
                results[code] = (courses, None)
            except TimetableError as exc:
                results[code] = ([], exc)

    await asyncio.gather(*(_one(code) for code in unique_codes))
    return results


def _find_section_matches(courses: list[Course], section_name: str) -> list[Section]:
    wanted = section_name.upper()
    matches: list[Section] = []
    for course in courses:
        for section in course.sections:
            if section.name.upper() == wanted:
                matches.append(section)
    return matches


register_quercus_tools(mcp)
