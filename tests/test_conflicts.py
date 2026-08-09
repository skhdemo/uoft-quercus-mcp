"""Unit tests for pure conflict and transition-buffer logic."""

from __future__ import annotations

from uoft_timetable_mcp.timetable.conflicts import ResolvedSection, analyze_conflicts
from uoft_timetable_mcp.timetable.models import Meeting, Section


def _meeting(
    *,
    day: str,
    day_number: int,
    start_minutes: int,
    end_minutes: int,
    repetition: str = "WEEKLY",
) -> Meeting:
    start_h, start_m = divmod(start_minutes, 60)
    end_h, end_m = divmod(end_minutes, 60)
    return Meeting(
        day=day,
        day_number=day_number,
        start=f"{start_h:02d}:{start_m:02d}",
        end=f"{end_h:02d}:{end_m:02d}",
        start_minutes=start_minutes,
        end_minutes=end_minutes,
        location=None,
        building_code=None,
        room=None,
        building_url=None,
        session_code="20269",
        repetition=repetition,
    )


def _section(name: str, meetings: list[Meeting], *, tba: bool = False) -> Section:
    return Section(
        name=name,
        type="Lecture",
        teaching_method="LEC",
        section_number=name[-4:],
        meetings=meetings,
        cancelled=False,
        tba=tba,
    )


def _resolved(course: str, section: Section) -> ResolvedSection:
    return ResolvedSection(course_code=course, section=section)


def test_partial_overlap_is_conflict() -> None:
    a = _resolved(
        "CSC148H1",
        _section(
            "LEC0101",
            [_meeting(day="Tuesday", day_number=2, start_minutes=600, end_minutes=720)],
        ),
    )
    b = _resolved(
        "STA256H1",
        _section(
            "LEC0201",
            [_meeting(day="Tuesday", day_number=2, start_minutes=660, end_minutes=780)],
        ),
    )
    result = analyze_conflicts([a, b])
    assert len(result.conflicts) == 1
    assert result.conflicts[0].overlap_minutes == 60
    assert result.conflicts[0].overlap_start == "11:00"
    assert result.conflicts[0].overlap_end == "12:00"


def test_contained_interval_is_conflict() -> None:
    outer = _resolved(
        "AAA100H1",
        _section(
            "LEC0101",
            [_meeting(day="Monday", day_number=1, start_minutes=540, end_minutes=720)],
        ),
    )
    inner = _resolved(
        "BBB100H1",
        _section(
            "LEC0101",
            [_meeting(day="Monday", day_number=1, start_minutes=600, end_minutes=660)],
        ),
    )
    result = analyze_conflicts([outer, inner])
    assert len(result.conflicts) == 1


def test_identical_intervals_conflict() -> None:
    a = _resolved(
        "AAA100H1",
        _section(
            "LEC0101",
            [_meeting(day="Monday", day_number=1, start_minutes=600, end_minutes=660)],
        ),
    )
    b = _resolved(
        "BBB100H1",
        _section(
            "LEC0101",
            [_meeting(day="Monday", day_number=1, start_minutes=600, end_minutes=660)],
        ),
    )
    result = analyze_conflicts([a, b])
    assert len(result.conflicts) == 1
    assert result.conflicts[0].overlap_minutes == 60


def test_back_to_back_is_not_conflict() -> None:
    a = _resolved(
        "AAA100H1",
        _section(
            "LEC0101",
            [_meeting(day="Monday", day_number=1, start_minutes=540, end_minutes=600)],
        ),
    )
    b = _resolved(
        "BBB100H1",
        _section(
            "LEC0101",
            [_meeting(day="Monday", day_number=1, start_minutes=600, end_minutes=660)],
        ),
    )
    result = analyze_conflicts([a, b], minimum_transition_minutes=0)
    assert result.conflicts == []
    assert result.transition_violations == []


