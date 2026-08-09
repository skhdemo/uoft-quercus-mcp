"""In-memory MCP tests for Quercus Phase 1 tools."""

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
def quercus_settings() -> QuercusSettings:
    return QuercusSettings(
        base_url="https://q.utoronto.ca",
        api_prefix="/api/v1",
        connect_timeout_seconds=1.0,
        read_timeout_seconds=1.0,
        max_attempts=1,
        max_page_size=100,
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
    configure_runtime(
        client=timetable_client,
        settings=timetable_settings,
        clock=lambda: datetime(2026, 7, 27, 1, 0, 0, tzinfo=UTC),
        owns_client=False,
        quercus_client=quercus_client,
        quercus_settings=quercus_settings,
        quercus_auth=PersonalTokenAuth(TOKEN),
        owns_quercus_client=False,
    )
    try:
        yield None
    finally:
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


@respx.mock
@pytest.mark.asyncio
async def test_tool_inventory_includes_timetable_and_quercus(
    configured: None,
) -> None:
    async with Client(mcp) as client:
        tools = await client.list_tools()
    names = {tool.name for tool in tools}
    assert names == EXPECTED_TOOLS


@respx.mock
@pytest.mark.asyncio
async def test_quercus_whoami_returns_normalized_user(configured: None) -> None:
    respx.get(f"{QUERCUS_API_ROOT}/users/self").mock(
        return_value=httpx.Response(200, json=_load_quercus("users_self.json"))
    )

    async with Client(mcp) as client:
        result = await client.call_tool("quercus_whoami", {})

    data = result.data
    assert isinstance(data, dict)
    assert data["id"] == 12345
    assert data["name"] == "Jane Student"
    assert data["login_id"] == "jane.student@mail.utoronto.ca"
    assert "permissions" not in data


@respx.mock
@pytest.mark.asyncio
async def test_quercus_list_courses_returns_courses_and_count(
    configured: None,
) -> None:
    respx.get(f"{QUERCUS_API_ROOT}/courses").mock(
        return_value=httpx.Response(200, json=_load_quercus("courses_page1.json"))
    )

    async with Client(mcp) as client:
        result = await client.call_tool("quercus_list_courses", {})

    data = result.data
    assert isinstance(data, dict)
    assert data["count"] == 2
    assert len(data["courses"]) == 2
    first = data["courses"][0]
    assert first["id"] == 1001
    assert first["course_code"] == "CSCA08H3"
    assert first["name"] == "Introduction to Computer Science I"
    assert "calendar" not in first


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
    assert payload["error"]["retryable"] is False
    assert TOKEN not in payload["error"]["message"]


@respx.mock
@pytest.mark.asyncio
async def test_401_returns_auth_rejected(configured: None) -> None:
    respx.get(f"{QUERCUS_API_ROOT}/users/self").mock(
        return_value=httpx.Response(401, json={"errors": [{"message": "Invalid"}]})
    )

    async with Client(mcp) as client:
        with pytest.raises(ToolError) as exc_info:
            await client.call_tool("quercus_whoami", {})

    payload = _parse_tool_error(exc_info.value)
    assert payload["error"]["code"] == "quercus_auth_rejected"
    assert TOKEN not in payload["error"]["message"]


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
        tools = await client.list_tools()
        names = {tool.name for tool in tools}
        assert "get_reference_data" in names
        assert "quercus_whoami" in names
        result = await client.call_tool("get_reference_data", {})

    data = result.data
    assert isinstance(data, dict)
    assert any(item["value"] == "ARTSC" for item in data["divisions"])
