"""Pydantic models for tool inputs and normalized public outputs."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class ReferenceOption(BaseModel):
    """A selectable reference-data option exposed to MCP clients."""

    model_config = ConfigDict(extra="forbid")

    label: str
    value: str
    group: str | None = None


class ReferenceData(BaseModel):
    """Normalized output of `get_reference_data`."""

    model_config = ConfigDict(extra="forbid")

    sessions: list[ReferenceOption]
    divisions: list[ReferenceOption]
    campuses: list[ReferenceOption]
    delivery_modes: list[ReferenceOption]
    course_levels: list[ReferenceOption]
    fetched_at: datetime


class DeliveryMode(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session: str
    mode: str


class Meeting(BaseModel):
    """Normalized meeting time used by course details and conflict checks."""

    model_config = ConfigDict(extra="forbid")

    day: str
    day_number: int | None
    start: str | None
    end: str | None
    start_minutes: int | None
    end_minutes: int | None
    location: str | None
    building_code: str | None
    room: str | None
    building_url: str | None
    session_code: str | None
    repetition: str | None


class Section(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    type: str | None = None
    teaching_method: str | None = None
    section_number: str | None = None
    meetings: list[Meeting] = Field(default_factory=list)
    instructors: list[str] = Field(default_factory=list)
    delivery_modes: list[DeliveryMode] = Field(default_factory=list)
    current_enrolment: int | None = None
    max_enrolment: int | None = None
    available_space: int | None = None
    current_waitlist: int | None = None
    waitlist_allowed: bool | None = None
    enrolment_indicator: str | None = None
    cancelled: bool | None = None
    tba: bool | None = None
    notes: list[str] = Field(default_factory=list)


class Course(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str | int | None = None
    code: str
    name: str | None = None
    section_code: str | None = None
    campus: str | None = None
    sessions: list[str] = Field(default_factory=list)
    division: str | None = None
    department: str | None = None
    description: str | None = None
    prerequisites: str | None = None
    corequisites: str | None = None
    exclusions: str | None = None
    breadth_requirements: list[str] = Field(default_factory=list)
    distribution_requirements: list[str] = Field(default_factory=list)
    sections: list[Section] = Field(default_factory=list)
    fetched_at: datetime


class CourseSearchItem(BaseModel):
    """Concise course summary returned by `search_courses`."""

    model_config = ConfigDict(extra="forbid")

    id: str | int | None = None
    code: str
    name: str | None = None
    section_code: str | None = None
    campus: str | None = None
    sessions: list[str] = Field(default_factory=list)
    section_count: int


class SearchCoursesResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    courses: list[CourseSearchItem]
    page: int
    page_size: int
    total: int
    has_next_page: bool
    fetched_at: datetime


class CourseDetailsResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    courses: list[Course]
    found: bool
    fetched_at: datetime


class SearchCoursesInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(default="", max_length=200)
    sessions: list[str]
    divisions: list[str]
    campuses: list[str] = Field(default_factory=list)
    instructor: str | None = None
    course_levels: list[str] = Field(default_factory=list)
    delivery_modes: list[str] = Field(default_factory=list)
    available_space_only: bool = False
    waitlistable_only: bool = False
    search_description: bool = False
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=10, ge=1, le=50)

    @field_validator("query")
    @classmethod
    def _trim_query(cls, value: str) -> str:
        return value.strip()

    @field_validator(
        "sessions", "divisions", "campuses", "course_levels", "delivery_modes"
    )
    @classmethod
    def _dedupe_lists(cls, values: list[str]) -> list[str]:
        seen: set[str] = set()
        ordered: list[str] = []
        for item in values:
            if item in seen:
                continue
            seen.add(item)
            ordered.append(item)
        return ordered

    @model_validator(mode="after")
    def _require_session_and_division(self) -> SearchCoursesInput:
        if not self.sessions:
            msg = "At least one session is required."
            raise ValueError(msg)
        if not self.divisions:
            msg = "At least one division is required."
            raise ValueError(msg)
        return self


class CourseDetailsInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    course_code: str = Field(min_length=1, max_length=32)
    session: str = Field(min_length=1)
    section_code: str | None = None

    @field_validator("course_code")
    @classmethod
    def _normalize_course_code(cls, value: str) -> str:
        return value.strip().upper()

    @field_validator("section_code")
    @classmethod
    def _normalize_section_code(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip().upper()
        return normalized or None


class SectionSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    course_code: str = Field(min_length=1, max_length=32)
    section_names: list[str] = Field(min_length=1, max_length=10)

    @field_validator("course_code")
    @classmethod
    def _normalize_course_code(cls, value: str) -> str:
        return value.strip().upper()

    @field_validator("section_names")
    @classmethod
    def _normalize_section_names(cls, values: list[str]) -> list[str]:
        seen: set[str] = set()
        ordered: list[str] = []
        for item in values:
            name = item.strip()
            if not name:
                continue
            key = name.upper()
            if key in seen:
                continue
            seen.add(key)
            ordered.append(name)
        if not ordered:
            msg = "At least one section name is required."
            raise ValueError(msg)
        return ordered


class CheckConflictsInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session: str = Field(min_length=1)
    selections: list[SectionSelection] = Field(min_length=1, max_length=20)
    minimum_transition_minutes: int = Field(default=0, ge=0, le=180)

    @model_validator(mode="after")
    def _dedupe_courses(self) -> CheckConflictsInput:
        seen: set[str] = set()
        ordered: list[SectionSelection] = []
        for selection in self.selections:
            if selection.course_code in seen:
                continue
            seen.add(selection.course_code)
            ordered.append(selection)
        self.selections = ordered
        return self


class ConflictRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: str = "overlap"
    day: str
    section_a: str
    section_b: str
    meeting_a: str
    meeting_b: str
    overlap_start: str
    overlap_end: str
    overlap_minutes: int


class TransitionViolation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: str = "transition"
    day: str
    section_a: str
    section_b: str
    meeting_a: str
    meeting_b: str
    gap_minutes: int
    required_minutes: int


class UnresolvedSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    course_code: str
    section_name: str | None = None
    reason: str


class UncheckedMeeting(BaseModel):
    model_config = ConfigDict(extra="forbid")

    course_code: str
    section_name: str
    reason: str
    meeting: Meeting | None = None


class CheckConflictsResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    has_conflicts: bool
    is_complete: bool
    conflicts: list[ConflictRecord] = Field(default_factory=list)
    transition_violations: list[TransitionViolation] = Field(default_factory=list)
    unresolved: list[UnresolvedSelection] = Field(default_factory=list)
    unchecked_meetings: list[UncheckedMeeting] = Field(default_factory=list)
    cancelled_sections: list[str] = Field(default_factory=list)
    checked_section_count: int = 0
    fetched_at: datetime


def model_to_public_dict(model: BaseModel) -> dict[str, Any]:
    """Serialize a model for MCP responses with ISO-8601 UTC timestamps."""
    return model.model_dump(mode="json")
