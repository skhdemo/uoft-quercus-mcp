"""In-memory MCP tests for Phase 4 data tools."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from fastmcp import Client
from fastmcp.exceptions import ToolError

from uoft_timetable_mcp.client import TimetableClient
from uoft_timetable_mcp.server import configure_runtime, mcp, reset_runtime
from uoft_timetable_mcp.settings import Settings

FIXTURES = Path(__file__).parent / "fixtures"
BASE_URL = "https://api.easi.utoronto.ca/ttb"
PHASE4_TOOLS = {
    "get_reference_data",
    "search_courses",
    "get_course_details",
}


def _load(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / name).read_text())


def _parse_tool_error(exc: ToolError) -> dict[str, Any]:
    payload = json.loads(str(exc))
    assert "error" in payload
    return payload


class MutableClock:
    def __init__(self, start: datetime) -> None:
        self.now = start

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now = self.now + timedelta(seconds=seconds)


@pytest.fixture
def settings() -> Settings:
    return Settings(
        base_url=BASE_URL,
        connect_timeout_seconds=1.0,
        read_timeout_seconds=1.0,
        reference_cache_ttl_seconds=900,
        max_attempts=1,
        version="0.1.0",
    )


@pytest.fixture
def clock() -> MutableClock:
    return MutableClock(datetime(2026, 7, 27, 1, 0, 0, tzinfo=UTC))


@pytest.fixture
async def timetable_client(settings: Settings) -> AsyncIterator[TimetableClient]:
    client = TimetableClient(settings)
    try:
        yield client
    finally:
        await client.aclose()


@pytest.fixture
async def configured(
    timetable_client: TimetableClient,
    settings: Settings,
    clock: MutableClock,
) -> AsyncIterator[None]:
    configure_runtime(
        client=timetable_client,
        settings=settings,
        clock=clock,
        owns_client=False,
    )
    try:
        yield None
    finally:
        reset_runtime()


def _error_message_has_no_traceback(message: str) -> bool:
    lowered = message.lower()
    return "traceback" not in lowered and 'file "' not in lowered


@respx.mock
@pytest.mark.asyncio
async def test_phase4_tools_are_discoverable(configured: None) -> None:
    async with Client(mcp) as client:
        tools = await client.list_tools()
    names = {tool.name for tool in tools}
    assert names == PHASE4_TOOLS
    by_name = {tool.name: tool for tool in tools}
    reference_description = by_name["get_reference_data"].description or ""
    search_description = by_name["search_courses"].description or ""
    details_description = by_name["get_course_details"].description or ""
    assert "sessions" in reference_description
    assert "page_size" in search_description
    assert "section_code" in details_description
    assert "not affiliated" in reference_description.lower()


@respx.mock
@pytest.mark.asyncio
async def test_get_reference_data_strips_headers(configured: None) -> None:
    fixture = _load("reference_data.json")
    respx.get(f"{BASE_URL}/reference-data").mock(
        return_value=httpx.Response(200, json=fixture)
    )

    async with Client(mcp) as client:
        result = await client.call_tool("get_reference_data", {})

    data = result.data
    assert isinstance(data, dict)
    session_values = [item["value"] for item in data["sessions"]]
    assert "Summer" not in session_values
    assert "20265F" in session_values
    assert any(item["value"] == "ARTSC" for item in data["divisions"])
    assert data["fetched_at"].startswith("2026-07-27T01:00:00")


@respx.mock
@pytest.mark.asyncio
async def test_reference_cache_hit_and_expiry(
    configured: None,
    clock: MutableClock,
) -> None:
    fixture = _load("reference_data.json")
    route = respx.get(f"{BASE_URL}/reference-data").mock(
        return_value=httpx.Response(200, json=fixture)
    )

    async with Client(mcp) as client:
        first = await client.call_tool("get_reference_data", {})
        second = await client.call_tool("get_reference_data", {})
        assert route.call_count == 1
        assert first.data == second.data

        clock.advance(901)
        third = await client.call_tool("get_reference_data", {})
        assert route.call_count == 2
        assert third.data["fetched_at"].startswith("2026-07-27T01:15:01")


@respx.mock
@pytest.mark.asyncio
async def test_reference_cache_single_flight(configured: None) -> None:
    fixture = _load("reference_data.json")
    release = asyncio.Event()
    started = asyncio.Event()

    async def _slow_response(_request: httpx.Request) -> httpx.Response:
        started.set()
        await release.wait()
        return httpx.Response(200, json=fixture)

    route = respx.get(f"{BASE_URL}/reference-data").mock(side_effect=_slow_response)

    async with Client(mcp) as client:
        task_a = asyncio.create_task(client.call_tool("get_reference_data", {}))
        await started.wait()
        task_b = asyncio.create_task(client.call_tool("get_reference_data", {}))
        await asyncio.sleep(0.05)
        release.set()
        results = await asyncio.gather(task_a, task_b)

    assert route.call_count == 1
    assert results[0].data == results[1].data


@respx.mock
@pytest.mark.asyncio
async def test_failed_reference_fetch_is_not_cached(configured: None) -> None:
    error_fixture = _load("upstream_error.json")
    ok_fixture = _load("reference_data.json")
    route = respx.get(f"{BASE_URL}/reference-data").mock(
        side_effect=[
            httpx.Response(200, json=error_fixture),
            httpx.Response(200, json=ok_fixture),
        ]
    )

    async with Client(mcp) as client:
        with pytest.raises(ToolError) as exc_info:
            await client.call_tool("get_reference_data", {})
        payload = _parse_tool_error(exc_info.value)
        assert payload["error"]["code"] == "upstream_error"
        assert _error_message_has_no_traceback(payload["error"]["message"])

        result = await client.call_tool("get_reference_data", {})
        assert route.call_count == 2
        assert result.data["sessions"]


@respx.mock
@pytest.mark.asyncio
async def test_search_courses_rejects_missing_filters(configured: None) -> None:
    async with Client(mcp) as client:
        with pytest.raises(ToolError) as no_sessions:
            await client.call_tool(
                "search_courses",
                {"sessions": [], "divisions": ["ARTSC"], "query": "CSC108H1"},
            )
        with pytest.raises(ToolError) as no_divisions:
            await client.call_tool(
                "search_courses",
                {"sessions": ["20269"], "divisions": [], "query": "CSC108H1"},
            )
        with pytest.raises(ToolError) as bad_page_size:
            await client.call_tool(
                "search_courses",
                {
                    "sessions": ["20269"],
                    "divisions": ["ARTSC"],
                    "page_size": 51,
                },
            )

    assert "session" in str(no_sessions.value).lower()
    assert "division" in str(no_divisions.value).lower()
    assert (
        "page_size" in str(bad_page_size.value).lower()
        or "less than or equal" in str(bad_page_size.value).lower()
    )


@respx.mock
@pytest.mark.asyncio
async def test_search_courses_routes_code_and_title_queries(configured: None) -> None:
    fixture = _load("search_csc108.json")
    route = respx.post(f"{BASE_URL}/getPageableCourses").mock(
        return_value=httpx.Response(200, json=fixture)
    )

    async with Client(mcp) as client:
        await client.call_tool(
            "search_courses",
            {
                "query": "csc108h1",
                "sessions": ["20269"],
                "divisions": ["ARTSC"],
            },
        )
        code_body = json.loads(route.calls.last.request.content.decode())
        assert code_body["courseCodeAndTitleProps"]["courseCode"] == "CSC108H1"
        assert code_body["courseCodeAndTitleProps"]["courseTitle"] == ""

        await client.call_tool(
            "search_courses",
            {
                "query": "computer programming",
                "sessions": ["20269"],
                "divisions": ["ARTSC"],
                "search_description": True,
            },
        )
        title_body = json.loads(route.calls.last.request.content.decode())
        assert title_body["courseCodeAndTitleProps"]["courseCode"] == ""
        assert (
            title_body["courseCodeAndTitleProps"]["courseTitle"]
            == "computer programming"
        )
        assert title_body["courseCodeAndTitleProps"]["searchCourseDescription"] is True


@respx.mock
@pytest.mark.asyncio
async def test_search_courses_concise_pagination_and_empty(
    configured: None,
) -> None:
    fixture = _load("search_csc108.json")
    respx.post(f"{BASE_URL}/getPageableCourses").mock(
        side_effect=[
            httpx.Response(200, json=fixture),
            httpx.Response(
                200,
                json={
                    "payload": {
                        "pageableCourse": {"total": 0, "courses": []},
                        "divisionalLegends": [],
                        "divisionalEnrolmentIndicators": [],
                    },
                    "status": [],
                },
            ),
        ]
    )

    async with Client(mcp) as client:
        result = await client.call_tool(
            "search_courses",
            {
                "query": "CSC108H1",
                "sessions": ["20269"],
                "divisions": ["ARTSC"],
                "page": 1,
                "page_size": 10,
            },
        )
        data = result.data
        assert data["total"] == 1
        assert data["has_next_page"] is False
        assert data["courses"][0]["code"] == "CSC108H1"
        assert "section_count" in data["courses"][0]
        assert "sections" not in data["courses"][0]

        empty = await client.call_tool(
            "search_courses",
            {
                "query": "ZZZ999H1",
                "sessions": ["20269"],
                "divisions": ["ARTSC"],
            },
        )
        assert empty.data["courses"] == []
        assert empty.data["total"] == 0
        assert empty.is_error is False


@respx.mock
@pytest.mark.asyncio
async def test_get_course_details_filters_session_and_case(
    configured: None,
) -> None:
    fixture = _load("course_csc108.json")
    respx.get(url__regex=r".*/getCoursesByCodeAndSectionCode/CSC108H1$").mock(
        return_value=httpx.Response(200, json=fixture)
    )

    async with Client(mcp) as client:
        result = await client.call_tool(
            "get_course_details",
            {"course_code": "csc108h1", "session": "20269"},
        )

    data = result.data
    assert data["found"] is True
    codes = {
        (course["section_code"], tuple(course["sessions"]))
        for course in data["courses"]
    }
    assert ("F", ("20269",)) in codes
    assert ("Y", ("20269-20271",)) in codes
    assert all(course["section_code"] != "S" for course in data["courses"])


@respx.mock
@pytest.mark.asyncio
async def test_get_course_details_combined_session_and_multiple_records(
    configured: None,
) -> None:
    fixture = _load("course_csc108.json")
    respx.get(url__regex=r".*/getCoursesByCodeAndSectionCode/CSC108H1$").mock(
        return_value=httpx.Response(200, json=fixture)
    )

    async with Client(mcp) as client:
        winter = await client.call_tool(
            "get_course_details",
            {"course_code": "CSC108H1", "session": "20271"},
        )
        year = await client.call_tool(
            "get_course_details",
            {"course_code": "CSC108H1", "session": "20269", "section_code": "y"},
        )

    winter_sections = {course["section_code"] for course in winter.data["courses"]}
    assert winter_sections == {"S", "Y"}
    assert len(year.data["courses"]) == 1
    assert year.data["courses"][0]["section_code"] == "Y"
    assert year.data["courses"][0]["sessions"] == ["20269-20271"]


@respx.mock
@pytest.mark.asyncio
async def test_get_course_details_not_found_error_shape(configured: None) -> None:
    fixture = _load("course_csc108.json")
    respx.get(url__regex=r".*/getCoursesByCodeAndSectionCode/CSC108H1$").mock(
        return_value=httpx.Response(200, json=fixture)
    )

    async with Client(mcp) as client:
        with pytest.raises(ToolError) as exc_info:
            await client.call_tool(
                "get_course_details",
                {"course_code": "CSC108H1", "session": "19991"},
            )

    payload = _parse_tool_error(exc_info.value)
    assert payload["error"]["code"] == "course_not_found"
    assert payload["error"]["retryable"] is False
    assert "CSC108H1" in payload["error"]["message"]
    assert _error_message_has_no_traceback(payload["error"]["message"])


@respx.mock
@pytest.mark.asyncio
async def test_mapped_timeout_error_shape(configured: None) -> None:
    respx.get(f"{BASE_URL}/reference-data").mock(
        side_effect=httpx.ReadTimeout("read timed out")
    )

    async with Client(mcp) as client:
        with pytest.raises(ToolError) as exc_info:
            await client.call_tool("get_reference_data", {})

    payload = _parse_tool_error(exc_info.value)
    assert payload["error"]["code"] == "upstream_timeout"
    assert payload["error"]["retryable"] is True
    assert _error_message_has_no_traceback(str(exc_info.value))
