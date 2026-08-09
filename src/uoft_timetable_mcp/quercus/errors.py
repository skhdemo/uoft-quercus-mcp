"""Quercus domain exceptions."""

from __future__ import annotations

from typing import Any

from uoft_timetable_mcp.common.errors import DomainError, to_mcp_error

__all__ = [
    "QuercusAmbiguousError",
    "QuercusAuthMissingError",
    "QuercusAuthRejectedError",
    "QuercusError",
    "QuercusExtractError",
    "QuercusFileTooLargeError",
    "QuercusForbiddenError",
    "QuercusNetworkError",
    "QuercusNotFoundError",
    "QuercusRateLimitError",
    "QuercusTimeoutError",
    "QuercusUpstreamError",
    "QuercusValidationError",
    "to_mcp_error",
]


class QuercusError(DomainError):
    """Base error for Quercus MCP domain failures."""

    code: str = "quercus_upstream_error"
    retryable: bool = False


class QuercusAuthMissingError(QuercusError):
    code = "quercus_auth_missing"
    retryable = False


class QuercusAuthRejectedError(QuercusError):
    code = "quercus_auth_rejected"
    retryable = False


class QuercusForbiddenError(QuercusError):
    code = "quercus_forbidden"
    retryable = False


class QuercusNetworkError(QuercusError):
    code = "quercus_network_error"
    retryable = True


class QuercusTimeoutError(QuercusError):
    code = "quercus_upstream_timeout"
    retryable = True


class QuercusRateLimitError(QuercusError):
    code = "quercus_rate_limited"
    retryable = True


class QuercusUpstreamError(QuercusError):
    code = "quercus_upstream_error"
    retryable = False


class QuercusValidationError(QuercusError):
    code = "quercus_invalid_input"
    retryable = False


class QuercusNotFoundError(QuercusError):
    code = "quercus_not_found"
    retryable = False


class QuercusAmbiguousError(QuercusError):
    code = "quercus_ambiguous"
    retryable = False

    def __init__(
        self,
        message: str,
        *,
        candidates: list[dict[str, Any]] | None = None,
        retryable: bool | None = None,
    ) -> None:
        details = {"candidates": candidates} if candidates is not None else None
        super().__init__(message, retryable=retryable, details=details)
        self.candidates = candidates or []


class QuercusFileTooLargeError(QuercusError):
    code = "quercus_file_too_large"
    retryable = False


class QuercusExtractError(QuercusError):
    code = "quercus_extract_failed"
    retryable = False
