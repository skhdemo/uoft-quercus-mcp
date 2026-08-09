"""Shared domain-error base and MCP-safe error payload mapping."""

from __future__ import annotations

from typing import Any


class DomainError(Exception):
    """Base error for MCP domain failures (timetable, Quercus, …)."""

    code: str = "upstream_error"
    retryable: bool = False

    def __init__(
        self,
        message: str,
        *,
        retryable: bool | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        if retryable is not None:
            self.retryable = retryable
        self.details = details


def to_mcp_error(exc: DomainError) -> dict[str, Any]:
    """Map a domain exception to the stable MCP error payload shape."""
    error: dict[str, Any] = {
        "code": exc.code,
        "message": exc.message,
        "retryable": bool(exc.retryable),
    }
    if exc.details:
        for key, value in exc.details.items():
            if key not in error:
                error[key] = value
    return {"error": error}
