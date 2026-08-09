"""Optional live smoke test against Quercus.

Excluded by default. Run explicitly with:

    uv run pytest -m live_quercus
"""

from __future__ import annotations

import os

import pytest

from uoft_timetable_mcp.quercus.auth import PersonalTokenAuth
from uoft_timetable_mcp.quercus.client import QuercusClient
from uoft_timetable_mcp.quercus.normalize import normalize_course, normalize_whoami
from uoft_timetable_mcp.quercus.settings import QuercusSettings


@pytest.mark.live_quercus
@pytest.mark.asyncio
async def test_live_whoami_courses_and_light_lists() -> None:
    """Broad invariants only — do not assert specific course codes or files."""
    settings = QuercusSettings.from_env()
    if not os.environ.get(settings.access_token_env, "").strip():
        pytest.skip("QUERCUS_ACCESS_TOKEN is not set")

    async with QuercusClient(
        settings,
        auth=PersonalTokenAuth.from_env(settings),
    ) as client:
        user = normalize_whoami(await client.get_self())
        assert user.get("id") is not None
        assert user.get("name")

        courses = [normalize_course(item) for item in await client.list_courses()]
        todo = await client.list_todo()
        upcoming = await client.list_upcoming_events()
        assert isinstance(todo, list)
        assert isinstance(upcoming, list)

        if courses:
            course_id = courses[0]["id"]
            assignments = await client.list_assignments(course_id)
            modules = await client.list_modules(course_id, include_items=True)
            assert isinstance(assignments, list)
            assert isinstance(modules, list)

    for course in courses:
        assert course.get("id") is not None
