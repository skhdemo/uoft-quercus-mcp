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
async def test_live_whoami_and_list_courses() -> None:
    """Broad invariants only — do not assert specific course codes."""
    settings = QuercusSettings.from_env()
    if not os.environ.get(settings.access_token_env, "").strip():
        pytest.skip("QUERCUS_ACCESS_TOKEN is not set")

    async with QuercusClient(
        settings,
        auth=PersonalTokenAuth.from_env(settings),
    ) as client:
        raw_user = await client.get_self()
        user = normalize_whoami(raw_user)
        assert user.get("id") is not None
        assert user.get("name")

        raw_courses = await client.list_courses()
        courses = [normalize_course(item) for item in raw_courses]

    assert isinstance(courses, list)
    for course in courses:
        assert course.get("id") is not None
