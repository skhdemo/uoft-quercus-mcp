"""Unit tests for upstream → public normalization helpers."""

from __future__ import annotations

import copy
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from uoft_timetable_mcp.timetable.course_codes import (
    expand_short_course_code,
    is_course_code_query,
    is_short_course_code,
    suffixes_for_divisions,
)
from uoft_timetable_mcp.timetable.normalize import (
    classify_search_query,
    compute_available_space,
    day_name,
    format_hhmm,
    format_instructor_name,
    format_location,
    meeting_is_conflict_checkable,
    millis_to_minutes,
    normalize_course,
    normalize_meeting,
    normalize_reference_data,
    normalize_search_course,
    normalize_section,
    normalize_yn_flag,
    session_matches,
)

FIXTURES = Path(__file__).parent / "fixtures"
FETCHED_AT = datetime(2026, 7, 27, 1, 0, 0, tzinfo=UTC)


def _load_fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


@pytest.mark.parametrize(
    ("millis", "expected_minutes", "expected_hhmm"),
    [
        (0, 0, "00:00"),
        (3_600_000, 60, "01:00"),
        (43_200_000, 720, "12:00"),
        (46_800_000, 780, "13:00"),
        (82_800_000, 1380, "23:00"),
        (86_400_000, 1440, "24:00"),
    ],
)
def test_millis_conversion_common_times(
    millis: int,
    expected_minutes: int,
    expected_hhmm: str,
) -> None:
    minutes = millis_to_minutes(millis)
    assert minutes == expected_minutes
    assert format_hhmm(minutes) == expected_hhmm


@pytest.mark.parametrize(
    "millis",
    [-1, 86_400_001, "46800000", 46_800_000.0, True, None],
)
def test_millis_rejects_invalid_values(millis: object) -> None:
    assert millis_to_minutes(millis) is None
    assert format_hhmm(None) is None


@pytest.mark.parametrize(
    ("day_number", "expected"),
    [
        (1, "Monday"),
        (2, "Tuesday"),
        (3, "Wednesday"),
        (4, "Thursday"),
        (5, "Friday"),
        (6, "Saturday"),
        (7, "Sunday"),
    ],
)
def test_every_supported_day_maps(day_number: int, expected: str) -> None:
    name, number = day_name(day_number)
    assert name == expected
    assert number == day_number


@pytest.mark.parametrize("day_number", [0, 8, -1, "1", 1.0, None, True])
def test_unknown_day_values(day_number: object) -> None:
    name, number = day_name(day_number)
    assert name == "Unknown"
    assert number is None


def test_unknown_day_meeting_is_not_conflict_checkable() -> None:
    meeting = normalize_meeting(
        {
            "start": {"day": 99, "millisofday": 36_000_000},
            "end": {"day": 99, "millisofday": 39_600_000},
            "building": None,
            "sessionCode": "20269",
            "repetition": "WEEKLY",
        }
    )
    assert meeting.day == "Unknown"
    assert meeting.day_number is None
    assert not meeting_is_conflict_checkable(meeting)


def test_location_joins_code_room_and_suffix_without_duplicate_spaces() -> None:
    location, code, room, url = format_location(
        {
            "buildingCode": "BA",
            "buildingRoomNumber": "1130",
            "buildingRoomSuffix": "A",
            "buildingUrl": "https://map.utoronto.ca/example",
        }
    )
    assert location == "BA 1130 A"
    assert code == "BA"
    assert room == "1130 A"
    assert url == "https://map.utoronto.ca/example"


def test_location_handles_missing_building_fields() -> None:
    assert format_location(None) == (None, None, None, None)
    location, code, room, url = format_location(
        {"buildingCode": "PB", "buildingRoomNumber": "", "buildingRoomSuffix": None}
    )
    assert location == "PB"
    assert code == "PB"
    assert room is None
    assert url is None


@pytest.mark.parametrize(
    ("instructor", "expected"),
    [
        ({"firstName": "Jacqueline", "lastName": "Smith"}, "Jacqueline Smith"),
        ({"firstName": "Ada", "lastName": ""}, "Ada"),
        ({"firstName": "", "lastName": "Lovelace"}, "Lovelace"),
        ({"firstName": None, "lastName": None}, None),
        ({"firstName": "  ", "lastName": "  "}, None),
    ],
)
def test_instructor_name_handles_missing_parts(
    instructor: dict[str, object],
    expected: str | None,
) -> None:
    assert format_instructor_name(instructor) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("Y", True),
        ("N", False),
        ("y", True),
        ("n", False),
        (None, None),
        ("", None),
        ("YES", None),
        ("X", None),
        (True, None),
    ],
)
def test_yn_flag_normalization(value: object, expected: bool | None) -> None:
    assert normalize_yn_flag(value) == expected


