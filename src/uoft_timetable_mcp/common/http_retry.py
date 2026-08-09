"""Shared HTTP retry/backoff helpers for upstream clients."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable, Sequence
from email.utils import parsedate_to_datetime

import httpx

Sleeper = Callable[[float], Awaitable[None]]

RETRYABLE_STATUS_CODES = frozenset({429, 502, 503, 504})
DEFAULT_BACKOFF_SECONDS = (0.25, 0.75, 1.5)


async def default_sleeper(seconds: float) -> None:
    await asyncio.sleep(seconds)


def backoff_seconds(
    attempt: int,
    schedule: Sequence[float] = DEFAULT_BACKOFF_SECONDS,
) -> float:
    index = min(max(attempt - 1, 0), len(schedule) - 1)
    return schedule[index]


def parse_retry_after(value: str) -> float | None:
    stripped = value.strip()
    if not stripped:
        return None
    if stripped.isdigit():
        return float(stripped)
    try:
        when = parsedate_to_datetime(stripped)
    except (TypeError, ValueError, IndexError):
        return None
    delay = when.timestamp() - time.time()
    return max(delay, 0.0)


def retry_delay_seconds(response: httpx.Response, attempt: int) -> float:
    retry_after = response.headers.get("Retry-After")
    if retry_after:
        parsed = parse_retry_after(retry_after)
        if parsed is not None:
            return parsed
    return backoff_seconds(attempt)
