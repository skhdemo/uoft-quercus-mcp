"""Shared serialization helpers for MCP tool responses."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel


def model_to_public_dict(
    model: BaseModel,
    *,
    exclude_none: bool = False,
) -> dict[str, Any]:
    """Serialize a model for MCP responses with JSON-compatible values."""
    return model.model_dump(mode="json", exclude_none=exclude_none)
