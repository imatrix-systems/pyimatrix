import asyncio
import json
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import aiohttp
import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from pyimatrix import ApiKeyAuth, IMatrixClient

FAKE_KEY = "imx_00000000_fakefakefakefakefakefakefakefake"
FIXTURES = Path(__file__).parent / "fixtures"


def fixture(name: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((FIXTURES / name).read_text())
    return data


@dataclass
class Reply:
    body: Any = None
    status: int = 200
    headers: dict[str, str] = field(default_factory=dict)
    raw: str | None = None
    delay: float = 0.0


@dataclass
class Seen:
    path: str
    query: dict[str, str]
    headers: dict[str, str]


class FakeApi:
    """A local HTTP server. Queue replies per path; requests are recorded in order."""

    def __init__(self) -> None:
        self.replies: dict[str, list[Reply]] = {}
        self.seen: list[Seen] = []
        self.server: TestServer | None = None

    def add(self, path: str, body: Any = None, **kwargs: Any) -> None:
        self.replies.setdefault(path, []).append(Reply(body, **kwargs))

    async def handle(self, request: web.Request) -> web.StreamResponse:
        path = request.path.removeprefix("/api/v1/integration")
        self.seen.append(Seen(path, dict(request.query), dict(request.headers)))
        queue = self.replies.get(path) or []
        reply = queue.pop(0) if len(queue) > 1 else queue[0]
        if reply.delay:
            await asyncio.sleep(reply.delay)
        if reply.raw is not None:
            return web.Response(text=reply.raw, status=reply.status, headers=reply.headers)
        return web.json_response(reply.body, status=reply.status, headers=reply.headers)

    @property
    def base_url(self) -> str:
        assert self.server is not None
        return str(self.server.make_url("/"))


@pytest.fixture
async def api() -> AsyncIterator[FakeApi]:
    fake = FakeApi()
    app = web.Application()
    app.router.add_get("/{tail:.*}", fake.handle)
    fake.server = TestServer(app)
    await fake.server.start_server()
    yield fake
    await fake.server.close()


@pytest.fixture
async def session() -> AsyncIterator[aiohttp.ClientSession]:
    async with aiohttp.ClientSession() as s:
        yield s


@pytest.fixture
def auth(session: aiohttp.ClientSession, api: FakeApi) -> ApiKeyAuth:
    return ApiKeyAuth(session, api.base_url, FAKE_KEY, user_agent="HomeAssistant/2026.11.2")


@pytest.fixture
def client(auth: ApiKeyAuth) -> IMatrixClient:
    return IMatrixClient(auth)
