"""Unit tests for Quercus course/file resolution."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx

from uoft_quercus_mcp.quercus.auth import PersonalTokenAuth
from uoft_quercus_mcp.quercus.client import QuercusClient
from uoft_quercus_mcp.quercus.errors import (
    QuercusAmbiguousError,
    QuercusNotFoundError,
    QuercusUpstreamError,
)
from uoft_quercus_mcp.quercus.normalize import normalize_course, normalize_file
from uoft_quercus_mcp.quercus.resolve import (
    CourseListCache,
    resolve_announcement_window,
    resolve_course,
    resolve_file_in_course,
)
from uoft_quercus_mcp.quercus.settings import QuercusSettings
from uoft_quercus_mcp.timetable.normalize import utc_now

FIXTURES = Path(__file__).parent / "fixtures" / "quercus"
API_ROOT = "https://q.utoronto.ca/api/v1"


def _load(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text())


@pytest.fixture
def courses() -> list[dict[str, Any]]:
    raw = _load("courses_page1.json") + _load("courses_page2.json")
    return [normalize_course(item) for item in raw]


def test_resolve_exact_id(courses: list[dict[str, Any]]) -> None:
    course = resolve_course("1001", courses)
    assert course["id"] == 1001
    assert course["course_code"] == "CSCA08H3"


def test_resolve_exact_code(courses: list[dict[str, Any]]) -> None:
    course = resolve_course("mata22h3", courses)
    assert course["id"] == 1002


def test_resolve_code_fragment(courses: list[dict[str, Any]]) -> None:
    course = resolve_course("MATA22", courses)
    assert course["id"] == 1002


def test_resolve_ambiguous_fragment(courses: list[dict[str, Any]]) -> None:
    with pytest.raises(QuercusAmbiguousError) as exc_info:
        resolve_course("MAT", courses)
    assert exc_info.value.code == "quercus_ambiguous"
    assert len(exc_info.value.candidates) >= 2


def test_resolve_missing(courses: list[dict[str, Any]]) -> None:
    with pytest.raises(QuercusNotFoundError) as exc_info:
        resolve_course("ZZZ999", courses)
    assert "quercus_list_courses" in exc_info.value.message


def test_announcement_window_uses_term_start(
    courses: list[dict[str, Any]],
) -> None:
    now = datetime(2026, 7, 27, 12, 0, 0, tzinfo=UTC)
    window = resolve_announcement_window(courses[1], now=now)
    assert window["start_date"] == "2026-09-01"
    assert window["end_date"] == "2026-07-27"
    assert window["start_source"] == "term_start_at"


def test_announcement_window_falls_back_without_term() -> None:
    now = datetime(2026, 7, 27, 12, 0, 0, tzinfo=UTC)
    window = resolve_announcement_window(
        {"id": 1, "course_code": "X", "name": "Y"},
        now=now,
    )
    assert window["start_date"] == "2025-07-27"
    assert window["end_date"] == "2026-07-27"
    assert window["start_source"] == "fallback_lookback"


def test_resolve_file_by_name() -> None:
    files = [normalize_file(item) for item in _load("files_page1.json")]
    matched = resolve_file_in_course("quiz2", files)
    assert matched["id"] == 801


@respx.mock
@pytest.mark.asyncio
async def test_course_cache_hit_avoids_second_http() -> None:
    settings = QuercusSettings(
        base_url="https://q.utoronto.ca",
        course_cache_ttl_seconds=120,
        max_attempts=1,
        version="0.1.0",
    )
    route = respx.get(f"{API_ROOT}/courses").mock(
        return_value=httpx.Response(200, json=_load("courses_page1.json"))
    )
    cache = CourseListCache(ttl_seconds=120, clock=utc_now)
    async with QuercusClient(settings, auth=PersonalTokenAuth("tok")) as client:
        first = await cache.get_courses(client)
        second = await cache.get_courses(client)
    assert first == second
    assert route.call_count == 1


@respx.mock
@pytest.mark.asyncio
async def test_course_cache_single_flight() -> None:
    settings = QuercusSettings(
        base_url="https://q.utoronto.ca",
        course_cache_ttl_seconds=120,
        max_attempts=1,
        version="0.1.0",
    )
    release = asyncio.Event()
    started = asyncio.Event()

    async def _slow_response(_request: httpx.Request) -> httpx.Response:
        started.set()
        await release.wait()
        return httpx.Response(200, json=_load("courses_page1.json"))

    route = respx.get(f"{API_ROOT}/courses").mock(side_effect=_slow_response)
    cache = CourseListCache(ttl_seconds=120, clock=utc_now)
    async with QuercusClient(settings, auth=PersonalTokenAuth("tok")) as client:
        task_a = asyncio.create_task(cache.get_courses(client))
        await started.wait()
        task_b = asyncio.create_task(cache.get_courses(client))
        await asyncio.sleep(0.05)
        release.set()
        results = await asyncio.gather(task_a, task_b)

    assert route.call_count == 1
    assert results[0] == results[1]


@respx.mock
@pytest.mark.asyncio
async def test_course_cache_failure_is_not_cached() -> None:
    settings = QuercusSettings(
        base_url="https://q.utoronto.ca",
        course_cache_ttl_seconds=120,
        max_attempts=1,
        version="0.1.0",
    )
    route = respx.get(f"{API_ROOT}/courses").mock(
        side_effect=[
            httpx.Response(500, json={"errors": [{"message": "boom"}]}),
            httpx.Response(200, json=_load("courses_page1.json")),
        ]
    )
    cache = CourseListCache(ttl_seconds=120, clock=utc_now)
    async with QuercusClient(settings, auth=PersonalTokenAuth("tok")) as client:
        with pytest.raises(QuercusUpstreamError):
            await cache.get_courses(client)
        courses = await cache.get_courses(client)

    assert route.call_count == 2
    assert courses[0]["id"] == 1001


@respx.mock
@pytest.mark.asyncio
async def test_set_courses_warms_cache_without_http() -> None:
    settings = QuercusSettings(
        base_url="https://q.utoronto.ca",
        course_cache_ttl_seconds=120,
        max_attempts=1,
        version="0.1.0",
    )
    route = respx.get(f"{API_ROOT}/courses").mock(
        return_value=httpx.Response(200, json=_load("courses_page1.json"))
    )
    cache = CourseListCache(ttl_seconds=120, clock=utc_now)
    warmed = [normalize_course(item) for item in _load("courses_page1.json")]
    cache.set_courses(warmed, enrollment_state="active")
    async with QuercusClient(settings, auth=PersonalTokenAuth("tok")) as client:
        courses = await cache.get_courses(client, enrollment_state="active")
    assert route.call_count == 0
    assert courses[0]["id"] == 1001