@pytest.mark.parametrize(
    ("max_enrolment", "current_enrolment", "expected"),
    [
        (196, 177, 19),
        (10, 10, 0),
        (10, 12, -2),
        (None, 10, None),
        (10, None, None),
        ("10", 1, None),
        (10, "1", None),
        (True, 1, None),
    ],
)
def test_available_space_calculation(
    max_enrolment: object,
    current_enrolment: object,
    expected: int | None,
) -> None:
    assert compute_available_space(max_enrolment, current_enrolment) == expected


def test_tba_section_with_no_meetings_normalizes() -> None:
    section = normalize_section(
        {
            "name": "LEC9999",
            "type": "Lecture",
            "teachMethod": "LEC",
            "sectionNumber": "9999",
            "meetingTimes": [],
            "instructors": [{"firstName": "Ada", "lastName": ""}],
            "currentEnrolment": 0,
            "maxEnrolment": 50,
            "cancelInd": "N",
            "waitlistInd": "N",
            "deliveryModes": [{"session": "20269", "mode": "ONLIN"}],
            "currentWaitlist": 0,
            "enrolmentInd": "P",
            "tbaInd": "Y",
            "notes": [{"name": "Section Note", "type": "SECTION", "content": "TBA"}],
        }
    )
    assert section.tba is True
    assert section.meetings == []
    assert section.instructors == ["Ada"]
    assert section.notes == ["TBA"]
    assert section.available_space == 50


def test_asynchronous_section_missing_meeting_times_key() -> None:
    section = normalize_section(
        {
            "name": "LEC0001",
            "teachMethod": "LEC",
            "instructors": [],
            "tbaInd": "N",
            "cancelInd": "N",
        }
    )
    assert section.meetings == []
    assert section.cancelled is False
    assert section.tba is False


def test_fixture_course_is_not_mutated() -> None:
    payload = _load_fixture("course_csc108.json")
    course_raw = payload["payload"]["pageableCourse"]["courses"][0]
    before = copy.deepcopy(course_raw)

    normalized = normalize_course(course_raw, fetched_at=FETCHED_AT)

    assert course_raw == before
    assert normalized.code == "CSC108H1"
    assert normalized.section_code == "F"
    assert normalized.sessions == ["20269"]
    assert any(section.name == "LEC0201" for section in normalized.sections)


def test_normalize_meeting_from_fixture_shape() -> None:
    meeting = normalize_meeting(
        {
            "start": {"day": 1, "millisofday": 46_800_000},
            "end": {"day": 1, "millisofday": 50_400_000},
            "building": {
                "buildingCode": "PB",
                "buildingRoomNumber": "B250",
                "buildingRoomSuffix": "",
                "buildingUrl": "https://map.utoronto.ca/example",
                "buildingName": None,
            },
            "sessionCode": "20269",
            "repetition": "WEEKLY",
            "repetitionTime": "ONCE_A_WEEK",
        }
    )
    assert meeting.day == "Monday"
    assert meeting.day_number == 1
    assert meeting.start == "13:00"
    assert meeting.end == "14:00"
    assert meeting.start_minutes == 780
    assert meeting.end_minutes == 840
    assert meeting.location == "PB B250"
    assert meeting.building_code == "PB"
    assert meeting.room == "B250"
    assert meeting_is_conflict_checkable(meeting)


def test_non_weekly_meeting_copied_but_not_conflict_checkable() -> None:
    meeting = normalize_meeting(
        {
            "start": {"day": 5, "millisofday": 36_000_000},
            "end": {"day": 5, "millisofday": 39_600_000},
            "building": {
                "buildingCode": "BA",
                "buildingRoomNumber": "1130",
                "buildingRoomSuffix": "A",
            },
            "sessionCode": "20269",
            "repetition": "BIWEEKLY",
        }
    )
    assert meeting.repetition == "BIWEEKLY"
    assert meeting.day == "Friday"
    assert meeting.location == "BA 1130 A"
    assert not meeting_is_conflict_checkable(meeting)


def test_invalid_millis_are_not_wrapped() -> None:
    meeting = normalize_meeting(
        {
            "start": {"day": 1, "millisofday": -1},
            "end": {"day": 1, "millisofday": 99_999_999},
            "repetition": "WEEKLY",
        }
    )
    assert meeting.start_minutes is None
    assert meeting.end_minutes is None
    assert meeting.start is None
    assert meeting.end is None
    assert not meeting_is_conflict_checkable(meeting)


def test_reference_data_strips_headers_and_preserves_group() -> None:
    payload = _load_fixture("reference_data.json")["payload"]
    before = copy.deepcopy(payload)

    normalized = normalize_reference_data(payload, fetched_at=FETCHED_AT)

    assert payload == before
    assert all(option.value != "Summer" for option in normalized.sessions)
    assert any(option.value == "20265F" for option in normalized.sessions)
    fall = next(option for option in normalized.sessions if option.value == "20269")
    assert fall.label.startswith("Fall")
    assert fall.group is not None
    assert any(option.value == "ARTSC" for option in normalized.divisions)
    assert normalized.fetched_at == FETCHED_AT


