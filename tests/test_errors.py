import aiohttp
import pytest

from pyimatrix import (
    ApiKeyAuth,
    IMatrixAuthError,
    IMatrixClient,
    IMatrixConnectionError,
    IMatrixNotFoundError,
    IMatrixPermissionError,
    IMatrixRateLimitError,
    IMatrixResponseError,
    IMatrixUnsupportedSchemaError,
    TokenAuth,
)
from pyimatrix.models import parse_time

from .conftest import FAKE_KEY, FakeApi

OK = {"schemaVersion": 1, "orgId": 1}


@pytest.mark.parametrize(
    ("status", "exc"),
    [
        (401, IMatrixAuthError),
        (403, IMatrixPermissionError),
        (404, IMatrixNotFoundError),
        (500, IMatrixConnectionError),
        (503, IMatrixConnectionError),
        (400, IMatrixResponseError),
    ],
)
async def test_status_mapping(
    client: IMatrixClient, api: FakeApi, status: int, exc: type[Exception]
) -> None:
    api.add("/account", {"schemaVersion": 1}, status=status)
    with pytest.raises(exc):
        await client.get_account()


@pytest.mark.parametrize(
    ("header", "expected"), [("30", 30.0), ("-5", 0.0), ("soon", None), (None, None)]
)
async def test_rate_limited(
    client: IMatrixClient, api: FakeApi, header: str | None, expected: float | None
) -> None:
    headers = {"Retry-After": header} if header is not None else {}
    api.add("/account", {}, status=429, headers=headers)
    with pytest.raises(IMatrixRateLimitError) as info:
        await client.get_account()
    assert info.value.retry_after == expected


async def test_timeout(session: aiohttp.ClientSession, api: FakeApi) -> None:
    api.add("/account", OK, delay=0.5)
    client = IMatrixClient(ApiKeyAuth(session, api.base_url, FAKE_KEY, timeout=0.05))
    with pytest.raises(IMatrixConnectionError, match="timeout"):
        await client.get_account()


async def test_connection_refused(session: aiohttp.ClientSession) -> None:
    client = IMatrixClient(ApiKeyAuth(session, "http://127.0.0.1:9", FAKE_KEY))
    with pytest.raises(IMatrixConnectionError, match="error calling"):
        await client.get_account()


async def test_invalid_json(client: IMatrixClient, api: FakeApi) -> None:
    api.add("/account", raw="<html>", headers={"Content-Type": "text/html"})
    with pytest.raises(IMatrixResponseError, match="invalid JSON"):
        await client.get_account()


async def test_non_object(client: IMatrixClient, api: FakeApi) -> None:
    api.add("/account", [1, 2])
    with pytest.raises(IMatrixResponseError, match="JSON object"):
        await client.get_account()


@pytest.mark.parametrize("version", [None, 2, "1"])
async def test_schema_version(client: IMatrixClient, api: FakeApi, version: object) -> None:
    api.add("/account", {"schemaVersion": version, "orgId": 1})
    with pytest.raises(IMatrixUnsupportedSchemaError):
        await client.get_account()


async def test_missing_required_field(client: IMatrixClient, api: FakeApi) -> None:
    api.add("/account", {"schemaVersion": 1})
    with pytest.raises(IMatrixResponseError, match="orgId"):
        await client.get_account()


async def test_non_numeric_rate_limit_header(client: IMatrixClient, api: FakeApi) -> None:
    api.add("/account", OK, headers={"RateLimit-Remaining": "x"})
    await client.get_account()
    assert client.auth.rate_limit_remaining is None


def test_parse_time_rejects_bad_values() -> None:
    with pytest.raises(IMatrixResponseError, match="ISO 8601"):
        parse_time(1763900000000)
    with pytest.raises(IMatrixResponseError, match="invalid timestamp"):
        parse_time("yesterday")


class _StaticToken(TokenAuth):
    async def async_get_access_token(self) -> str:
        return "tok_fake"


async def test_token_auth_sends_bearer(session: aiohttp.ClientSession, api: FakeApi) -> None:
    api.add("/account", OK)
    await IMatrixClient(_StaticToken(session, api.base_url)).get_account()
    headers = api.seen[0].headers
    assert headers["Authorization"] == "Bearer tok_fake"
    assert "X-API-Key" not in headers
    assert headers["User-Agent"] == "pyimatrix/0.1.1"
