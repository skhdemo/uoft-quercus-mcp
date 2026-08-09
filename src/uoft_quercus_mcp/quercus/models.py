"""Pydantic models for Quercus tool inputs and outputs."""

from __future__ import annotations

import re
from typing import Any, Literal

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


class ListTodoInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    include_todo: bool = True
    include_upcoming: bool = True


class CourseScopedInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    course: str = Field(min_length=1, max_length=200)
    include_concluded: bool = False

    @field_validator("course")
    @classmethod
    def _normalize_course(cls, value: str) -> str:
        return _reject_control_chars(value.strip())


class ListModulesInput(CourseScopedInput):
    include_items: bool = True


class ListFilesInput(CourseScopedInput):
    search: str | None = None

    @field_validator("search")
    @classmethod
    def _normalize_search(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = _reject_control_chars(value.strip())
        return cleaned or None


class GetFileInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    file: str = Field(min_length=1, max_length=400)
    course: str | None = None
    mode: Literal["metadata", "text", "download"] = "text"
    include_concluded: bool = False

    @field_validator("file")
    @classmethod
    def _normalize_file(cls, value: str) -> str:
        return _reject_control_chars(value.strip())

    @field_validator("course")
    @classmethod
    def _normalize_course(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = _reject_control_chars(value.strip())
        return cleaned or None
