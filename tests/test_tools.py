"""In-memory MCP tests for V1 data and conflict tools."""

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

from uoft_timetable_mcp.quercus.auth import PersonalTokenAuth
from uoft_timetable_mcp.quercus.client import QuercusClient
from uoft_timetable_mcp.quercus.settings import QuercusSettings
from uoft_timetable_mcp.server import configure_runtime, mcp, reset_runtime
from uoft_timetable_mcp.timetable.client import TimetableClient
from uoft_timetable_mcp.timetable.settings import Settings

FIXTURES = Path(__file__).parent / "fixtures"
BASE_URL = "https://api.easi.utoronto.ca/ttb"
V1_TOOLS = {
    "get_reference_data",
    "search_courses",
    "get_course_details",
    "check_conflicts",
    "quercus_whoami",
    "quercus_list_courses",
    "quercus_list_todo",
    "quercus_list_assignments",
    "quercus_list_announcements",
    "quercus_list_modules",
    "quercus_list_files",
    "quercus_get_file",
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
    quercus_settings = QuercusSettings(
        base_url="https://q.utoronto.ca",
        connect_timeout_seconds=1.0,
        read_timeout_seconds=1.0,
        max_attempts=1,
        version="0.1.0",
    )
    quercus_client = QuercusClient(
        quercus_settings,
        auth=PersonalTokenAuth(None),
    )
    configure_runtime(
        client=timetable_client,
        settings=settings,
        clock=clock,
        owns_client=False,
        quercus_client=quercus_client,
        quercus_settings=quercus_settings,
        quercus_auth=PersonalTokenAuth(None),
        owns_quercus_client=False,
    )
    try:
        yield None
    finally:
        reset_runtime()
        await quercus_client.aclose()


def _error_message_has_no_traceback(message: str) -> bool:
    lowered = message.lower()
    return "traceback" not in lowered and 'file "' not in lowered


@respx.mock
@pytest.mark.asyncio
async def test_v1_tools_are_discoverable(configured: None) -> None:
    async with Client(mcp) as client:
        tools = await client.list_tools()
    names = {tool.name for tool in tools}
    assert names == V1_TOOLS
    by_name = {tool.name: tool for tool in tools}
    reference_description = by_name["get_reference_data"].description or ""
    search_description = by_name["search_courses"].description or ""
    details_description = by_name["get_course_details"].description or ""
    conflicts_description = by_name["check_conflicts"].description or ""
    assert "sessions" in reference_description
    assert "page_size" in search_description
    assert "section_code" in details_description
    assert "not affiliated" in reference_description.lower()
    assert "has_conflicts" in conflicts_description
    assert "transition_violations" in conflicts_description
    assert "is_complete" in conflicts_description
    assert "verified" in conflicts_description.lower()
    assert "campus suffix" in search_description.lower()
    assert "SCAR" in search_description
    assert "H3" in search_description
    assert "campus suffix" in details_description.lower()
    assert "full" in conflicts_description.lower()


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


def _search_hit(code: str, *, section_code: str = "F") -> dict[str, Any]:
    return {
        "payload": {
            "pageableCourse": {
                "total": 1,
                "courses": [
                    {
                        "id": "mock-1",
                        "code": code,
                        "name": f"Mock {code}",
                        "sectionCode": section_code,
                        "campus": "Scarborough",
                        "sessions": ["20269"],
                        "sections": [{"name": "LEC0101"}],
                    }
                ],
            },
            "divisionalLegends": [],
            "divisionalEnrolmentIndicators": [],
        },
        "status": [],
    }


def _empty_search() -> dict[str, Any]:
    return {
        "payload": {
            "pageableCourse": {"total": 0, "courses": []},
            "divisionalLegends": [],
            "divisionalEnrolmentIndicators": [],
        },
        "status": [],
    }


@respx.mock
@pytest.mark.asyncio
async def test_search_courses_full_utsc_code_not_title(configured: None) -> None:
    route = respx.post(f"{BASE_URL}/getPageableCourses").mock(
        return_value=httpx.Response(200, json=_search_hit("CSCA08H3"))
    )

    async with Client(mcp) as client:
        result = await client.call_tool(
            "search_courses",
            {
                "query": "CSCA08H3",
                "sessions": ["20269"],
                "divisions": ["SCAR"],
            },
        )

    body = json.loads(route.calls.last.request.content.decode())
    assert body["courseCodeAndTitleProps"]["courseCode"] == "CSCA08H3"
    assert body["courseCodeAndTitleProps"]["courseTitle"] == ""
    assert result.data["courses"][0]["code"] == "CSCA08H3"
    assert result.data["total"] == 1


@respx.mock
@pytest.mark.asyncio
async def test_search_courses_short_code_expands_for_scar(configured: None) -> None:
    def _side_effect(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode())
        code = body["courseCodeAndTitleProps"]["courseCode"]
        title = body["courseCodeAndTitleProps"]["courseTitle"]
        # Regression: short/code-like queries must never become title searches.
        assert title == ""
        assert code != "CSCA08"
        if code == "CSCA08H3":
            return httpx.Response(200, json=_search_hit("CSCA08H3"))
        return httpx.Response(200, json=_empty_search())

    route = respx.post(f"{BASE_URL}/getPageableCourses").mock(side_effect=_side_effect)

    async with Client(mcp) as client:
        result = await client.call_tool(
            "search_courses",
            {
                "query": "CSCA08",
                "sessions": ["20269"],
                "divisions": ["SCAR"],
            },
        )

    requested_codes = [
        json.loads(call.request.content.decode())["courseCodeAndTitleProps"][
            "courseCode"
        ]
        for call in route.calls
    ]
    assert requested_codes[0] == "CSCA08H3"
    assert "CSCA08" not in requested_codes
    assert result.data["courses"][0]["code"] == "CSCA08H3"
    assert result.data["total"] == 1


@respx.mock
@pytest.mark.asyncio
async def test_search_courses_short_code_skips_candidate_404(configured: None) -> None:
    def _side_effect(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode())
        code = body["courseCodeAndTitleProps"]["courseCode"]
        title = body["courseCodeAndTitleProps"]["courseTitle"]
        assert title == ""
        assert code != "CSCA08"
        if code == "CSCA08H3":
            return httpx.Response(404, text="not found")
        if code == "CSCA08Y3":
            return httpx.Response(200, json=_search_hit("CSCA08Y3", section_code="Y"))
        return httpx.Response(200, json=_empty_search())

    route = respx.post(f"{BASE_URL}/getPageableCourses").mock(side_effect=_side_effect)

    async with Client(mcp) as client:
        result = await client.call_tool(
            "search_courses",
            {
                "query": "CSCA08",
                "sessions": ["20269"],
                "divisions": ["SCAR"],
            },
        )

    requested_codes = [
        json.loads(call.request.content.decode())["courseCodeAndTitleProps"][
            "courseCode"
        ]
        for call in route.calls
    ]
    assert requested_codes[:2] == ["CSCA08H3", "CSCA08Y3"]
    assert route.call_count >= 2
    assert result.is_error is False
    assert result.data["courses"][0]["code"] == "CSCA08Y3"
    assert result.data["total"] == 1


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
async def test_get_course_details_short_code_expands(configured: None) -> None:
    short_route = respx.get(
        url__regex=r".*/getCoursesByCodeAndSectionCode/CSCA08$"
    ).mock(return_value=httpx.Response(404, text="not found"))
    full_fixture = {
        "payload": {
            "pageableCourse": {
                "total": 1,
                "courses": [
                    {
                        "id": "mock-csca08",
                        "code": "CSCA08H3",
                        "name": "Introduction to Computer Science I",
                        "sectionCode": "F",
                        "campus": "Scarborough",
                        "sessions": ["20269"],
                        "sections": [
                            {
                                "name": "LEC01",
                                "type": "Lecture",
                                "teachMethod": "LEC",
                                "sectionNumber": "01",
                                "meetingTimes": [],
                                "instructors": [],
                                "currentEnrolment": 10,
                                "maxEnrolment": 100,
                                "cancelInd": "N",
                                "waitlistInd": "N",
                                "deliveryModes": [],
                                "currentWaitlist": 0,
                                "enrolmentInd": "P",
                                "tbaInd": "N",
                                "notes": [],
                            }
                        ],
                        "department": {"code": "CSC", "name": "Computer Science"},
                        "faculty": {"code": "SCAR", "name": "UTSC"},
                        "title": "Introduction to Computer Science I",
                        "notes": [],
                        "cancelInd": "N",
                        "cmCourseInfo": {
                            "description": "Intro CS",
                            "title": "Introduction to Computer Science I",
                            "prerequisitesText": "",
                            "corequisitesText": "",
                            "exclusionsText": "",
                            "division": "University of Toronto Scarborough",
                            "breadthRequirements": [],
                            "distributionRequirements": [],
                        },
                    }
                ],
            },
            "divisionalLegends": [],
            "divisionalEnrolmentIndicators": [],
        },
        "status": [],
    }
    full_route = respx.get(
        url__regex=r".*/getCoursesByCodeAndSectionCode/CSCA08H3$"
    ).mock(return_value=httpx.Response(200, json=full_fixture))
    # Other suffix candidates return empty.
    for suffix in ("H1", "Y1", "Y3", "H5", "Y5"):
        respx.get(
            url__regex=rf".*/getCoursesByCodeAndSectionCode/CSCA08{suffix}$"
        ).mock(
            return_value=httpx.Response(
                200,
                json={
                    "payload": {
                        "pageableCourse": {"total": 0, "courses": []},
                        "divisionalLegends": [],
                        "divisionalEnrolmentIndicators": [],
                    },
                    "status": [],
                },
            )
        )

    async with Client(mcp) as client:
        result = await client.call_tool(
            "get_course_details",
            {"course_code": "CSCA08", "session": "20269"},
        )

    assert short_route.called
    assert full_route.called
    assert result.data["found"] is True
    assert result.data["courses"][0]["code"] == "CSCA08H3"


@respx.mock
@pytest.mark.asyncio
async def test_get_course_details_short_code_not_found_mentions_suffixes(
    configured: None,
) -> None:
    respx.get(url__regex=r".*/getCoursesByCodeAndSectionCode/CSCA08.*$").mock(
        return_value=httpx.Response(
            200,
            json={
                "payload": {
                    "pageableCourse": {"total": 0, "courses": []},
                    "divisionalLegends": [],
                    "divisionalEnrolmentIndicators": [],
                },
                "status": [],
            },
        )
    )

    async with Client(mcp) as client:
        with pytest.raises(ToolError) as exc_info:
            await client.call_tool(
                "get_course_details",
                {"course_code": "CSCA08", "session": "20269"},
            )

    payload = _parse_tool_error(exc_info.value)
    assert payload["error"]["code"] == "course_not_found"
    message = payload["error"]["message"]
    assert "campus suffix" in message.lower()
    assert "H3" in message
    assert "CSCA08H3" in message
    assert _error_message_has_no_traceback(message)


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


def _course_envelope(courses: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "payload": {
            "pageableCourse": {"total": len(courses), "courses": courses},
            "divisionalLegends": [],
            "divisionalEnrolmentIndicators": [],
        },
        "status": [],
    }


def _minimal_section(
    name: str,
    *,
    day: int,
    start_ms: int,
    end_ms: int,
    cancel_ind: str = "N",
    tba_ind: str = "N",
    repetition: str = "WEEKLY",
) -> dict[str, Any]:
    return {
        "name": name,
        "type": "Lecture",
        "teachMethod": "LEC",
        "sectionNumber": name[-4:],
        "meetingTimes": [
            {
                "start": {"day": day, "millisofday": start_ms},
                "end": {"day": day, "millisofday": end_ms},
                "building": {
                    "buildingCode": "BA",
                    "buildingRoomNumber": "100",
                    "buildingRoomSuffix": "",
                    "buildingUrl": None,
                    "buildingName": None,
                },
                "sessionCode": "20269",
                "repetition": repetition,
                "repetitionTime": "ONCE_A_WEEK",
            }
        ],
        "instructors": [],
        "currentEnrolment": 1,
        "maxEnrolment": 10,
        "cancelInd": cancel_ind,
        "waitlistInd": "N",
        "deliveryModes": [{"session": "20269", "mode": "INPER"}],
        "currentWaitlist": 0,
        "enrolmentInd": "P",
        "tbaInd": tba_ind,
        "notes": [],
    }


@respx.mock
@pytest.mark.asyncio
async def test_check_conflicts_detects_known_overlap(configured: None) -> None:
    # Fixture LEC0201 Wed 13:00-15:00 overlaps TUT0101 Wed 14:00-15:00.
    fixture = _load("course_csc108.json")
    respx.get(url__regex=r".*/getCoursesByCodeAndSectionCode/CSC108H1$").mock(
        return_value=httpx.Response(200, json=fixture)
    )

    async with Client(mcp) as client:
        result = await client.call_tool(
            "check_conflicts",
            {
                "session": "20269",
                "selections": [
                    {
                        "course_code": "csc108h1",
                        "section_names": ["lec0201", "TUT0101"],
                    }
                ],
            },
        )

    data = result.data
    assert data["has_conflicts"] is True
    assert data["is_complete"] is True
    assert data["checked_section_count"] == 2
    assert data["conflicts"][0]["day"] == "Wednesday"
    assert data["conflicts"][0]["overlap_minutes"] == 60


@respx.mock
@pytest.mark.asyncio
async def test_check_conflicts_dedupes_course_fetches(configured: None) -> None:
    fixture = _load("course_csc108.json")
    route = respx.get(url__regex=r".*/getCoursesByCodeAndSectionCode/CSC108H1$").mock(
        return_value=httpx.Response(200, json=fixture)
    )

    async with Client(mcp) as client:
        result = await client.call_tool(
            "check_conflicts",
            {
                "session": "20269",
                "selections": [
                    {"course_code": "CSC108H1", "section_names": ["LEC0201"]},
                    {"course_code": "CSC108H1", "section_names": ["TUT0101"]},
                ],
            },
        )

    assert route.call_count == 1
    assert result.data["checked_section_count"] == 2
    assert result.data["has_conflicts"] is True


@respx.mock
@pytest.mark.asyncio
async def test_check_conflicts_partial_failure_and_missing_section(
    configured: None,
) -> None:
    csc = _load("course_csc108.json")
    respx.get(url__regex=r".*/getCoursesByCodeAndSectionCode/CSC108H1$").mock(
        return_value=httpx.Response(200, json=csc)
    )
    respx.get(url__regex=r".*/getCoursesByCodeAndSectionCode/MAT137Y1$").mock(
        side_effect=httpx.ReadTimeout("timeout")
    )

    async with Client(mcp) as client:
        result = await client.call_tool(
            "check_conflicts",
            {
                "session": "20269",
                "selections": [
                    {
                        "course_code": "CSC108H1",
                        "section_names": ["LEC0201", "TUT9999"],
                    },
                    {
                        "course_code": "MAT137Y1",
                        "section_names": ["LEC0101"],
                    },
                ],
            },
        )

    data = result.data
    assert data["is_complete"] is False
    reasons = {
        (item["course_code"], item.get("section_name")) for item in data["unresolved"]
    }
    assert ("MAT137Y1", None) in reasons
    assert ("CSC108H1", "TUT9999") in reasons
    assert data["checked_section_count"] == 1


@respx.mock
@pytest.mark.asyncio
async def test_check_conflicts_ambiguous_cancelled_and_non_weekly(
    configured: None,
) -> None:
    ambiguous = _course_envelope(
        [
            {
                "id": "1",
                "code": "CSC108H1",
                "name": "Intro",
                "sectionCode": "F",
                "campus": "St. George",
                "sessions": ["20269"],
                "sections": [
                    _minimal_section(
                        "LEC0101",
                        day=1,
                        start_ms=36_000_000,
                        end_ms=39_600_000,
                    )
                ],
                "department": {"code": "CSC", "name": "CSC"},
                "faculty": {"code": "ARTSC", "name": "Arts"},
                "title": "Intro",
                "notes": [],
                "cancelInd": "N",
            },
            {
                "id": "2",
                "code": "CSC108H1",
                "name": "Intro",
                "sectionCode": "Y",
                "campus": "St. George",
                "sessions": ["20269-20271"],
                "sections": [
                    _minimal_section(
                        "LEC0101",
                        day=1,
                        start_ms=43_200_000,
                        end_ms=46_800_000,
                    )
                ],
                "department": {"code": "CSC", "name": "CSC"},
                "faculty": {"code": "ARTSC", "name": "Arts"},
                "title": "Intro",
                "notes": [],
                "cancelInd": "N",
            },
        ]
    )
    other = _course_envelope(
        [
            {
                "id": "3",
                "code": "STA256H1",
                "name": "Stats",
                "sectionCode": "F",
                "campus": "St. George",
                "sessions": ["20269"],
                "sections": [
                    _minimal_section(
                        "LEC0201",
                        day=2,
                        start_ms=36_000_000,
                        end_ms=39_600_000,
                        cancel_ind="Y",
                    ),
                    _minimal_section(
                        "PRA0101",
                        day=5,
                        start_ms=36_000_000,
                        end_ms=39_600_000,
                        repetition="BIWEEKLY",
                    ),
                ],
                "department": {"code": "STA", "name": "Stats"},
                "faculty": {"code": "ARTSC", "name": "Arts"},
                "title": "Stats",
                "notes": [],
                "cancelInd": "N",
            }
        ]
    )
    respx.get(url__regex=r".*/getCoursesByCodeAndSectionCode/CSC108H1$").mock(
        return_value=httpx.Response(200, json=ambiguous)
    )
    respx.get(url__regex=r".*/getCoursesByCodeAndSectionCode/STA256H1$").mock(
        return_value=httpx.Response(200, json=other)
    )

    async with Client(mcp) as client:
        result = await client.call_tool(
            "check_conflicts",
            {
                "session": "20269",
                "selections": [
                    {"course_code": "CSC108H1", "section_names": ["LEC0101"]},
                    {
                        "course_code": "STA256H1",
                        "section_names": ["LEC0201", "PRA0101"],
                    },
                ],
            },
        )

    data = result.data
    assert data["is_complete"] is False
    assert any(
        item["course_code"] == "CSC108H1"
        and item["section_name"] == "LEC0101"
        and "ambiguous" in item["reason"].lower()
        for item in data["unresolved"]
    )
    assert "STA256H1 LEC0201" in data["cancelled_sections"]
    assert any(item["section_name"] == "PRA0101" for item in data["unchecked_meetings"])


@respx.mock
@pytest.mark.asyncio
async def test_end_to_end_fixture_flow(configured: None) -> None:
    respx.get(f"{BASE_URL}/reference-data").mock(
        return_value=httpx.Response(200, json=_load("reference_data.json"))
    )
    respx.post(f"{BASE_URL}/getPageableCourses").mock(
        return_value=httpx.Response(200, json=_load("search_csc108.json"))
    )
    course_fixture = _load("course_csc108.json")
    respx.get(url__regex=r".*/getCoursesByCodeAndSectionCode/CSC108H1$").mock(
        return_value=httpx.Response(200, json=course_fixture)
    )

    async with Client(mcp) as client:
        reference = await client.call_tool("get_reference_data", {})
        search = await client.call_tool(
            "search_courses",
            {
                "query": "CSC108H1",
                "sessions": ["20269"],
                "divisions": ["ARTSC"],
            },
        )
        details = await client.call_tool(
            "get_course_details",
            {"course_code": "CSC108H1", "session": "20269"},
        )
        conflicts = await client.call_tool(
            "check_conflicts",
            {
                "session": "20269",
                "selections": [
                    {
                        "course_code": "CSC108H1",
                        "section_names": ["LEC0201", "TUT0101"],
                    }
                ],
            },
        )

    assert any(item["value"] == "20269" for item in reference.data["sessions"])
    assert search.data["courses"][0]["code"] == "CSC108H1"
    assert details.data["found"] is True
    assert conflicts.data["has_conflicts"] is True