def test_separated_intervals_no_conflict() -> None:
    a = _resolved(
        "AAA100H1",
        _section(
            "LEC0101",
            [_meeting(day="Monday", day_number=1, start_minutes=540, end_minutes=600)],
        ),
    )
    b = _resolved(
        "BBB100H1",
        _section(
            "LEC0101",
            [_meeting(day="Monday", day_number=1, start_minutes=660, end_minutes=720)],
        ),
    )
    assert analyze_conflicts([a, b]).conflicts == []


def test_different_days_same_time_no_conflict() -> None:
    a = _resolved(
        "AAA100H1",
        _section(
            "LEC0101",
            [_meeting(day="Monday", day_number=1, start_minutes=600, end_minutes=660)],
        ),
    )
    b = _resolved(
        "BBB100H1",
        _section(
            "LEC0101",
            [_meeting(day="Tuesday", day_number=2, start_minutes=600, end_minutes=660)],
        ),
    )
    assert analyze_conflicts([a, b]).conflicts == []


def test_multiple_conflicts_deduped_and_ordered() -> None:
    a = _resolved(
        "CSC148H1",
        _section(
            "LEC0101",
            [
                _meeting(
                    day="Tuesday", day_number=2, start_minutes=600, end_minutes=720
                ),
                _meeting(
                    day="Thursday", day_number=4, start_minutes=600, end_minutes=720
                ),
            ],
        ),
    )
    b = _resolved(
        "STA256H1",
        _section(
            "LEC0201",
            [
                _meeting(
                    day="Tuesday", day_number=2, start_minutes=660, end_minutes=780
                ),
                _meeting(
                    day="Thursday", day_number=4, start_minutes=660, end_minutes=780
                ),
            ],
        ),
    )
    result = analyze_conflicts([a, b])
    assert len(result.conflicts) == 2
    assert result.conflicts[0].day == "Tuesday"
    assert result.conflicts[1].day == "Thursday"
    assert result.conflicts[0].section_a <= result.conflicts[0].section_b


def test_same_course_components_can_conflict() -> None:
    lec = _resolved(
        "CSC108H1",
        _section(
            "LEC0201",
            [
                _meeting(
                    day="Wednesday", day_number=3, start_minutes=780, end_minutes=900
                )
            ],
        ),
    )
    tut = _resolved(
        "CSC108H1",
        _section(
            "TUT0101",
            [
                _meeting(
                    day="Wednesday", day_number=3, start_minutes=840, end_minutes=900
                )
            ],
        ),
    )
    result = analyze_conflicts([lec, tut])
    assert len(result.conflicts) == 1
    assert result.conflicts[0].section_a.startswith("CSC108H1")


def test_unknown_or_tba_meeting_is_unchecked_not_conflict() -> None:
    known = _resolved(
        "AAA100H1",
        _section(
            "LEC0101",
            [_meeting(day="Monday", day_number=1, start_minutes=600, end_minutes=660)],
        ),
    )
    unknown = _resolved(
        "BBB100H1",
        _section(
            "LEC0101",
            [
                Meeting(
                    day="Unknown",
                    day_number=None,
                    start="10:00",
                    end="11:00",
                    start_minutes=600,
                    end_minutes=660,
                    location=None,
                    building_code=None,
                    room=None,
                    building_url=None,
                    session_code="20269",
                    repetition="WEEKLY",
                )
            ],
        ),
    )
    tba = _resolved("CCC100H1", _section("LEC9999", [], tba=True))
    result = analyze_conflicts([known, unknown, tba])
    assert result.conflicts == []
    assert len(result.unchecked_meetings) == 2


def test_zero_buffer_accepts_back_to_back() -> None:
    a = _resolved(
        "AAA100H1",
        _section(
            "LEC0101",
            [_meeting(day="Monday", day_number=1, start_minutes=540, end_minutes=600)],
        ),
    )
    b = _resolved(
        "BBB100H1",
        _section(
            "LEC0101",
            [_meeting(day="Monday", day_number=1, start_minutes=600, end_minutes=660)],
        ),
    )
    result = analyze_conflicts([a, b], minimum_transition_minutes=0)
    assert result.transition_violations == []