def test_search_course_summary_is_concise() -> None:
    payload = _load_fixture("search_csc108.json")
    course_raw = payload["payload"]["pageableCourse"]["courses"][0]
    before = copy.deepcopy(course_raw)

    summary = normalize_search_course(course_raw)

    assert course_raw == before
    assert summary.code == "CSC108H1"
    assert summary.section_code == "F"
    assert summary.section_count == len(course_raw["sections"])
    assert summary.sessions == ["20269"]


def test_normalize_course_from_fixture_includes_section_details() -> None:
    payload = _load_fixture("course_csc108.json")
    course_raw = payload["payload"]["pageableCourse"]["courses"][0]
    course = normalize_course(course_raw, fetched_at=FETCHED_AT)

    lec = next(section for section in course.sections if section.name == "LEC0201")
    assert lec.type == "Lecture"
    assert lec.teaching_method == "LEC"
    assert lec.waitlist_allowed is True
    assert lec.cancelled is False
    assert lec.tba is False
    assert lec.enrolment_indicator == "P"
    assert lec.available_space == lec.max_enrolment - lec.current_enrolment  # type: ignore[operator]
    assert course.department == "Department of Computer Science"
    assert course.division is not None
    assert course.description is not None


@pytest.mark.parametrize(
    ("requested", "course_sessions", "expected"),
    [
        ("20269", ["20269"], True),
        ("20271", ["20269"], False),
        ("20269", ["20269-20271"], True),
        ("20271", ["20269-20271"], True),
        ("20265", ["20269-20271"], False),
        ("20265F", ["20265F"], True),
        ("20265", ["20265F"], False),
        ("20265F", ["20265F-20265S"], False),  # not a known 5+5 combined pattern
    ],
)
def test_session_matching_rules(
    requested: str,
    course_sessions: list[str],
    expected: bool,
) -> None:
    assert session_matches(requested, course_sessions) is expected


@pytest.mark.parametrize(
    ("query", "expected_code", "expected_title", "is_code"),
    [
        ("CSC108H1", "CSC108H1", "", True),
        ("csc108h1", "CSC108H1", "", True),
        ("CSCA08H3", "CSCA08H3", "", True),
        ("MATA67H3", "MATA67H3", "", True),
        ("CSCA08", "CSCA08", "", True),
        ("CSC108", "CSC108", "", True),
        ("computer programming", "", "computer programming", False),
        ("CS", "", "CS", False),
        ("  MAT137Y1  ", "MAT137Y1", "", True),
    ],
)
def test_search_query_classification(
    query: str,
    expected_code: str,
    expected_title: str,
    is_code: bool,
) -> None:
    code, title, matched = classify_search_query(query)
    assert code == expected_code
    assert title == expected_title
    assert matched is is_code


@pytest.mark.parametrize(
    ("value", "is_code", "is_short"),
    [
        ("CSCA08H3", True, False),
        ("MATA67H3", True, False),
        ("CSC108H1", True, False),
        ("CSCA08", True, True),
        ("CSC108", True, True),
        ("computer programming", False, False),
        ("CS", False, False),
    ],
)
def test_course_code_helpers(value: str, is_code: bool, is_short: bool) -> None:
    assert is_course_code_query(value) is is_code
    assert is_short_course_code(value) is is_short


def test_expand_short_code_scar_includes_h3() -> None:
    expanded = expand_short_course_code("CSCA08", ["SCAR"])
    assert expanded == ["CSCA08H3", "CSCA08Y3"]
    assert "CSCA08H3" in expanded


def test_expand_short_code_artsc_includes_h1() -> None:
    expanded = expand_short_course_code("CSC108", ["ARTSC"])
    assert expanded == ["CSC108H1", "CSC108Y1"]


def test_expand_short_code_multiple_divisions_stable_union() -> None:
    expanded = expand_short_course_code("CSC108", ["ARTSC", "SCAR", "ERIN"])
    assert expanded == [
        "CSC108H1",
        "CSC108Y1",
        "CSC108H3",
        "CSC108Y3",
        "CSC108H5",
        "CSC108Y5",
    ]


def test_expand_short_code_no_divisions_tries_all_common() -> None:
    assert expand_short_course_code("CSCA08", None) == [
        "CSCA08H1",
        "CSCA08Y1",
        "CSCA08H3",
        "CSCA08Y3",
        "CSCA08H5",
        "CSCA08Y5",
    ]


def test_suffixes_for_divisions_mapping() -> None:
    assert suffixes_for_divisions(["SCAR"]) == ["H3", "Y3"]
    assert suffixes_for_divisions(["ERIN"]) == ["H5", "Y5"]
    assert suffixes_for_divisions(["ARTSC"]) == ["H1", "Y1"]
    assert suffixes_for_divisions(["APSC"]) == ["H1", "Y1"]
    assert suffixes_for_divisions(None) == ["H1", "Y1", "H3", "Y3", "H5", "Y5"]
