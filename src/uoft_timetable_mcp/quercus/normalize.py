"""Convert Quercus (Canvas) payloads into stable public shapes."""

from __future__ import annotations

import re
from typing import Any

_TAG_RE = re.compile(r"<[^>]+>")
_MAX_HTML_PLAIN_CHARS = 500


def as_optional_str(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def _plain_snippet(value: Any, *, max_chars: int = _MAX_HTML_PLAIN_CHARS) -> str | None:
    text = as_optional_str(value)
    if text is None:
        return None
    plain = _TAG_RE.sub(" ", text)
    plain = re.sub(r"\s+", " ", plain).strip()
    if not plain:
        return None
    if len(plain) > max_chars:
        return plain[:max_chars]
    return plain


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


def course_ref(course: dict[str, Any]) -> dict[str, Any]:
    """Compact course identity for tool responses."""
    return {
        "id": course.get("id"),
        "course_code": course.get("course_code"),
        "name": course.get("name"),
    }


def normalize_todo_item(raw: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key in (
        "type",
        "assignment",
        "quiz",
        "context_type",
        "course_id",
        "html_url",
        "ignore",
        "ignore_permanently",
    ):
        if key in raw:
            result[key] = raw.get(key)
    assignment = raw.get("assignment")
    if isinstance(assignment, dict):
        result["assignment"] = {
            "id": assignment.get("id"),
            "name": as_optional_str(assignment.get("name")),
            "due_at": as_optional_str(assignment.get("due_at")),
            "html_url": as_optional_str(assignment.get("html_url")),
            "course_id": assignment.get("course_id"),
        }
    return result


def normalize_upcoming_event(raw: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {
        "id": raw.get("id"),
        "title": as_optional_str(raw.get("title")),
        "type": as_optional_str(raw.get("type")),
        "start_at": as_optional_str(raw.get("start_at")),
        "end_at": as_optional_str(raw.get("end_at")),
        "html_url": as_optional_str(raw.get("html_url")),
        "context_code": as_optional_str(raw.get("context_code")),
    }
    assignment = raw.get("assignment")
    if isinstance(assignment, dict):
        result["assignment_id"] = assignment.get("id")
        result["assignment_name"] = as_optional_str(assignment.get("name"))
    return {k: v for k, v in result.items() if v is not None}


def normalize_assignment(raw: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {
        "id": raw.get("id"),
        "name": as_optional_str(raw.get("name")),
        "due_at": as_optional_str(raw.get("due_at")),
        "points_possible": raw.get("points_possible"),
        "html_url": as_optional_str(raw.get("html_url")),
        "course_id": raw.get("course_id"),
        "submission_types": raw.get("submission_types")
        if isinstance(raw.get("submission_types"), list)
        else None,
        "published": raw.get("published")
        if isinstance(raw.get("published"), bool)
        else None,
    }
    return {k: v for k, v in result.items() if v is not None}


def normalize_announcement(raw: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {
        "id": raw.get("id"),
        "title": as_optional_str(raw.get("title")),
        "posted_at": as_optional_str(raw.get("posted_at")),
        "html_url": as_optional_str(raw.get("html_url")),
        "message": _plain_snippet(raw.get("message")),
    }
    return {k: v for k, v in result.items() if v is not None}


def normalize_module_item(raw: dict[str, Any]) -> dict[str, Any]:
    item_type = as_optional_str(raw.get("type"))
    result: dict[str, Any] = {
        "id": raw.get("id"),
        "title": as_optional_str(raw.get("title")),
        "type": item_type,
        "position": raw.get("position"),
        "content_id": raw.get("content_id"),
        "html_url": as_optional_str(raw.get("html_url")),
        "url": as_optional_str(raw.get("url")),
    }
    if item_type == "File" and raw.get("content_id") is not None:
        result["file_id"] = raw.get("content_id")
    return {k: v for k, v in result.items() if v is not None}


def normalize_module(
    raw: dict[str, Any],
    *,
    include_items: bool = True,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "id": raw.get("id"),
        "name": as_optional_str(raw.get("name")),
        "position": raw.get("position"),
        "published": raw.get("published")
        if isinstance(raw.get("published"), bool)
        else None,
    }
    if include_items:
        items_raw = raw.get("items")
        if isinstance(items_raw, list):
            result["items"] = [
                normalize_module_item(item)
                for item in items_raw
                if isinstance(item, dict)
            ]
    return {k: v for k, v in result.items() if v is not None}


def normalize_file(raw: dict[str, Any]) -> dict[str, Any]:
    display_name = as_optional_str(raw.get("display_name"))
    filename = as_optional_str(raw.get("filename"))
    content_type = as_optional_str(raw.get("content-type") or raw.get("content_type"))
    size = raw.get("size")
    result: dict[str, Any] = {
        "id": raw.get("id"),
        "display_name": display_name,
        "filename": filename,
        "content_type": content_type,
        "size": size if isinstance(size, int) and not isinstance(size, bool) else None,
        "folder_id": raw.get("folder_id"),
        # Intentionally omit `url` — Canvas download URLs often include verifiers.
    }
    return {k: v for k, v in result.items() if v is not None}
