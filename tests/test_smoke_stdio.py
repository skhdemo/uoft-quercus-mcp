"""Transport smoke test: start the package over stdio and list tools."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest
from fastmcp import Client
from fastmcp.client.transports import StdioTransport

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_STDIO_TIMEOUT_SECONDS = 20.0


@pytest.mark.asyncio
async def test_stdio_lists_tools() -> None:
    """Packaging and entry point work: initialize, list tools, exit cleanly."""
    transport = StdioTransport(
        command=sys.executable,
        args=["-m", "uoft_timetable_mcp"],
        cwd=str(_PROJECT_ROOT),
        env={
            "VIRTUAL_ENV": str(_PROJECT_ROOT / ".venv"),
        },
        keep_alive=False,
    )

    async def _list_tools() -> list[str]:
        async with Client(transport) as client:
            tools = await client.list_tools()
            return [tool.name for tool in tools]

    tool_names = await asyncio.wait_for(
        _list_tools(),
        timeout=_STDIO_TIMEOUT_SECONDS,
    )

    assert isinstance(tool_names, list)
    assert sorted(tool_names) == [
        "get_course_details",
        "get_reference_data",
        "search_courses",
    ]
