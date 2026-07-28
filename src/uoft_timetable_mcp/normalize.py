"""Convert upstream Timetable Builder payloads into stable public models."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

from uoft_timetable_mcp.models import (
    Course,
    CourseSearchItem,
    DeliveryMode,
    Meeting,
    ReferenceData,
    ReferenceOption,
    Section,
)

# Upstream day numbers observed in Timetable Builder payloads / fixtures.
# 1 = Monday ... 7 = Sunday. Unknown values must not be treated as conflict-checkable.
_DAY_NAMES: dict[int, str] = {
    1: "Monday",
    2: "Tuesday",
    3: "Wednesday",
    4: "Thursday",
    5: "Friday",
    6: "Saturday",
    7: "Sunday",
}

_MAX_MILLIS_OF_DAY = 86_400_000
_COMBINED_SESSION_RE = re.compile(r"^(\d{5})-(\d{5})$")
_COURSE_CODE_QUERY_RE = re.compile(r"^[A-Z]{2,4}\d{3}[A-Z0-9]*$")


def utc_now() -> datetime:
    """Return the current UTC time (injectable seam for tests)."""
    return datetime.now(UTC)


def day_name(day_number: Any) -> tuple[str, int | None]:
    """Map an upstream day number to a weekday name.

    Returns ``("Unknown", None)`` for unrecognized values.
    """
    if isinstance(day_number, bool) or not isinstance(day_number, int):
        return "Unknown", None
    name = _DAY_NAMES.get(day_number)
    if name is None:
        return "Unknown", None
    return name, day_number


def millis_to_minutes(millisofday: Any) -> int | None:
    """Convert upstream ``millisofday`` to minutes after midnight.

    Values outside ``0..86_400_000`` are rejected (not wrapped).
    """
    if isinstance(millisofday, bool) or not isinstance(millisofday, int):
        return None
    if millisofday < 0 or millisofday > _MAX_MILLIS_OF_DAY:
        return None
    return millisofday // 60_000


def format_hhmm(minutes_after_midnight: int | None) -> str | None:
    """Format minutes after midnight as ``HH:MM``."""
    if minutes_after_midnight is None:
        return None
    hours, minutes = divmod(minutes_after_midnight, 60)
    return f"{hours:02d}:{minutes:02d}"


def normalize_yn_flag(value: Any) -> bool | None:
    """Convert upstream ``Y``/``N`` flags. Unknown values become ``None``."""
    if value is None:
        return None
    if isinstance(value, str):
        normalized = value.strip().upper()
        if normalized == "Y":
            return True
        if normalized == "N":
            return False
        return None
    return None


def format_instructor_name(instructor: dict[str, Any] | Any) -> str | None:
    """Join non-empty instructor first/last names safely."""
    if not isinstance(instructor, dict):
        return None
    first = instructor.get("firstName")
    last = instructor.get("lastName")
    parts: list[str] = []
    if isinstance(first, str) and first.strip():
        parts.append(first.strip())
    if isinstance(last, str) and last.strip():
        parts.append(last.strip())
    if not parts:
        return None
    return " ".join(parts)


def format_location(
    building: dict[str, Any] | None,
) -> tuple[str | None, str | None, str | None, str | None]:
    """Build a clean location string from upstream building fields.

    Returns ``(location, building_code, room, building_url)``.
    """
    if not isinstance(building, dict):
        return None, None, None, None

    code = building.get("buildingCode")
    room_number = building.get("buildingRoomNumber")
    room_suffix = building.get("buildingRoomSuffix")
    url = building.get("buildingUrl")

    building_code = code.strip() if isinstance(code, str) and code.strip() else None

    room_parts: list[str] = []
    if isinstance(room_number, str):
        if room_number.strip():
            room_parts.append(room_number.strip())
    elif room_number is not None and not isinstance(room_number, bool):
        room_parts.append(str(room_number))
    if isinstance(room_suffix, str) and room_suffix.strip():
        room_parts.append(room_suffix.strip())
    room = " ".join(room_parts) if room_parts else None

    location_parts = [part for part in (building_code, room) if part]
    location = " ".join(location_parts) if location_parts else None

    building_url = url.strip() if isinstance(url, str) and url.strip() else None
    return location, building_code, room, building_url


def compute_available_space(max_enrolment: Any, current_enrolment: Any) -> int | None:
    """Compute available seats only when both enrolment values are integers."""
    if isinstance(max_enrolment, bool) or isinstance(current_enrolment, bool):
        return None
    if not isinstance(max_enrolment, int) or not isinstance(current_enrolment, int):
        return None
    return max_enrolment - current_enrolment


def as_optional_int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def as_optional_str(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def _blank_to_none(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        return str(value) if value is not None else None
    stripped = value.strip()
    return stripped or None


def session_matches(requested: str, course_sessions: list[str] | Any) -> bool:
    """Return whether a course record applies to the requested session code.

    Matching rules (§3.5):
    1. Exact membership in ``course.sessions``.
    2. Combined ranges such as ``20269-20271`` match either endpoint.
    3. Tokens like ``20265F`` are exact values only (not split on ``-``).
    """
    if not isinstance(course_sessions, list):
        return False
    for session in course_sessions:
        if not isinstance(session, str):
            continue
        if session == requested:
            return True
        match = _COMBINED_SESSION_RE.fullmatch(session)
        if match is not None and requested in match.groups():
            return True
    return False


def classify_search_query(query: str) -> tuple[str, str, bool]:
    """Classify a search query as course-code or title.

    Returns ``(course_code, course_title, is_code_query)``.
    """
    normalized = query.strip().upper()
    if _COURSE_CODE_QUERY_RE.fullmatch(normalized):
        return normalized, "", True
    return "", query.strip(), False


def normalize_reference_options(items: Any) -> list[ReferenceOption]:
    """Normalize selectable reference options, dropping UI header rows."""
    if not isinstance(items, list):
        return []
    options: list[ReferenceOption] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        if item.get("header") is True:
            continue
        label = as_optional_str(item.get("label"))
        value = as_optional_str(item.get("value"))
        if label is None or value is None:
            continue
        group = item.get("group")
        options.append(
            ReferenceOption(
                label=label,
                value=value,
                group=as_optional_str(group) if group is not None else None,
            )
        )
    return options


def normalize_reference_data(
    payload: dict[str, Any],
    *,
    fetched_at: datetime | None = None,
) -> ReferenceData:
    """Normalize a ``/reference-data`` payload object."""
    return ReferenceData(
        sessions=normalize_reference_options(payload.get("currentSessions")),
        divisions=normalize_reference_options(payload.get("divisions")),
        campuses=normalize_reference_options(payload.get("campuses")),
        delivery_modes=normalize_reference_options(payload.get("deliveryModes")),
        course_levels=normalize_reference_options(payload.get("courseLevels")),
        fetched_at=fetched_at or utc_now(),
    )


def normalize_meeting(raw: dict[str, Any]) -> Meeting:
    """Normalize one upstream meetingTimes entry."""
    start = raw.get("start") if isinstance(raw.get("start"), dict) else {}
    end = raw.get("end") if isinstance(raw.get("end"), dict) else {}

    day_label, day_number = day_name(
        start.get("day") if isinstance(start, dict) else None
    )
    # If start/end days disagree, keep start day but mark unknown for safety.
    end_day_label, end_day_number = day_name(
        end.get("day") if isinstance(end, dict) else None
    )
    if (
        day_number is not None
        and end_day_number is not None
        and day_number != end_day_number
    ):
        day_label, day_number = "Unknown", None
    elif day_number is None and end_day_number is not None:
        day_label, day_number = end_day_label, end_day_number

    start_minutes = millis_to_minutes(
        start.get("millisofday") if isinstance(start, dict) else None
    )
    end_minutes = millis_to_minutes(
        end.get("millisofday") if isinstance(end, dict) else None
    )
    location, building_code, room, building_url = format_location(
        raw.get("building") if isinstance(raw.get("building"), dict) else None
    )

    return Meeting(
        day=day_label,
        day_number=day_number,
        start=format_hhmm(start_minutes),
        end=format_hhmm(end_minutes),
        start_minutes=start_minutes,
        end_minutes=end_minutes,
        location=location,
        building_code=building_code,
        room=room,
        building_url=building_url,
        session_code=as_optional_str(raw.get("sessionCode")),
        repetition=as_optional_str(raw.get("repetition")),
    )


def _normalize_notes(raw_notes: Any) -> list[str]:
    if not isinstance(raw_notes, list):
        return []
    notes: list[str] = []
    for note in raw_notes:
        if isinstance(note, str):
            text = note.strip()
            if text:
                notes.append(text)
            continue
        if not isinstance(note, dict):
            continue
        content = note.get("content")
        if isinstance(content, str) and content.strip():
            notes.append(content.strip())
    return notes


def normalize_section(raw: dict[str, Any]) -> Section:
    """Normalize one upstream section record."""
    meetings_raw = raw.get("meetingTimes")
    meetings: list[Meeting] = []
    if isinstance(meetings_raw, list):
        for item in meetings_raw:
            if isinstance(item, dict):
                meetings.append(normalize_meeting(item))

    instructors: list[str] = []
    instructors_raw = raw.get("instructors")
    if isinstance(instructors_raw, list):
        for instructor in instructors_raw:
            name = format_instructor_name(instructor)
            if name is not None:
                instructors.append(name)

    delivery_modes: list[DeliveryMode] = []
    delivery_raw = raw.get("deliveryModes")
    if isinstance(delivery_raw, list):
        for item in delivery_raw:
            if not isinstance(item, dict):
                continue
            session = as_optional_str(item.get("session"))
            mode = as_optional_str(item.get("mode"))
            if session is None or mode is None:
                continue
            delivery_modes.append(DeliveryMode(session=session, mode=mode))

    current_enrolment = as_optional_int(raw.get("currentEnrolment"))
    max_enrolment = as_optional_int(raw.get("maxEnrolment"))

    return Section(
        name=as_optional_str(raw.get("name")) or "",
        type=as_optional_str(raw.get("type")),
        teaching_method=as_optional_str(raw.get("teachMethod")),
        section_number=as_optional_str(raw.get("sectionNumber")),
        meetings=meetings,
        instructors=instructors,
        delivery_modes=delivery_modes,
        current_enrolment=current_enrolment,
        max_enrolment=max_enrolment,
        available_space=compute_available_space(max_enrolment, current_enrolment),
        current_waitlist=as_optional_int(raw.get("currentWaitlist")),
        waitlist_allowed=normalize_yn_flag(raw.get("waitlistInd")),
        enrolment_indicator=as_optional_str(raw.get("enrolmentInd")),
        cancelled=normalize_yn_flag(raw.get("cancelInd")),
        tba=normalize_yn_flag(raw.get("tbaInd")),
        notes=_normalize_notes(raw.get("notes")),
    )


def _department_name(raw: dict[str, Any]) -> str | None:
    department = raw.get("department")
    if isinstance(department, dict):
        return as_optional_str(department.get("name")) or as_optional_str(
            department.get("code")
        )
    return as_optional_str(department)


def _division_name(raw: dict[str, Any]) -> str | None:
    info = raw.get("cmCourseInfo")
    if isinstance(info, dict):
        division = as_optional_str(info.get("division"))
        if division is not None:
            return division
    faculty = raw.get("faculty")
    if isinstance(faculty, dict):
        return as_optional_str(faculty.get("name")) or as_optional_str(
            faculty.get("code")
        )
    return None


def normalize_course(
    raw: dict[str, Any],
    *,
    fetched_at: datetime | None = None,
) -> Course:
    """Normalize one upstream course record, including sections."""
    info = raw.get("cmCourseInfo") if isinstance(raw.get("cmCourseInfo"), dict) else {}
    sections_raw = raw.get("sections")
    sections: list[Section] = []
    if isinstance(sections_raw, list):
        for item in sections_raw:
            if isinstance(item, dict):
                sections.append(normalize_section(item))

    sessions_raw = raw.get("sessions")
    sessions = (
        [session for session in sessions_raw if isinstance(session, str)]
        if isinstance(sessions_raw, list)
        else []
    )

    breadth = info.get("breadthRequirements") if isinstance(info, dict) else None
    distribution = (
        info.get("distributionRequirements") if isinstance(info, dict) else None
    )

    return Course(
        id=raw.get("id"),
        code=as_optional_str(raw.get("code")) or "",
        name=as_optional_str(raw.get("name")) or as_optional_str(raw.get("title")),
        section_code=as_optional_str(raw.get("sectionCode")),
        campus=as_optional_str(raw.get("campus")),
        sessions=sessions,
        division=_division_name(raw),
        department=_department_name(raw),
        description=_blank_to_none(
            info.get("description") if isinstance(info, dict) else None
        ),
        prerequisites=_blank_to_none(
            info.get("prerequisitesText") if isinstance(info, dict) else None
        ),
        corequisites=_blank_to_none(
            info.get("corequisitesText") if isinstance(info, dict) else None
        ),
        exclusions=_blank_to_none(
            info.get("exclusionsText") if isinstance(info, dict) else None
        ),
        breadth_requirements=(
            [item for item in breadth if isinstance(item, str)]
            if isinstance(breadth, list)
            else []
        ),
        distribution_requirements=(
            [item for item in distribution if isinstance(item, str)]
            if isinstance(distribution, list)
            else []
        ),
        sections=sections,
        fetched_at=fetched_at or utc_now(),
    )


def normalize_search_course(raw: dict[str, Any]) -> CourseSearchItem:
    """Normalize a concise search-result course summary."""
    sections_raw = raw.get("sections")
    section_count = len(sections_raw) if isinstance(sections_raw, list) else 0
    sessions_raw = raw.get("sessions")
    sessions = (
        [session for session in sessions_raw if isinstance(session, str)]
        if isinstance(sessions_raw, list)
        else []
    )
    return CourseSearchItem(
        id=raw.get("id"),
        code=as_optional_str(raw.get("code")) or "",
        name=as_optional_str(raw.get("name")) or as_optional_str(raw.get("title")),
        section_code=as_optional_str(raw.get("sectionCode")),
        campus=as_optional_str(raw.get("campus")),
        sessions=sessions,
        section_count=section_count,
    )


def meeting_is_conflict_checkable(meeting: Meeting) -> bool:
    """Return whether a normalized meeting can be used in overlap calculations."""
    return (
        meeting.day != "Unknown"
        and meeting.day_number is not None
        and meeting.start_minutes is not None
        and meeting.end_minutes is not None
        and meeting.repetition == "WEEKLY"
    )
