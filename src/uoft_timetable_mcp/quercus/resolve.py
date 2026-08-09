"""Resolve human course/file references to Canvas ids."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from uoft_timetable_mcp.quercus.client import QuercusClient
from uoft_timetable_mcp.quercus.errors import (
    QuercusAmbiguousError,
    QuercusNotFoundError,
)
from uoft_timetable_mcp.quercus.normalize import normalize_course, normalize_file
from uoft_timetable_mcp.timetable.normalize import utc_now

Clock = Callable[[], datetime]

# Canvas announcements default to the last 14 days when start_date is omitted.
ANNOUNCEMENT_FALLBACK_LOOKBACK_DAYS = 365
AnnouncementStartSource = Literal["term_start_at", "fallback_lookback"]


def _parse_canvas_datetime(value: str) -> datetime | None:
    text = value.strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def resolve_announcement_window(
    course: Mapping[str, Any],
    *,
    now: datetime,
    fallback_days: int = ANNOUNCEMENT_FALLBACK_LOOKBACK_DAYS,
) -> dict[str, str]:
    """Pick announcement ``start_date`` / ``end_date`` for Canvas.

    Prefer the course term start when present; otherwise look back
    ``fallback_days``. Always set ``end_date`` to ``now`` so Canvas does not
    clamp the window to 28 days after ``start_date``.
    """
    if now.tzinfo is None:
        clock = now.replace(tzinfo=UTC)
    else:
        clock = now.astimezone(UTC)

    start = clock - timedelta(days=fallback_days)
    start_source: AnnouncementStartSource = "fallback_lookback"
    term = course.get("term")
    if isinstance(term, dict):
        raw_start = term.get("start_at")
        if isinstance(raw_start, str):
            parsed = _parse_canvas_datetime(raw_start)
            if parsed is not None:
                start = parsed
                start_source = "term_start_at"

    return {
        "start_date": start.date().isoformat(),
        "end_date": clock.date().isoformat(),
        "start_source": start_source,
    }


class CourseListCache:
    """Short-TTL cache of normalized courses with single-flight loading."""

    def __init__(self, *, ttl_seconds: int, clock: Clock = utc_now) -> None:
        self._ttl_seconds = ttl_seconds
        self._clock = clock
        self._lock = asyncio.Lock()
        self._courses: list[dict[str, Any]] | None = None
        self._enrollment_state: str | None = "active"
        self._expires_at: datetime | None = None
        self._inflight: dict[str | None, asyncio.Future[list[dict[str, Any]]]] = {}

    def clear(self) -> None:
        self._courses = None
        self._expires_at = None
        self._inflight.clear()

    def set_courses(
        self,
        courses: list[dict[str, Any]],
        *,
        enrollment_state: str | None = "active",
    ) -> None:
        """Warm the cache from an already-fetched normalized course list."""
        self._courses = list(courses)
        self._enrollment_state = enrollment_state
        self._expires_at = self._clock() + timedelta(seconds=self._ttl_seconds)
        self._inflight.pop(enrollment_state, None)

    async def get_courses(
        self,
        client: QuercusClient,
        *,
        enrollment_state: str | None = "active",
    ) -> list[dict[str, Any]]:
        async with self._lock:
            now = self._clock()
            if (
                self._courses is not None
                and self._expires_at is not None
                and now < self._expires_at
                and self._enrollment_state == enrollment_state
            ):
                return list(self._courses)

            existing = self._inflight.get(enrollment_state)
            if existing is not None:
                wait_for = existing
                owner = False
            else:
                wait_for = asyncio.get_running_loop().create_future()
                self._inflight[enrollment_state] = wait_for
                owner = True

        if not owner:
            return await wait_for

        try:
            raw = await client.list_courses(enrollment_state=enrollment_state)
            courses = [normalize_course(item) for item in raw]
        except Exception as exc:
            async with self._lock:
                # Failures are never cached.
                if not wait_for.done():
                    wait_for.set_exception(exc)
                self._inflight.pop(enrollment_state, None)
            return await wait_for

        async with self._lock:
            self._courses = courses
            self._enrollment_state = enrollment_state
            self._expires_at = self._clock() + timedelta(seconds=self._ttl_seconds)
            if not wait_for.done():
                wait_for.set_result(list(courses))
            self._inflight.pop(enrollment_state, None)
        return list(courses)


def _course_summary(course: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": course.get("id"),
        "course_code": course.get("course_code"),
        "name": course.get("name"),
    }


def resolve_course(course: str, courses: list[dict[str, Any]]) -> dict[str, Any]:
    """Resolve a flexible course ref to one normalized course dict."""
    query = course.strip()
    if not query:
        raise QuercusNotFoundError(
            "Course reference is empty. Call quercus_list_courses for candidates."
        )

    # Tier 1: exact Canvas id
    id_matches = [c for c in courses if str(c.get("id")) == query]
    if len(id_matches) == 1:
        return id_matches[0]
    if len(id_matches) > 1:
        raise QuercusAmbiguousError(
            f"Multiple Quercus courses matched id {query!r}.",
            candidates=[_course_summary(c) for c in id_matches],
        )

    upper = query.upper()

    # Tier 2: exact course_code
    exact_code = [
        c
        for c in courses
        if isinstance(c.get("course_code"), str)
        and c["course_code"].strip().upper() == upper
    ]
    if len(exact_code) == 1:
        return exact_code[0]
    if len(exact_code) > 1:
        raise QuercusAmbiguousError(
            f"Multiple Quercus courses matched code {query!r}.",
            candidates=[_course_summary(c) for c in exact_code],
        )

    # Tier 3: course_code prefix/fragment
    code_fragment = [
        c
        for c in courses
        if isinstance(c.get("course_code"), str)
        and upper in c["course_code"].strip().upper()
    ]
    if len(code_fragment) == 1:
        return code_fragment[0]
    if len(code_fragment) > 1:
        raise QuercusAmbiguousError(
            f"Multiple Quercus courses matched {query!r}.",
            candidates=[_course_summary(c) for c in code_fragment],
        )

    # Tier 4: name substring
    lower = query.lower()
    name_matches = [
        c
        for c in courses
        if isinstance(c.get("name"), str) and lower in c["name"].lower()
    ]
    if len(name_matches) == 1:
        return name_matches[0]
    if len(name_matches) > 1:
        raise QuercusAmbiguousError(
            f"Multiple Quercus courses matched {query!r}.",
            candidates=[_course_summary(c) for c in name_matches],
        )

    raise QuercusNotFoundError(
        f"No Quercus course matched {query!r}. Call quercus_list_courses "
        "and retry with a Canvas id or fuller course code."
    )


def resolve_file_in_course(
    name_query: str,
    files: list[dict[str, Any]],
) -> dict[str, Any]:
    """Resolve a file id or filename fragment within one course's file list."""
    query = name_query.strip()
    if not query:
        raise QuercusNotFoundError("File reference is empty.")

    id_matches = [f for f in files if str(f.get("id")) == query]
    if len(id_matches) == 1:
        return id_matches[0]
    if len(id_matches) > 1:
        raise QuercusAmbiguousError(
            f"Multiple Quercus files matched id {query!r}.",
            candidates=[normalize_file(f) for f in id_matches],
        )

    lower = query.lower()
    exact_name = [
        f
        for f in files
        if (
            isinstance(f.get("display_name"), str)
            and f["display_name"].lower() == lower
        )
        or (isinstance(f.get("filename"), str) and f["filename"].lower() == lower)
    ]
    if len(exact_name) == 1:
        return exact_name[0]
    if len(exact_name) > 1:
        raise QuercusAmbiguousError(
            f"Multiple Quercus files matched {query!r}.",
            candidates=[normalize_file(f) for f in exact_name],
        )

    fragment = [
        f
        for f in files
        if (
            isinstance(f.get("display_name"), str)
            and lower in f["display_name"].lower()
        )
        or (isinstance(f.get("filename"), str) and lower in f["filename"].lower())
    ]
    if len(fragment) == 1:
        return fragment[0]
    if len(fragment) > 1:
        raise QuercusAmbiguousError(
            f"Multiple Quercus files matched {query!r}.",
            candidates=[normalize_file(f) for f in fragment],
        )

    raise QuercusNotFoundError(
        f"No Quercus file matched {query!r} in this course. "
        "Call quercus_list_files and retry with a file id."
    )


async def resolve_course_ref(
    course: str,
    *,
    client: QuercusClient,
    cache: CourseListCache,
    include_concluded: bool = False,
) -> dict[str, Any]:
    enrollment_state = None if include_concluded else "active"
    courses = await cache.get_courses(client, enrollment_state=enrollment_state)
    return resolve_course(course, courses)
