"""Pure overlap and transition-buffer conflict detection."""

from __future__ import annotations

from dataclasses import dataclass

from uoft_timetable_mcp.timetable.models import (
    ConflictRecord,
    Meeting,
    Section,
    TransitionViolation,
    UncheckedMeeting,
)
from uoft_timetable_mcp.timetable.normalize import (
    format_hhmm,
    meeting_is_conflict_checkable,
)

_DAY_SORT_ORDER = {
    "Monday": 1,
    "Tuesday": 2,
    "Wednesday": 3,
    "Thursday": 4,
    "Friday": 5,
    "Saturday": 6,
    "Sunday": 7,
}


@dataclass(frozen=True, slots=True)
class ResolvedSection:
    """A section successfully resolved for conflict checking."""

    course_code: str
    section: Section

    @property
    def section_name(self) -> str:
        return self.section.name

    @property
    def label(self) -> str:
        return f"{self.course_code} {self.section_name}"


@dataclass(frozen=True, slots=True)
class _MeetingRef:
    course_code: str
    section_name: str
    label: str
    meeting: Meeting

    @property
    def section_key(self) -> tuple[str, str]:
        return self.course_code, self.section_name.upper()


@dataclass(frozen=True, slots=True)
class ConflictAnalysis:
    conflicts: list[ConflictRecord]
    transition_violations: list[TransitionViolation]
    unchecked_meetings: list[UncheckedMeeting]


def meeting_range_label(meeting: Meeting) -> str:
    start = meeting.start or "?"
    end = meeting.end or "?"
    return f"{start}-{end}"


def intervals_overlap(
    start_a: int,
    end_a: int,
    start_b: int,
    end_b: int,
) -> bool:
    """Return True when half-open-style exclusive endpoints still overlap.

    Back-to-back intervals (``end_a == start_b``) do not overlap.
    """
    return start_a < end_b and start_b < end_a


def analyze_conflicts(
    resolved_sections: list[ResolvedSection],
    *,
    minimum_transition_minutes: int = 0,
) -> ConflictAnalysis:
    """Detect direct overlaps and optional transition-buffer violations.

    This function is pure: no HTTP or MCP dependencies.
    """
    unchecked: list[UncheckedMeeting] = []
    checkable: list[_MeetingRef] = []

    for resolved in resolved_sections:
        section = resolved.section
        if section.tba is True and not section.meetings:
            unchecked.append(
                UncheckedMeeting(
                    course_code=resolved.course_code,
                    section_name=resolved.section_name,
                    reason="Section is marked TBA and has no meeting times.",
                    meeting=None,
                )
            )
            continue

        if not section.meetings:
            unchecked.append(
                UncheckedMeeting(
                    course_code=resolved.course_code,
                    section_name=resolved.section_name,
                    reason="Section has no meeting times to check.",
                    meeting=None,
                )
            )
            continue

        for meeting in section.meetings:
            if meeting_is_conflict_checkable(meeting):
                checkable.append(
                    _MeetingRef(
                        course_code=resolved.course_code,
                        section_name=resolved.section_name,
                        label=resolved.label,
                        meeting=meeting,
                    )
                )
                continue

            reason = _unchecked_reason(meeting, tba=section.tba is True)
            unchecked.append(
                UncheckedMeeting(
                    course_code=resolved.course_code,
                    section_name=resolved.section_name,
                    reason=reason,
                    meeting=meeting,
                )
            )

    conflicts = _find_direct_conflicts(checkable)
    transitions = _find_transition_violations(
        checkable,
        minimum_transition_minutes=minimum_transition_minutes,
    )
    return ConflictAnalysis(
        conflicts=conflicts,
        transition_violations=transitions,
        unchecked_meetings=unchecked,
    )


def _unchecked_reason(meeting: Meeting, *, tba: bool) -> str:
    if tba:
        return "Meeting belongs to a TBA section."
    if meeting.repetition != "WEEKLY":
        repetition = meeting.repetition or "unknown"
        return f"Meeting repetition is {repetition}, not WEEKLY."
    if meeting.day == "Unknown" or meeting.day_number is None:
        return "Meeting day is unknown."
    if meeting.start_minutes is None or meeting.end_minutes is None:
        return "Meeting start/end time is invalid or missing."
    return "Meeting cannot be checked for conflicts."