def test_fifteen_minute_buffer_flags_ten_minute_gap() -> None:
    a = _resolved(
        "AAA100H1",
        _section(
            "LEC0101",
            [_meeting(day="Monday", day_number=1, start_minutes=540, end_minutes=600)],
        ),
    )
    b = _resolved(
        "BBB100H1",
        _section(
            "LEC0101",
            [_meeting(day="Monday", day_number=1, start_minutes=610, end_minutes=670)],
        ),
    )
    result = analyze_conflicts([a, b], minimum_transition_minutes=15)
    assert len(result.transition_violations) == 1
    assert result.transition_violations[0].gap_minutes == 10


def test_fifteen_minute_buffer_accepts_fifteen_minute_gap() -> None:
    a = _resolved(
        "AAA100H1",
        _section(
            "LEC0101",
            [_meeting(day="Monday", day_number=1, start_minutes=540, end_minutes=600)],
        ),
    )
    b = _resolved(
        "BBB100H1",
        _section(
            "LEC0101",
            [_meeting(day="Monday", day_number=1, start_minutes=615, end_minutes=675)],
        ),
    )
    result = analyze_conflicts([a, b], minimum_transition_minutes=15)
    assert result.transition_violations == []


def test_overlaps_not_duplicated_as_transitions() -> None:
    a = _resolved(
        "AAA100H1",
        _section(
            "LEC0101",
            [_meeting(day="Monday", day_number=1, start_minutes=540, end_minutes=630)],
        ),
    )
    b = _resolved(
        "BBB100H1",
        _section(
            "LEC0101",
            [_meeting(day="Monday", day_number=1, start_minutes=600, end_minutes=660)],
        ),
    )
    result = analyze_conflicts([a, b], minimum_transition_minutes=15)
    assert len(result.conflicts) == 1
    assert result.transition_violations == []


def test_different_days_never_create_transition_violations() -> None:
    a = _resolved(
        "AAA100H1",
        _section(
            "LEC0101",
            [_meeting(day="Monday", day_number=1, start_minutes=540, end_minutes=600)],
        ),
    )
    b = _resolved(
        "BBB100H1",
        _section(
            "LEC0101",
            [_meeting(day="Tuesday", day_number=2, start_minutes=605, end_minutes=665)],
        ),
    )
    result = analyze_conflicts([a, b], minimum_transition_minutes=15)
    assert result.transition_violations == []


def test_three_meetings_only_adjacent_gaps_flagged() -> None:
    early = _resolved(
        "AAA100H1",
        _section(
            "LEC0101",
            [_meeting(day="Monday", day_number=1, start_minutes=540, end_minutes=600)],
        ),
    )
    mid = _resolved(
        "BBB100H1",
        _section(
            "LEC0101",
            [_meeting(day="Monday", day_number=1, start_minutes=610, end_minutes=660)],
        ),
    )
    late = _resolved(
        "CCC100H1",
        _section(
            "LEC0101",
            [_meeting(day="Monday", day_number=1, start_minutes=690, end_minutes=750)],
        ),
    )
    result = analyze_conflicts([early, mid, late], minimum_transition_minutes=15)
    # Adjacent pairs only: 10-minute gap violates; 30-minute gap does not.
    # Non-adjacent early↔late is never compared.
    assert [item.gap_minutes for item in result.transition_violations] == [10]


def test_non_weekly_meeting_is_unchecked() -> None:
    weekly = _resolved(
        "AAA100H1",
        _section(
            "LEC0101",
            [_meeting(day="Friday", day_number=5, start_minutes=600, end_minutes=660)],
        ),
    )
    biweekly = _resolved(
        "BBB100H1",
        _section(
            "PRA0101",
            [
                _meeting(
                    day="Friday",
                    day_number=5,
                    start_minutes=600,
                    end_minutes=660,
                    repetition="BIWEEKLY",
                )
            ],
        ),
    )
    result = analyze_conflicts([weekly, biweekly])
    assert result.conflicts == []
    assert len(result.unchecked_meetings) == 1
    assert result.unchecked_meetings[0].section_name == "PRA0101"
