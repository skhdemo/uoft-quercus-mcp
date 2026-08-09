"""Convert Quercus (Canvas) payloads into stable public shapes."""

from __future__ import annotations

from typing import Any


def as_optional_str(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def normalize_term(raw: Any) -> dict[str, Any] | None:
    """Normalize an included Canvas term object."""
    if not isinstance(raw, dict):
        return None
    term: dict[str, Any] = {}
    if "id" in raw:
        term["id"] = raw["id"]
    name = as_optional_str(raw.get("name"))
    if name is not None:
        term["name"] = name
    start_at = as_optional_str(raw.get("start_at"))
    if start_at is not None:
        term["start_at"] = start_at
    end_at = as_optional_str(raw.get("end_at"))
    if end_at is not None:
        term["end_at"] = end_at
    return term or None


def normalize_whoami(raw: dict[str, Any]) -> dict[str, Any]:
    """Public fields for ``quercus_whoami``."""
    result: dict[str, Any] = {
        "id": raw.get("id"),
        "name": as_optional_str(raw.get("name")),
    }
    sortable_name = as_optional_str(raw.get("sortable_name"))
    if sortable_name is not None:
        result["sortable_name"] = sortable_name
    short_name = as_optional_str(raw.get("short_name"))
    if short_name is not None:
        result["short_name"] = short_name
    login_id = as_optional_str(raw.get("login_id"))
    if login_id is not None:
        result["login_id"] = login_id
    return result


def normalize_course(raw: dict[str, Any]) -> dict[str, Any]:
    """Public fields for one course from ``quercus_list_courses``."""
    result: dict[str, Any] = {
        "id": raw.get("id"),
        "name": as_optional_str(raw.get("name")),
        "course_code": as_optional_str(raw.get("course_code")),
    }
    if "enrollment_term_id" in raw:
        result["enrollment_term_id"] = raw.get("enrollment_term_id")
    term = normalize_term(raw.get("term"))
    if term is not None:
        result["term"] = term
    workflow_state = as_optional_str(raw.get("workflow_state"))
    if workflow_state is not None:
        result["workflow_state"] = workflow_state
    return result