def _find_direct_conflicts(meetings: list[_MeetingRef]) -> list[ConflictRecord]:
    records: list[ConflictRecord] = []
    seen: set[tuple[str, str, str, str, str, int, int]] = set()

    for index, left in enumerate(meetings):
        for right in meetings[index + 1 :]:
            if left.section_key == right.section_key:
                continue
            left_meeting = left.meeting
            right_meeting = right.meeting
            assert left_meeting.day_number is not None
            assert right_meeting.day_number is not None
            assert left_meeting.start_minutes is not None
            assert left_meeting.end_minutes is not None
            assert right_meeting.start_minutes is not None
            assert right_meeting.end_minutes is not None

            if left_meeting.day_number != right_meeting.day_number:
                continue
            if not intervals_overlap(
                left_meeting.start_minutes,
                left_meeting.end_minutes,
                right_meeting.start_minutes,
                right_meeting.end_minutes,
            ):
                continue

            section_a, meeting_a, section_b, meeting_b = _ordered_pair(left, right)
            overlap_start = max(
                meeting_a.start_minutes or 0,
                meeting_b.start_minutes or 0,
            )
            overlap_end = min(
                meeting_a.end_minutes or 0,
                meeting_b.end_minutes or 0,
            )
            dedupe_key = (
                meeting_a.day,
                section_a,
                section_b,
                meeting_range_label(meeting_a),
                meeting_range_label(meeting_b),
                overlap_start,
                overlap_end,
            )
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            records.append(
                ConflictRecord(
                    kind="overlap",
                    day=meeting_a.day,
                    section_a=section_a,
                    section_b=section_b,
                    meeting_a=meeting_range_label(meeting_a),
                    meeting_b=meeting_range_label(meeting_b),
                    overlap_start=format_hhmm(overlap_start) or "00:00",
                    overlap_end=format_hhmm(overlap_end) or "00:00",
                    overlap_minutes=overlap_end - overlap_start,
                )
            )

    records.sort(
        key=lambda item: (
            _DAY_SORT_ORDER.get(item.day, 99),
            item.overlap_start,
            item.section_a,
            item.section_b,
            item.meeting_a,
            item.meeting_b,
        )
    )
    return records


def _find_transition_violations(
    meetings: list[_MeetingRef],
    *,
    minimum_transition_minutes: int,
) -> list[TransitionViolation]:
    if minimum_transition_minutes <= 0:
        return []

    by_day: dict[int, list[_MeetingRef]] = {}
    for item in meetings:
        day_number = item.meeting.day_number
        assert day_number is not None
        by_day.setdefault(day_number, []).append(item)

    violations: list[TransitionViolation] = []
    seen: set[tuple[str, str, str, str, str, int]] = set()

    for day_number in sorted(by_day):
        day_meetings = sorted(
            by_day[day_number],
            key=lambda item: (
                item.meeting.start_minutes or 0,
                item.meeting.end_minutes or 0,
                item.label,
            ),
        )
        for index in range(len(day_meetings) - 1):
            earlier = day_meetings[index]
            later = day_meetings[index + 1]
            earlier_end = earlier.meeting.end_minutes
            later_start = later.meeting.start_minutes
            assert earlier_end is not None
            assert later_start is not None
            gap = later_start - earlier_end
            # Overlaps are reported only as conflicts, not transitions.
            if gap < 0:
                continue
            if gap >= minimum_transition_minutes:
                continue

            section_a, meeting_a, section_b, meeting_b = _ordered_pair(earlier, later)
            dedupe_key = (
                meeting_a.day,
                section_a,
                section_b,
                meeting_range_label(meeting_a),
                meeting_range_label(meeting_b),
                gap,
            )
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            violations.append(
                TransitionViolation(
                    kind="transition",
                    day=meeting_a.day,
                    section_a=section_a,
                    section_b=section_b,
                    meeting_a=meeting_range_label(meeting_a),
                    meeting_b=meeting_range_label(meeting_b),
                    gap_minutes=gap,
                    required_minutes=minimum_transition_minutes,
                )
            )

    violations.sort(
        key=lambda item: (
            _DAY_SORT_ORDER.get(item.day, 99),
            item.gap_minutes,
            item.section_a,
            item.section_b,
            item.meeting_a,
            item.meeting_b,
        )
    )
    return violations


def _ordered_pair(
    left: _MeetingRef,
    right: _MeetingRef,
) -> tuple[str, Meeting, str, Meeting]:
    if (left.label, meeting_range_label(left.meeting)) <= (
        right.label,
        meeting_range_label(right.meeting),
    ):
        return left.label, left.meeting, right.label, right.meeting
    return right.label, right.meeting, left.label, left.meeting
