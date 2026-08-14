"""Unit tests for Quercus payload normalization."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from uoft_quercus_mcp.quercus.normalize import normalize_course, normalize_whoami

FIXTURES = Path(__file__).parent / "fixtures" / "quercus"


def _load(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text())


def test_normalize_whoami_keeps_stable_fields() -> None:
    raw = _load("users_self.json")
    result = normalize_whoami(raw)

    assert result == {
        "id": 12345,
        "name": "Jane Student",
        "sortable_name": "Student, Jane",
        "short_name": "Jane",
        "login_id": "jane.student@mail.utoronto.ca",
    }
    assert "permissions" not in result
    assert "locale" not in result


def test_normalize_course_drops_calendar_and_keeps_term() -> None:
    raw = _load("courses_page1.json")[0]
    result = normalize_course(raw)

    assert result["id"] == 1001
    assert result["name"] == "Introduction to Computer Science I"
    assert result["course_code"] == "CSCA08H3"
    assert result["enrollment_term_id"] == 55
    assert result["workflow_state"] == "available"
    assert result["term"] == {
        "id": 55,
        "name": "Fall 2026",
        "start_at": "2026-09-01T00:00:00Z",
        "end_at": "2026-12-31T00:00:00Z",
    }
    assert "calendar" not in result
    assert "enrollments" not in result


def test_normalize_file_omits_download_url() -> None:
    from uoft_quercus_mcp.quercus.normalize import normalize_file

    raw = _load("file_metadata.json")
    result = normalize_file(raw)
    assert result["id"] == 801
    assert result["display_name"] == "quiz2.pdf"
    assert "url" not in result
    assert "verifier" not in str(result)
