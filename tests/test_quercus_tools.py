"""In-memory MCP tests for Quercus Phase 1–2 tools."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from fastmcp import Client
from fastmcp.exceptions import ToolError

from uoft_timetable_mcp.quercus.auth import PersonalTokenAuth
from uoft_timetable_mcp.quercus.client import QuercusClient
from uoft_timetable_mcp.quercus.resolve import CourseListCache
from uoft_timetable_mcp.quercus.settings import QuercusSettings
from uoft_timetable_mcp.server import configure_runtime, mcp, reset_runtime
from uoft_timetable_mcp.timetable.client import TimetableClient
from uoft_timetable_mcp.timetable.settings import Settings

FIXTURES = Path(__file__).parent / "fixtures"
QUERCUS_FIXTURES = FIXTURES / "quercus"
TTB_BASE_URL = "https://api.easi.utoronto.ca/ttb"
QUERCUS_API_ROOT = "https://q.utoronto.ca/api/v1"
TOKEN = "test-token"

EXPECTED_TOOLS = {
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


def _load_quercus(name: str) -> Any:
    return json.loads((QUERCUS_FIXTURES / name).read_text())


def _load_ttb(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / name).read_text())


def _parse_tool_error(exc: ToolError) -> dict[str, Any]:
    payload = json.loads(str(exc))
    assert "error" in payload
    return payload


@pytest.fixture
def timetable_settings() -> Settings:
    return Settings(
        base_url=TTB_BASE_URL,
        connect_timeout_seconds=1.0,
        read_timeout_seconds=1.0,
        reference_cache_ttl_seconds=900,
        max_attempts=1,
        version="0.1.0",
    )


@pytest.fixture
def quercus_settings(tmp_path: Path) -> QuercusSettings:
    return QuercusSettings(
        base_url="https://q.utoronto.ca",
        api_prefix="/api/v1",
        connect_timeout_seconds=1.0,
        read_timeout_seconds=1.0,
        max_attempts=1,
        max_page_size=100,
        download_dir=str(tmp_path / "downloads"),
        course_cache_ttl_seconds=120,
        version="0.1.0",
    )


@pytest.fixture
async def timetable_client(
    timetable_settings: Settings,
) -> AsyncIterator[TimetableClient]:
    client = TimetableClient(timetable_settings)
    try:
        yield client
    finally:
        await client.aclose()


@pytest.fixture
async def quercus_client(
    quercus_settings: QuercusSettings,
) -> AsyncIterator[QuercusClient]:
    client = QuercusClient(quercus_settings, auth=PersonalTokenAuth(TOKEN))
    try:
        yield client
    finally:
        await client.aclose()


@pytest.fixture
async def configured(
    timetable_client: TimetableClient,
    timetable_settings: Settings,
    quercus_client: QuercusClient,
    quercus_settings: QuercusSettings,
) -> AsyncIterator[None]:
    cache = CourseListCache(ttl_seconds=120, clock=lambda: datetime.now(UTC))
    configure_runtime(
        client=timetable_client,
        settings=timetable_settings,
        clock=lambda: datetime(2026, 7, 27, 1, 0, 0, tzinfo=UTC),
        owns_client=False,
        quercus_client=quercus_client,
        quercus_settings=quercus_settings,
        quercus_auth=PersonalTokenAuth(TOKEN),
        owns_quercus_client=False,
        quercus_course_cache=cache,
    )
    try:
        yield None
    finally:
        cache.clear()
        reset_runtime()


@pytest.fixture
async def configured_without_token(
    timetable_client: TimetableClient,
    timetable_settings: Settings,
    quercus_settings: QuercusSettings,
) -> AsyncIterator[None]:
    quercus = QuercusClient(quercus_settings, auth=PersonalTokenAuth(None))
    configure_runtime(
        client=timetable_client,
        settings=timetable_settings,
        clock=lambda: datetime(2026, 7, 27, 1, 0, 0, tzinfo=UTC),
        owns_client=False,
        quercus_client=quercus,
        quercus_settings=quercus_settings,
        quercus_auth=PersonalTokenAuth(None),
        owns_quercus_client=False,
    )
    try:
        yield None
    finally:
        reset_runtime()
        await quercus.aclose()


def _mock_courses() -> None:
    respx.get(f"{QUERCUS_API_ROOT}/courses").mock(
        return_value=httpx.Response(200, json=_load_quercus("courses_page1.json"))
    )


@respx.mock
@pytest.mark.asyncio
async def test_tool_inventory_includes_phase2(configured: None) -> None:
    async with Client(mcp) as client:
        tools = await client.list_tools()
    assert {tool.name for tool in tools} == EXPECTED_TOOLS


@respx.mock
@pytest.mark.asyncio
async def test_quercus_whoami_returns_normalized_user(configured: None) -> None:
    respx.get(f"{QUERCUS_API_ROOT}/users/self").mock(
        return_value=httpx.Response(200, json=_load_quercus("users_self.json"))
    )
    async with Client(mcp) as client:
        result = await client.call_tool("quercus_whoami", {})
    assert result.data["id"] == 12345


@respx.mock
@pytest.mark.asyncio
async def test_list_assignments_resolves_fragment(configured: None) -> None:
    _mock_courses()
    respx.get(f"{QUERCUS_API_ROOT}/courses/1002/assignments").mock(
        return_value=httpx.Response(200, json=_load_quercus("assignments.json"))
    )
    async with Client(mcp) as client:
        result = await client.call_tool(
            "quercus_list_assignments",
            {"course": "MATA22"},
        )
    data = result.data
    assert data["course"]["id"] == 1002
    assert data["count"] == 2


@respx.mock
@pytest.mark.asyncio
async def test_list_announcements_uses_term_window(configured: None) -> None:
    _mock_courses()
    route = respx.get(f"{QUERCUS_API_ROOT}/announcements").mock(
        return_value=httpx.Response(200, json=_load_quercus("announcements.json"))
    )
    async with Client(mcp) as client:
        result = await client.call_tool(
            "quercus_list_announcements",
            {"course": "MATA22"},
        )
    url = str(route.calls.last.request.url)
    assert "start_date=2026-09-01" in url
    assert "end_date=2026-07-27" in url
    assert result.data["window"] == {
        "start_date": "2026-09-01",
        "end_date": "2026-07-27",
        "start_source": "term_start_at",
    }
    assert result.data["count"] == 1


@respx.mock
@pytest.mark.asyncio
async def test_list_courses_warms_cache_for_resolve(configured: None) -> None:
    route = respx.get(f"{QUERCUS_API_ROOT}/courses").mock(
        return_value=httpx.Response(200, json=_load_quercus("courses_page1.json"))
    )
    respx.get(f"{QUERCUS_API_ROOT}/courses/1002/assignments").mock(
        return_value=httpx.Response(200, json=_load_quercus("assignments.json"))
    )
    async with Client(mcp) as client:
        await client.call_tool("quercus_list_courses", {})
        assert route.call_count == 1
        result = await client.call_tool(
            "quercus_list_assignments",
            {"course": "MATA22"},
        )
    assert route.call_count == 1
    assert result.data["course"]["id"] == 1002


@respx.mock
@pytest.mark.asyncio
async def test_ambiguous_course_tool_error(configured: None) -> None:
    combined = _load_quercus("courses_page1.json") + _load_quercus("courses_page2.json")
    respx.get(f"{QUERCUS_API_ROOT}/courses").mock(
        return_value=httpx.Response(200, json=combined)
    )
    async with Client(mcp) as client:
        with pytest.raises(ToolError) as exc_info:
            await client.call_tool("quercus_list_assignments", {"course": "MAT"})
    payload = _parse_tool_error(exc_info.value)
    assert payload["error"]["code"] == "quercus_ambiguous"
    assert "candidates" in payload["error"]


@respx.mock
@pytest.mark.asyncio
async def test_list_modules_exposes_file_id(configured: None) -> None:
    _mock_courses()
    respx.get(f"{QUERCUS_API_ROOT}/courses/1002/modules").mock(
        return_value=httpx.Response(200, json=_load_quercus("modules.json"))
    )
    async with Client(mcp) as client:
        result = await client.call_tool(
            "quercus_list_modules",
            {"course": "MATA22"},
        )
    item = result.data["modules"][0]["items"][0]
    assert item["type"] == "File"
    assert item["file_id"] == 801


@respx.mock
@pytest.mark.asyncio
async def test_get_file_modes(
    configured: None,
    quercus_settings: QuercusSettings,
) -> None:
    meta = _load_quercus("file_metadata.json")
    pdf = (QUERCUS_FIXTURES / "sample.pdf").read_bytes()
    respx.get(f"{QUERCUS_API_ROOT}/files/801").mock(
        return_value=httpx.Response(200, json=meta)
    )
    respx.get(meta["url"]).mock(
        return_value=httpx.Response(
            200,
            content=pdf,
            headers={"Content-Type": "application/pdf"},
        )
    )

    async with Client(mcp) as client:
        meta_result = await client.call_tool(
            "quercus_get_file",
            {"file": "801", "mode": "metadata"},
        )
        text_result = await client.call_tool(
            "quercus_get_file",
            {"file": "801", "mode": "text"},
        )
        download_result = await client.call_tool(
            "quercus_get_file",
            {"file": "801", "mode": "download"},
        )

    assert meta_result.data["mode"] == "metadata"
    assert meta_result.data["file"]["id"] == 801
    assert text_result.data["mode"] == "text"
    assert text_result.data["text"] is not None
    assert "Hello Quercus PDF" in text_result.data["text"]
    path = Path(download_result.data["path"])
    assert path.exists()
    assert path.read_bytes() == pdf
    assert str(quercus_settings.resolved_download_dir) in str(path)


@respx.mock
@pytest.mark.asyncio
async def test_list_todo(configured: None) -> None:
    respx.get(f"{QUERCUS_API_ROOT}/users/self/todo").mock(
        return_value=httpx.Response(200, json=_load_quercus("todo.json"))
    )
    respx.get(f"{QUERCUS_API_ROOT}/users/self/upcoming_events").mock(
        return_value=httpx.Response(200, json=_load_quercus("upcoming_events.json"))
    )
    async with Client(mcp) as client:
        result = await client.call_tool("quercus_list_todo", {})
    assert result.data["todo_count"] == 1
    assert result.data["upcoming_count"] == 1


@respx.mock
@pytest.mark.asyncio
async def test_missing_token_returns_auth_missing(
    configured_without_token: None,
) -> None:
    async with Client(mcp) as client:
        with pytest.raises(ToolError) as exc_info:
            await client.call_tool("quercus_whoami", {})
    payload = _parse_tool_error(exc_info.value)
    assert payload["error"]["code"] == "quercus_auth_missing"


@respx.mock
@pytest.mark.asyncio
async def test_timetable_tools_still_work_with_quercus_unset(
    configured_without_token: None,
) -> None:
    fixture = _load_ttb("reference_data.json")
    respx.get(f"{TTB_BASE_URL}/reference-data").mock(
        return_value=httpx.Response(200, json=fixture)
    )
    async with Client(mcp) as client:
        result = await client.call_tool("get_reference_data", {})
    assert any(item["value"] == "ARTSC" for item in result.data["divisions"])
