"""Mocked HTTP tests for the Quercus Canvas client."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx

from uoft_timetable_mcp.quercus.auth import PersonalTokenAuth
from uoft_timetable_mcp.quercus.client import QuercusClient, parse_link_header
from uoft_timetable_mcp.quercus.errors import (
    QuercusAuthMissingError,
    QuercusAuthRejectedError,
    QuercusForbiddenError,
    QuercusRateLimitError,
    QuercusTimeoutError,
)
from uoft_timetable_mcp.quercus.settings import QuercusSettings

FIXTURES = Path(__file__).parent / "fixtures" / "quercus"
API_ROOT = "https://q.utoronto.ca/api/v1"
TOKEN = "test-token"


def _load(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text())


@pytest.fixture
def settings() -> QuercusSettings:
    return QuercusSettings(
        base_url="https://q.utoronto.ca",
        api_prefix="/api/v1",
        connect_timeout_seconds=1.0,
        read_timeout_seconds=1.0,
        max_attempts=3,
        max_page_size=100,
        version="0.1.0",
    )


@pytest.fixture
def sleep_calls() -> list[float]:
    return []


@pytest.fixture
async def client(
    settings: QuercusSettings,
    sleep_calls: list[float],
) -> AsyncIterator[QuercusClient]:
    async def fake_sleep(seconds: float) -> None:
        sleep_calls.append(seconds)

    quercus = QuercusClient(
        settings,
        auth=PersonalTokenAuth(TOKEN),
        sleeper=fake_sleep,
    )
    try:
        yield quercus
    finally:
        await quercus.aclose()


def _assert_quercus_headers(request: httpx.Request) -> None:
    assert request.headers["Accept"] == "application/json"
    assert request.headers["User-Agent"] == "uoft-timetable-mcp/0.1.0"
    assert request.headers["Authorization"] == f"Bearer {TOKEN}"
    assert "Origin" not in request.headers
    assert "Referer" not in request.headers


def test_parse_link_header_extracts_next() -> None:
    header = (
        f'<{API_ROOT}/courses?page=2&per_page=100>; rel="next", '
        f'<{API_ROOT}/courses?page=5&per_page=100>; rel="last"'
    )
    links = parse_link_header(header)
    assert links["next"] == f"{API_ROOT}/courses?page=2&per_page=100"
    assert links["last"] == f"{API_ROOT}/courses?page=5&per_page=100"


@respx.mock
@pytest.mark.asyncio
async def test_get_self_success(client: QuercusClient) -> None:
    fixture = _load("users_self.json")
    route = respx.get(f"{API_ROOT}/users/self").mock(
        return_value=httpx.Response(200, json=fixture)
    )

    payload = await client.get_self()

    assert route.called
    request = route.calls.last.request
    assert request.method == "GET"
    _assert_quercus_headers(request)
    assert payload["id"] == 12345
    assert payload["name"] == "Jane Student"


@respx.mock
@pytest.mark.asyncio
async def test_get_self_without_token(settings: QuercusSettings) -> None:
    async with QuercusClient(settings, auth=PersonalTokenAuth(None)) as client:
        with pytest.raises(QuercusAuthMissingError):
            await client.get_self()


@respx.mock
@pytest.mark.asyncio
async def test_get_self_401(client: QuercusClient) -> None:
    respx.get(f"{API_ROOT}/users/self").mock(
        return_value=httpx.Response(401, json={"errors": [{"message": "Invalid"}]})
    )

    with pytest.raises(QuercusAuthRejectedError) as exc_info:
        await client.get_self()

    assert exc_info.value.code == "quercus_auth_rejected"
    assert TOKEN not in exc_info.value.message


@respx.mock
@pytest.mark.asyncio
async def test_get_self_403(client: QuercusClient) -> None:
    respx.get(f"{API_ROOT}/users/self").mock(
        return_value=httpx.Response(403, json={"errors": [{"message": "Forbidden"}]})
    )

    with pytest.raises(QuercusForbiddenError) as exc_info:
        await client.get_self()

    assert exc_info.value.code == "quercus_forbidden"


@respx.mock
@pytest.mark.asyncio
async def test_timeout_is_retried_then_raises(
    client: QuercusClient,
    sleep_calls: list[float],
) -> None:
    route = respx.get(f"{API_ROOT}/users/self").mock(
        side_effect=httpx.ReadTimeout("read timed out")
    )

    with pytest.raises(QuercusTimeoutError):
        await client.get_self()

    assert route.call_count == 3
    assert sleep_calls == [0.25, 0.75]


@respx.mock
@pytest.mark.asyncio
async def test_429_then_success(
    client: QuercusClient,
    sleep_calls: list[float],
) -> None:
    fixture = _load("users_self.json")
    route = respx.get(f"{API_ROOT}/users/self").mock(
        side_effect=[
            httpx.Response(429, headers={"Retry-After": "2"}),
            httpx.Response(200, json=fixture),
        ]
    )

    payload = await client.get_self()

    assert route.call_count == 2
    assert sleep_calls == [2.0]
    assert payload["id"] == 12345


@respx.mock
@pytest.mark.asyncio
async def test_429_exhausted_raises_rate_limit(
    client: QuercusClient,
    sleep_calls: list[float],
) -> None:
    respx.get(f"{API_ROOT}/users/self").mock(
        return_value=httpx.Response(429, headers={"Retry-After": "1"})
    )

    with pytest.raises(QuercusRateLimitError):
        await client.get_self()

    assert sleep_calls == [1.0, 1.0]


@respx.mock
@pytest.mark.asyncio
async def test_list_courses_single_page(client: QuercusClient) -> None:
    page1 = _load("courses_page1.json")
    route = respx.get(f"{API_ROOT}/courses").mock(
        return_value=httpx.Response(200, json=page1)
    )

    courses = await client.list_courses()

    assert route.called
    request = route.calls.last.request
    _assert_quercus_headers(request)
    assert "enrollment_state=active" in str(request.url)
    assert "include%5B%5D=term" in str(request.url) or "include[]=term" in str(
        request.url
    )
    assert len(courses) == 2
    assert courses[0]["course_code"] == "CSCA08H3"


@respx.mock
@pytest.mark.asyncio
async def test_list_courses_multi_page(client: QuercusClient) -> None:
    page1 = _load("courses_page1.json")
    page2 = _load("courses_page2.json")
    next_url = f"{API_ROOT}/courses?page=2&per_page=100&enrollment_state=active"

    def _paged(request: httpx.Request) -> httpx.Response:
        if str(request.url) == next_url or request.url.params.get("page") == "2":
            return httpx.Response(200, json=page2)
        return httpx.Response(
            200,
            json=page1,
            headers={"Link": f'<{next_url}>; rel="next"'},
        )

    courses_pattern = API_ROOT.replace(".", r"\.") + r"/courses.*"
    route = respx.get(url__regex=courses_pattern).mock(side_effect=_paged)

    courses = await client.list_courses()

    assert route.call_count == 2
    assert len(courses) == 3
    assert courses[2]["course_code"] == "MATA30H3"


@pytest.mark.asyncio
async def test_http_client_closed_cleanly(settings: QuercusSettings) -> None:
    owned = QuercusClient(settings, auth=PersonalTokenAuth(TOKEN))
    await owned.aclose()

    external = httpx.AsyncClient(base_url=settings.api_root)
    wrapped = QuercusClient(
        settings,
        auth=PersonalTokenAuth(TOKEN),
        http_client=external,
    )
    await wrapped.aclose()
    assert not external.is_closed
    await external.aclose()
    assert external.is_closed
