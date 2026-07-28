"""Domain exceptions and MCP-safe error mapping."""

from __future__ import annotations

from typing import Any


class TimetableError(Exception):
    """Base error for timetable MCP domain failures."""

    code: str = "upstream_error"
    retryable: bool = False

    def __init__(self, message: str, *, retryable: bool | None = None) -> None:
        super().__init__(message)
        self.message = message
        if retryable is not None:
            self.retryable = retryable


class TimetableNetworkError(TimetableError):
    code = "network_error"
    retryable = True


class TimetableTimeoutError(TimetableError):
    code = "upstream_timeout"
    retryable = True


class TimetableRateLimitError(TimetableError):
    code = "rate_limited"
    retryable = True


class TimetableUpstreamError(TimetableError):
    code = "upstream_error"
    retryable = False


class TimetableValidationError(TimetableError):
    code = "invalid_input"
    retryable = False


class CourseNotFoundError(TimetableError):
    code = "course_not_found"
    retryable = False


def to_mcp_error(exc: TimetableError) -> dict[str, Any]:
    """Map a domain exception to the stable MCP error payload shape."""
    return {
        "error": {
            "code": exc.code,
            "message": exc.message,
            "retryable": bool(exc.retryable),
        }
    }
