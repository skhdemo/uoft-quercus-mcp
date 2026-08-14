"""Mocked HTTP tests for the Timetable Builder client."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx
import pytest
import respx

from uoft_quercus_mcp.timetable.client import TimetableClient
from uoft_quercus_mcp.timetable.errors import (
    TimetableRateLimitError,
    TimetableTimeoutError,
    TimetableUpstreamError,
)
from uoft_quercus_mcp.timetable.settings import Settings

FIXTURES = Path(__file__).parent / "fixtures"
BASE_URL = "https://api.easi.utoronto.ca/ttb"


def _load(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / name).read_text())


@pytest.fixture
def settings() -> Settings:
    return Settings(
        base_url=BASE_URL,
        connect_timeout_seconds=1.0,
        read_timeout_seconds=1.0,
        max_attempts=3,
        version="0.1.0",
    )


@pytest.fixture
def sleep_calls() -> list[float]:
    return []


@pytest.fixture
async def client(
    settings: Settings,
    sleep_calls: list[float],
) -> AsyncIterator[TimetableClient]:
    async def fake_sleep(seconds: float) -> None:
        sleep_calls.append(seconds)

    timetable = TimetableClient(settings, sleeper=fake_sleep)
    try:
        yield timetable
    finally:
        await timetable.aclose()


def _assert_common_headers(request: httpx.Request) -> None:
    assert request.headers["Accept"] == "application/json"
    assert request.headers["Origin"] == "https://ttb.utoronto.ca"
    assert request.headers["Referer"] == "https://ttb.utoronto.ca/"
    assert request.headers["User-Agent"] == "uoft-quercus-mcp/0.1.0"
    assert "Sec-CH-UA" not in request.headers


@respx.mock
@pytest.mark.asyncio
async def test_get_reference_data_success(client: TimetableClient) -> None:
    fixture = _load("reference_data.json")
    route = respx.get(f"{BASE_URL}/reference-data").mock(
        return_value=httpx.Response(200, json=fixture)
    )

    payload = await client.get_reference_data()

    assert route.called
    request = route.calls.last.request
    assert request.method == "GET"
    _assert_common_headers(request)
    assert "Content-Type" not in request.headers
    assert (
        payload["currentSessions"][0]["label"]
        == fixture["payload"]["currentSessions"][0]["label"]
    )


@respx.mock
@pytest.mark.asyncio
async def test_search_courses_payload_and_pagination(client: TimetableClient) -> None:
    fixture = _load("search_csc108.json")
    route = respx.post(f"{BASE_URL}/getPageableCourses").mock(
        return_value=httpx.Response(200, json=fixture)
    )

    pageable = await client.search_courses(
        course_code="CSC108H1",
        sessions=["20269"],
        divisions=["ARTSC"],
        page=2,
        page_size=10,
    )

    assert route.called
    request = route.calls.last.request
    assert request.method == "POST"
    _assert_common_headers(request)
    assert request.headers["Content-Type"] == "application/json"
    body = json.loads(request.content.decode())
    assert body["sessions"] == ["20269"]
    assert body["divisions"] == ["ARTSC"]
    assert body["page"] == 2
    assert body["pageSize"] == 10
    assert body["courseCodeAndTitleProps"]["courseCode"] == "CSC108H1"
    assert body["courseCodeAndTitleProps"]["courseTitle"] == ""
    assert pageable["total"] == fixture["payload"]["pageableCourse"]["total"]
    assert isinstance(pageable["courses"], list)


@respx.mock
@pytest.mark.asyncio
async def test_search_page_size_is_capped(client: TimetableClient) -> None:
    fixture = _load("search_csc108.json")
    route = respx.post(f"{BASE_URL}/getPageableCourses").mock(
        return_value=httpx.Response(200, json=fixture)
    )

    await client.search_courses(
        sessions=["20269"],
        divisions=["ARTSC"],
        page_size=999,
    )

    body = json.loads(route.calls.last.request.content.decode())
    assert body["pageSize"] == 50


@respx.mock
@pytest.mark.asyncio
async def test_get_courses_by_code_url_encodes_path(client: TimetableClient) -> None:
    fixture = _load("course_csc108.json")
    course_code = "CSC108H1/extra"
    encoded = quote(course_code, safe="")
    route = respx.get(f"{BASE_URL}/getCoursesByCodeAndSectionCode/{encoded}").mock(
        return_value=httpx.Response(200, json=fixture)
    )

    courses = await client.get_courses_by_code(course_code)

    assert route.called
    request = route.calls.last.request
    assert request.method == "GET"
    assert encoded in str(request.url)
    assert courses[0]["code"] == "CSC108H1"


@respx.mock
@pytest.mark.asyncio
async def test_payload_null_with_application_error_raises(
    client: TimetableClient,
) -> None:
    fixture = _load("upstream_error.json")
    respx.get(f"{BASE_URL}/reference-data").mock(
        return_value=httpx.Response(200, json=fixture)
    )

    with pytest.raises(TimetableUpstreamError) as exc_info:
        await client.get_reference_data()

    assert "unexpected error" in exc_info.value.message.lower()
    assert exc_info.value.retryable is False


@respx.mock
@pytest.mark.asyncio
async def test_invalid_json_raises_safe_upstream_error(client: TimetableClient) -> None:
    respx.get(f"{BASE_URL}/reference-data").mock(
        return_value=httpx.Response(
            200, text="not-json", headers={"Content-Type": "text/plain"}
        )
    )

    with pytest.raises(TimetableUpstreamError) as exc_info:
        await client.get_reference_data()

    assert "non-JSON" in exc_info.value.message
    assert exc_info.value.retryable is False


@respx.mock
@pytest.mark.asyncio
async def test_missing_expected_keys_raise_safe_upstream_error(
    client: TimetableClient,
) -> None:
    respx.post(f"{BASE_URL}/getPageableCourses").mock(
        return_value=httpx.Response(
            200,
            json={"payload": {"unexpected": True}, "status": []},
        )
    )

    with pytest.raises(TimetableUpstreamError) as exc_info:
        await client.search_courses(sessions=["20269"], divisions=["ARTSC"])

    assert "pageableCourse" in exc_info.value.message


@respx.mock
@pytest.mark.asyncio
async def test_timeout_is_retried_then_raises_timeout_error(
    client: TimetableClient,
    sleep_calls: list[float],
) -> None:
    route = respx.get(f"{BASE_URL}/reference-data").mock(
        side_effect=httpx.ReadTimeout("read timed out")
    )

    with pytest.raises(TimetableTimeoutError):
        await client.get_reference_data()

    assert route.call_count == 3
    assert sleep_calls == [0.25, 0.75]


@respx.mock
@pytest.mark.asyncio
async def test_503_is_retried_then_raises(
    client: TimetableClient,
    sleep_calls: list[float],
) -> None:
    route = respx.get(f"{BASE_URL}/reference-data").mock(
        return_value=httpx.Response(503, text="unavailable")
    )

    with pytest.raises(TimetableUpstreamError) as exc_info:
        await client.get_reference_data()

    assert route.call_count == 3
    assert exc_info.value.retryable is True
    assert sleep_calls == [0.25, 0.75]


@respx.mock
@pytest.mark.asyncio
async def test_400_is_not_retried(
    client: TimetableClient, sleep_calls: list[float]
) -> None:
    route = respx.get(f"{BASE_URL}/reference-data").mock(
        return_value=httpx.Response(400, text="bad request")
    )

    with pytest.raises(TimetableUpstreamError) as exc_info:
        await client.get_reference_data()

    assert route.call_count == 1
    assert sleep_calls == []
    assert exc_info.value.retryable is False


@respx.mock
@pytest.mark.asyncio
async def test_429_respects_retry_after_without_real_sleep(
    client: TimetableClient,
    sleep_calls: list[float],
) -> None:
    route = respx.get(f"{BASE_URL}/reference-data").mock(
        side_effect=[
            httpx.Response(429, headers={"Retry-After": "7"}),
            httpx.Response(200, json=_load("reference_data.json")),
        ]
    )

    payload = await client.get_reference_data()

    assert route.call_count == 2
    assert sleep_calls == [7.0]
    assert "currentSessions" in payload


@respx.mock
@pytest.mark.asyncio
async def test_429_exhausted_raises_rate_limit_error(
    client: TimetableClient,
    sleep_calls: list[float],
) -> None:
    respx.get(f"{BASE_URL}/reference-data").mock(
        return_value=httpx.Response(429, headers={"Retry-After": "1"})
    )

    with pytest.raises(TimetableRateLimitError):
        await client.get_reference_data()

    assert sleep_calls == [1.0, 1.0]


@pytest.mark.asyncio
async def test_http_client_closed_cleanly(settings: Settings) -> None:
    owned = TimetableClient(settings)
    await owned.aclose()

    external = httpx.AsyncClient(base_url=settings.base_url)
    wrapped = TimetableClient(settings, http_client=external)
    await wrapped.aclose()
    # External client remains usable because TimetableClient does not own it.
    assert not external.is_closed
    await external.aclose()
    assert external.is_closed
