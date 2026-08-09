"""Pydantic models for Quercus Phase 1 tool inputs and outputs."""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

_CONTROL_CHARS_RE = re.compile(r"[\x00-\x1f\x7f]")


def _reject_control_chars(value: str) -> str:
    if _CONTROL_CHARS_RE.search(value):
        msg = "Control characters are not allowed."
        raise ValueError(msg)
    return value


class QuercusWhoamiResult(BaseModel):
    """Normalized output of ``quercus_whoami``."""

    model_config = ConfigDict(extra="forbid")

    id: int | str | None = None
    name: str | None = None
    sortable_name: str | None = None
    short_name: str | None = None
    login_id: str | None = None


class QuercusCourse(BaseModel):
    """Normalized course summary from ``quercus_list_courses``."""

    model_config = ConfigDict(extra="forbid")

    id: int | str | None = None
    name: str | None = None
    course_code: str | None = None
    enrollment_term_id: int | str | None = None
    term: dict[str, Any] | None = None
    workflow_state: str | None = None


class ListCoursesInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enrollment_state: str = Field(default="active", max_length=64)
    include_concluded: bool = False

    @field_validator("enrollment_state")
    @classmethod
    def _normalize_enrollment_state(cls, value: str) -> str:
        cleaned = _reject_control_chars(value.strip())
        if not cleaned:
            msg = "enrollment_state must not be empty."
            raise ValueError(msg)
        return cleaned


class ListCoursesResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    courses: list[QuercusCourse]
    count: int
