"""Optional live smoke test against the Timetable Builder API.

Excluded by default. Run explicitly with:

    uv run pytest -m live
"""

from __future__ import annotations

import pytest

from uoft_timetable_mcp.client import TimetableClient
from uoft_timetable_mcp.normalize import normalize_reference_options
from uoft_timetable_mcp.settings import Settings


@pytest.mark.live
@pytest.mark.asyncio
async def test_live_reference_and_small_search() -> None:
    """Broad invariants only — do not assert specific rooms or enrolment."""
    settings = Settings.from_env()
    async with TimetableClient(settings) as client:
        payload = await client.get_reference_data()
        sessions = normalize_reference_options(payload.get("currentSessions"))
        divisions = normalize_reference_options(payload.get("divisions"))

        assert sessions, "Expected at least one selectable session from live API"
        assert divisions, "Expected at least one selectable division from live API"

        # Prefer a currently advertised Fall/Winter-style session when present.
        session = next(
            (item.value for item in sessions if item.value.isdigit()),
            sessions[0].value,
        )
        division = next(
            (item.value for item in divisions if item.value == "ARTSC"),
            divisions[0].value,
        )

        pageable = await client.search_courses(
            sessions=[session],
            divisions=[division],
            course_code="CSC108H1",
            page=1,
            page_size=1,
        )

    assert isinstance(pageable["total"], int)
    assert pageable["total"] >= 0
    assert isinstance(pageable["courses"], list)
    # Upstream may not always honor pageSize strictly; only require a small page.
    assert len(pageable["courses"]) <= 10
    for course in pageable["courses"]:
        assert isinstance(course.get("code"), str)
        assert course["code"]
