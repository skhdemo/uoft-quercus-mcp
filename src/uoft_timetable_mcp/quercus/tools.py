"""Quercus MCP tool registration."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pydantic import ValidationError

from uoft_timetable_mcp.common.serialize import model_to_public_dict
from uoft_timetable_mcp.quercus.errors import (
    QuercusError,
    QuercusValidationError,
)
from uoft_timetable_mcp.quercus.extract import extract_text, safe_filename
from uoft_timetable_mcp.quercus.models import (
    CourseScopedInput,
    GetFileInput,
    ListCoursesInput,
    ListCoursesResult,
    ListFilesInput,
    ListModulesInput,
    ListTodoInput,
    QuercusCourse,
    QuercusWhoamiResult,
)
from uoft_timetable_mcp.quercus.normalize import (
    course_ref,
    normalize_announcement,
    normalize_assignment,
    normalize_course,
    normalize_file,
    normalize_module,
    normalize_todo_item,
    normalize_upcoming_event,
    normalize_whoami,
)
from uoft_timetable_mcp.quercus.resolve import (
    resolve_announcement_window,
    resolve_course_ref,
    resolve_file_in_course,
)

if TYPE_CHECKING:
    from fastmcp import FastMCP

_DISCLAIMER = (
    "Requires QUERCUS_ACCESS_TOKEN. Unofficial Quercus/Canvas integration; "
    "not affiliated with the University of Toronto."
)
_COURSE_HINT = "Prefer Canvas ids or codes from quercus_list_courses when ambiguous. "


def register_quercus_tools(mcp: FastMCP) -> None:
    """Register all Quercus tools on the shared FastMCP instance."""
    # Local imports avoid circular import at module load (server ↔ tools).
    from uoft_timetable_mcp.server import get_state, raise_tool_error

    @mcp.tool(
        description=(
            "Verify the configured Quercus personal access token and return the "
            "current user identity (Canvas id, name, and related fields). "
            f"{_DISCLAIMER}"
        )
    )
    async def quercus_whoami() -> dict[str, Any]:
        state = get_state()
        try:
            raw = await state.quercus_client.get_self()
        except QuercusError as exc:
            raise_tool_error(exc)
        result = QuercusWhoamiResult.model_validate(normalize_whoami(raw))
        return model_to_public_dict(result, exclude_none=True)

    @mcp.tool(
        description=(
            "List Quercus courses for the authenticated user. Returns Canvas "
            "course `id` with `course_code` and `name`. Default enrollment_state "
            "is `active`. Set include_concluded=true to include concluded courses. "
            f"{_DISCLAIMER}"
        )
    )
    async def quercus_list_courses(
        enrollment_state: str = "active",
        include_concluded: bool = False,
    ) -> dict[str, Any]:
        try:
            params = ListCoursesInput(
                enrollment_state=enrollment_state,
                include_concluded=include_concluded,
            )
        except ValidationError as exc:
            message = "; ".join(
                error.get("msg", "Invalid input") for error in exc.errors()
            )
            raise_tool_error(QuercusValidationError(message))

        state = get_state()
        resolved_state = None if params.include_concluded else params.enrollment_state
        try:
            raw_courses = await state.quercus_client.list_courses(
                enrollment_state=resolved_state
            )
        except QuercusError as exc:
            raise_tool_error(exc)

        normalized = [normalize_course(raw) for raw in raw_courses]
        courses = [QuercusCourse.model_validate(item) for item in normalized]
        # Warm resolver cache so the next resolve avoids another /courses fetch.
        state.quercus_course_cache.set_courses(
            normalized,
            enrollment_state=resolved_state,
        )
        result = ListCoursesResult(courses=courses, count=len(courses))
        return model_to_public_dict(result, exclude_none=True)

    @mcp.tool(
        description=(
            "List Quercus todo items and upcoming events for the authenticated "
            "user (dashboard-style due work and events). Optional flags "
            "include_todo / include_upcoming (both default true). "
            f"{_DISCLAIMER}"
        )
    )
    async def quercus_list_todo(
        include_todo: bool = True,
        include_upcoming: bool = True,
    ) -> dict[str, Any]:
        try:
            params = ListTodoInput(
                include_todo=include_todo,
                include_upcoming=include_upcoming,
            )
        except ValidationError as exc:
            message = "; ".join(
                error.get("msg", "Invalid input") for error in exc.errors()
            )
            raise_tool_error(QuercusValidationError(message))

        state = get_state()
        todo: list[dict[str, Any]] = []
        upcoming: list[dict[str, Any]] = []
        try:
            if params.include_todo:
                todo = [
                    normalize_todo_item(item)
                    for item in await state.quercus_client.list_todo()
                ]
            if params.include_upcoming:
                upcoming = [
                    normalize_upcoming_event(item)
                    for item in await state.quercus_client.list_upcoming_events()
                ]
        except QuercusError as exc:
            raise_tool_error(exc)
        return {
            "todo": todo,
            "upcoming_events": upcoming,
            "todo_count": len(todo),
            "upcoming_count": len(upcoming),
        }

    @mcp.tool(
        description=(
            "List assignments for a Quercus course. `course` may be a Canvas id, "
            "course code fragment (e.g. MATA22), or name fragment. "
            f"{_COURSE_HINT}{_DISCLAIMER}"
        )
    )
    async def quercus_list_assignments(
        course: str,
        include_concluded: bool = False,
    ) -> dict[str, Any]:
        try:
            params = CourseScopedInput(
                course=course,
                include_concluded=include_concluded,
            )
        except ValidationError as exc:
            message = "; ".join(
                error.get("msg", "Invalid input") for error in exc.errors()
            )
            raise_tool_error(QuercusValidationError(message))

        state = get_state()
        try:
            resolved = await resolve_course_ref(
                params.course,
                client=state.quercus_client,
                cache=state.quercus_course_cache,
                include_concluded=params.include_concluded,
            )
            raw = await state.quercus_client.list_assignments(resolved["id"])
        except QuercusError as exc:
            raise_tool_error(exc)
        assignments = [normalize_assignment(item) for item in raw]
        return {
            "course": course_ref(resolved),
            "assignments": assignments,
            "count": len(assignments),
        }

    @mcp.tool(
        description=(
            "List announcements for a Quercus course. `course` may be a Canvas "
            "id, course code fragment, or name fragment. Overrides Canvas's "
            "default 14-day window: uses term start when available, otherwise "
            "the last 365 days, through today. "
            f"{_COURSE_HINT}{_DISCLAIMER}"
        )
    )
    async def quercus_list_announcements(
        course: str,
        include_concluded: bool = False,
    ) -> dict[str, Any]:
        try:
            params = CourseScopedInput(
                course=course,
                include_concluded=include_concluded,
            )
        except ValidationError as exc:
            message = "; ".join(
                error.get("msg", "Invalid input") for error in exc.errors()
            )
            raise_tool_error(QuercusValidationError(message))

        state = get_state()
        try:
            resolved = await resolve_course_ref(
                params.course,
                client=state.quercus_client,
                cache=state.quercus_course_cache,
                include_concluded=params.include_concluded,
            )
            window = resolve_announcement_window(resolved, now=state.clock())
            raw = await state.quercus_client.list_announcements(
                resolved["id"],
                start_date=window["start_date"],
                end_date=window["end_date"],
            )
        except QuercusError as exc:
            raise_tool_error(exc)
        announcements = [normalize_announcement(item) for item in raw]
        return {
            "course": course_ref(resolved),
            "window": {
                "start_date": window["start_date"],
                "end_date": window["end_date"],
                "start_source": window["start_source"],
            },
            "announcements": announcements,
            "count": len(announcements),
        }

    @mcp.tool(
        description=(
            "List Quercus modules (course materials map). File-type items include "
            "`file_id` for quercus_get_file. For past quiz PDFs: list modules or "
            "files, then quercus_get_file with mode=text. "
            f"{_COURSE_HINT}{_DISCLAIMER}"
        )
    )
    async def quercus_list_modules(
        course: str,
        include_items: bool = True,
        include_concluded: bool = False,
    ) -> dict[str, Any]:
        try:
            params = ListModulesInput(
                course=course,
                include_items=include_items,
                include_concluded=include_concluded,
            )
        except ValidationError as exc:
            message = "; ".join(
                error.get("msg", "Invalid input") for error in exc.errors()
            )
            raise_tool_error(QuercusValidationError(message))

        state = get_state()
        try:
            resolved = await resolve_course_ref(
                params.course,
                client=state.quercus_client,
                cache=state.quercus_course_cache,
                include_concluded=params.include_concluded,
            )
            raw = await state.quercus_client.list_modules(
                resolved["id"],
                include_items=params.include_items,
            )
        except QuercusError as exc:
            raise_tool_error(exc)
        modules = [
            normalize_module(item, include_items=params.include_items) for item in raw
        ]
        return {
            "course": course_ref(resolved),
            "modules": modules,
            "count": len(modules),
        }

    @mcp.tool(
        description=(
            "List files in a Quercus course (Files area — not module-only). "
            "Optional `search` filters by name when supported upstream. "
            "Use file ids with quercus_get_file. For past quiz PDFs prefer "
            "mode=text after listing. "
            f"{_COURSE_HINT}{_DISCLAIMER}"
        )
    )
    async def quercus_list_files(
        course: str,
        search: str | None = None,
        include_concluded: bool = False,
    ) -> dict[str, Any]:
        try:
            params = ListFilesInput(
                course=course,
                search=search,
                include_concluded=include_concluded,
            )
        except ValidationError as exc:
            message = "; ".join(
                error.get("msg", "Invalid input") for error in exc.errors()
            )
            raise_tool_error(QuercusValidationError(message))

        state = get_state()
        try:
            resolved = await resolve_course_ref(
                params.course,
                client=state.quercus_client,
                cache=state.quercus_course_cache,
                include_concluded=params.include_concluded,
            )
            raw = await state.quercus_client.list_files(
                resolved["id"],
                search_term=params.search,
            )
        except QuercusError as exc:
            raise_tool_error(exc)
        files = [normalize_file(item) for item in raw]
        return {
            "course": course_ref(resolved),
            "files": files,
            "count": len(files),
        }

    @mcp.tool(
        description=(
            "Fetch a Quercus file by Canvas file id or filename fragment. "
            "`course` is required when `file` is not a numeric id. "
            "mode=metadata|text|download (default text). text extracts PDF/DOCX/"
            "plain text when possible (no OCR). download saves under "
            "QUERCUS_DOWNLOAD_DIR. For past quiz PDFs use mode=text. "
            f"{_COURSE_HINT}{_DISCLAIMER}"
        )
    )
    async def quercus_get_file(
        file: str,
        course: str | None = None,
        mode: str = "text",
        include_concluded: bool = False,
    ) -> dict[str, Any]:
        try:
            params = GetFileInput(
                file=file,
                course=course,
                mode=mode,  # type: ignore[arg-type]
                include_concluded=include_concluded,
            )
        except ValidationError as exc:
            message = "; ".join(
                error.get("msg", "Invalid input") for error in exc.errors()
            )
            raise_tool_error(QuercusValidationError(message))

        state = get_state()
        settings = state.quercus_settings
        try:
            file_id, file_meta, resolved_course = await _resolve_file_ref(
                params,
                state=state,
            )
            if params.mode == "metadata":
                if file_meta is None:
                    raw = await state.quercus_client.get_file(file_id)
                    file_meta = normalize_file(raw)
                result: dict[str, Any] = {"file": file_meta, "mode": "metadata"}
                if resolved_course is not None:
                    result["course"] = course_ref(resolved_course)
                return result

            content, content_type, filename = await state.quercus_client.download_file(
                file_id
            )
            if file_meta is None:
                file_meta = {
                    "id": file_id,
                    "display_name": filename,
                    "content_type": content_type,
                    "size": len(content),
                }

            if params.mode == "download":
                path = _save_download(
                    content,
                    file_id=file_id,
                    filename=filename or file_meta.get("display_name"),
                    download_dir=settings.resolved_download_dir,
                )
                result = {
                    "file": file_meta,
                    "mode": "download",
                    "path": str(path),
                    "content_type": content_type,
                    "size_bytes": len(content),
                }
                if resolved_course is not None:
                    result["course"] = course_ref(resolved_course)
                return result

            # mode == text
            extracted = extract_text(
                content,
                content_type=content_type,
                filename=filename or file_meta.get("display_name"),
                max_text_chars=settings.max_text_chars,
            )
            result = {
                "file": file_meta,
                "mode": "text",
                "content_type": content_type,
                "text": extracted.text,
                "truncated": extracted.truncated,
                "warnings": extracted.warnings,
                "size_bytes": len(content),
            }
            if resolved_course is not None:
                result["course"] = course_ref(resolved_course)
            return result
        except QuercusError as exc:
            raise_tool_error(exc)


async def _resolve_file_ref(
    params: GetFileInput,
    *,
    state: Any,
) -> tuple[int | str, dict[str, Any] | None, dict[str, Any] | None]:
    """Return (file_id, optional normalized meta, optional resolved course)."""
    query = params.file
    if query.isdigit():
        return query, None, None

    if not params.course:
        raise QuercusValidationError(
            "course is required when file is not a numeric Canvas file id."
        )
    resolved_course = await resolve_course_ref(
        params.course,
        client=state.quercus_client,
        cache=state.quercus_course_cache,
        include_concluded=params.include_concluded,
    )
    raw_files = await state.quercus_client.list_files(resolved_course["id"])
    files = [normalize_file(item) for item in raw_files]
    matched = resolve_file_in_course(query, files)
    file_id = matched.get("id")
    if file_id is None:
        raise QuercusValidationError("Resolved file is missing an id.")
    return file_id, matched, resolved_course


def _save_download(
    content: bytes,
    *,
    file_id: int | str,
    filename: str | None,
    download_dir: Any,
) -> Any:
    from pathlib import Path

    directory = Path(download_dir)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / safe_filename(filename, file_id=file_id)
    path.write_bytes(content)
    return path.resolve()
