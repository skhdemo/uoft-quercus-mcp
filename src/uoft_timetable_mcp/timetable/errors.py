"""Timetable domain exceptions."""

from __future__ import annotations

from uoft_timetable_mcp.common.errors import DomainError, to_mcp_error

__all__ = [
    "CourseNotFoundError",
    "TimetableError",
    "TimetableNetworkError",
    "TimetableRateLimitError",
    "TimetableTimeoutError",
    "TimetableUpstreamError",
    "TimetableValidationError",
    "to_mcp_error",
]


class TimetableError(DomainError):
    """Base error for timetable MCP domain failures."""

    code: str = "upstream_error"
    retryable: bool = False


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
