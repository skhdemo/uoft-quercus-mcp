"""Pure helpers for UofT course-code classification and short-code expansion."""

from __future__ import annotations

import re

# Accept 3–4 letter departments and 2–3 digit course numbers so UTSC/UTM-style
# codes like CSCA08H3 classify as course codes (not titles).
_COURSE_CODE_QUERY_RE = re.compile(r"^[A-Z]{3,4}\d{2,3}[A-Z0-9]*$")

# Short codes are dept + number with no trailing campus/weight suffix (H1, Y3, …).
_SHORT_COURSE_CODE_RE = re.compile(r"^[A-Z]{3,4}\d{2,3}$")

# Common FAS-style campus/weight suffixes. Not universal for every division forever.
_COMMON_SUFFIXES = ("H1", "Y1", "H3", "Y3", "H5", "Y5")
_SCAR_SUFFIXES = ("H3", "Y3")
_ERIN_SUFFIXES = ("H5", "Y5")
_ST_GEORGE_SUFFIXES = ("H1", "Y1")


def is_course_code_query(value: str) -> bool:
    """Return True when ``value`` looks like a UofT course code (full or short)."""
    return _COURSE_CODE_QUERY_RE.fullmatch(value.strip().upper()) is not None


def is_short_course_code(value: str) -> bool:
    """Return True when ``value`` is dept+number without an H#/Y# campus suffix."""
    return _SHORT_COURSE_CODE_RE.fullmatch(value.strip().upper()) is not None


def suffixes_for_divisions(divisions: list[str] | None) -> list[str]:
    """Return candidate campus/weight suffixes for the given divisions.

    Stable union order is always ``H1, Y1, H3, Y3, H5, Y5``. When no divisions
    are provided, all common suffixes are returned.
    """
    if not divisions:
        return list(_COMMON_SUFFIXES)

    selected: set[str] = set()
    for division in divisions:
        normalized = division.strip().upper()
        if normalized == "SCAR":
            selected.update(_SCAR_SUFFIXES)
        elif normalized == "ERIN":
            selected.update(_ERIN_SUFFIXES)
        else:
            # ARTSC, APSC, and other St. George–style divisions.
            selected.update(_ST_GEORGE_SUFFIXES)

    return [suffix for suffix in _COMMON_SUFFIXES if suffix in selected]


def expand_short_course_code(
    code: str,
    divisions: list[str] | None = None,
) -> list[str]:
    """Expand a short code into candidate full codes using division suffixes.

    If ``code`` is already a full course code, returns ``[code]``.
    If ``code`` is not code-like, returns an empty list.
    """
    normalized = code.strip().upper()
    if is_short_course_code(normalized):
        return [f"{normalized}{suffix}" for suffix in suffixes_for_divisions(divisions)]
    if is_course_code_query(normalized):
        return [normalized]
    return []


def short_code_not_found_message(course_code: str, session: str) -> str:
    """User-safe not-found message that explains common campus suffixes."""
    example = f"{course_code.strip().upper()}H3"
    return (
        f"No course records matched {course_code.strip().upper()} in session "
        f"{session}. Short codes omit the campus suffix. Common forms: "
        f"St. George …H1/…Y1, UTSC …H3/…Y3, UTM …H5/…Y5 (example: {example})."
    )
