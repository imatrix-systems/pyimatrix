import asyncio
import json
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

import aiohttp
import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from pyimatrix import (
    ApiKeyAuth,
    ConnectivityFrame,
    ErrorFrame,
    EventFrame,
    Frame,
    IMatrixAuthError,
    IMatrixNotFoundError,
    IMatrixPermissionError,
    IMatrixStream,
    PositionFrame,
    ReplayDoneFrame,
    SubscribedFrame,
    ValuesFrame,
)
from pyimatrix.stream import parse_frame

from .conftest import FAKE_KEY

Script = Callable[[web.WebSocketResponse, dict[str, Any]], Awaitable[None]]


class FakeStream:
    """A stream server. Each connection runs the next script; the last one repeats."""

    def __init__(self) -> None:
        self.scripts: list[Script] = []
        self.subscriptions: list[dict[str, Any]] = []
        self.headers: list[dict[str, str]] = []
        self.status: int | None = None
        self.server: TestServer | None = None

    async def handle(self, request: web.Request) -> web.StreamResponse:
        self.headers.append(dict(request.headers))
        if self.status is not None:
            return web.Response(status=self.status)
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        sub = json.loads((await ws.receive()).data)
        self.subscriptions.append(sub)
        script = self.scripts.pop(0) if len(self.scripts) > 1 else self.scripts[0]
        await script(ws, sub)
        return ws

    @property
    def base_url(self) -> str:
        assert self.server is not None
        return str(self.server.make_url("/"))


@pytest.fixture
async def stream_api() -> AsyncIterator[FakeStream]:
    fake = FakeStream()
    app = web.Application()
    app.router.add_get("/api/v1/integration/stream", fake.handle)
    fake.server = TestServer(app)
    await fake.server.start_server()
    yield fake
    await fake.server.close()


def subscribed(devices: int = 2, after: int | None = None) -> dict[str, Any]:
    return {"type": "subscribed", "data": {"devices": devices, "types": [], "after": after}}


def event(eid: int) -> dict[str, Any]:
    return {
        "type": "event",
        "sn": 1,
        "eventId": eid,
        "data": {"type": "ignition_on", "ts": "2026-11-23T15:00:00Z", "data": {"value": 1}},
    }


class Recorder:
    def __init__(self) -> None:
        self.frames: list[Frame] = []
        self.states: list[bool] = []
        self.sleeps: list[float] = []
        self.stream: IMatrixStream | None = None
        self.stop_after_sleeps = 1

    def on_frame(self, frame: Frame) -> None:
        self.frames.append(frame)

    async def on_connected(self, connected: bool) -> None:
        self.states.append(connected)

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        if len(self.sleeps) >= self.stop_after_sleeps and self.stream:
            await self.stream.stop()


def make_stream(
    session: aiohttp.ClientSession, url: str, rec: Recorder, **kwargs: Any
) -> IMatrixStream:
    stream = IMatrixStream(
        ApiKeyAuth(session, url, FAKE_KEY),
        rec.on_frame,
        on_connected=rec.on_connected,
        sleep=rec.sleep,
        jitter=lambda: 0.5,
        **kwargs,
    )
    rec.stream = stream
    return stream


async def test_frames_and_reconnect_resume(
    session: aiohttp.ClientSession, stream_api: FakeStream
) -> None:
    async def first(ws: web.WebSocketResponse, sub: dict[str, Any]) -> None:
        await ws.send_json(subscribed())
        await ws.send_json(
            {
                "type": "values",
                "sn": 1,
                "ingestAt": "2026-11-23T15:00:00Z",
                "data": {"64": {"v": 10.5, "t": "2026-11-23T15:00:00Z", "alarm": "none"}},
            }
        )
        await ws.send_json(
            {
                "type": "position",
                "sn": 1,
                "ingestAt": None,
                "data": {"lat": 1.0, "lng": 2.0, "fixTime": "2026-11-23T15:00:00Z"},
            }
        )
        await ws.send_json(
            {"type": "connectivity", "sn": 2, "data": {"state": "late", "lastSeen": None}}
        )
        await ws.send_json(event(41))
        await ws.send_json({"type": "error", "data": {"code": "BAD_OP", "message": "x"}})
        await ws.send_json({"type": "something_new", "data": {}})
        await ws.send_str("not json")
        await ws.send_bytes(b"\x00")
        await ws.send_json({"type": "values", "data": {}})  # missing sn
        await ws.close()

    async def second(ws: web.WebSocketResponse, sub: dict[str, Any]) -> None:
        await ws.send_json(subscribed(after=sub["after"]))
        await ws.send_json(
            {"type": "replay_done", "data": {"replayed": 0, "nextAfter": 50, "truncated": False}}
        )
        await ws.close()

    stream_api.scripts = [first, second]
    rec = Recorder()
    rec.stop_after_sleeps = 2
    stream = make_stream(session, stream_api.base_url, rec, after=7)
    await stream.run()

    first_sub, second_sub = stream_api.subscriptions
    assert first_sub == {
        "op": "subscribe",
        "sns": "all",
        "types": ["values", "position", "connectivity", "events"],
        "after": 7,
    }
    assert second_sub["after"] == 41  # resumes from the last event delivered
    assert stream.last_event_id == 50
    assert stream_api.headers[0]["X-API-Key"] == FAKE_KEY

    kinds = [type(f) for f in rec.frames]
    assert kinds == [
        SubscribedFrame,
        ValuesFrame,
        PositionFrame,
        ConnectivityFrame,
        EventFrame,
        ErrorFrame,
        SubscribedFrame,
        ReplayDoneFrame,
    ]
    values = rec.frames[1]
    assert isinstance(values, ValuesFrame)
    assert values.readings[64].value == 10.5
    position = rec.frames[2]
    assert isinstance(position, PositionFrame)
    assert position.ingest_at is None
    assert position.position.latitude == 1.0
    assert rec.states == [True, False, True, False]
    assert stream.reconnects == 2
    # A session that reached "subscribed" resets the backoff to 1 s (x jitter 1.0).
    assert rec.sleeps == [1.0, 1.0]


async def test_closed_4401_after_subscribe_reconnects(
    session: aiohttp.ClientSession, stream_api: FakeStream
) -> None:
    """The server closes at token expiry; the stream reconnects with a fresh token."""

    async def expired(ws: web.WebSocketResponse, sub: dict[str, Any]) -> None:
        await ws.send_json(subscribed())
        await ws.close(code=4401)

    stream_api.scripts = [expired]
    rec = Recorder()
    stream = make_stream(session, stream_api.base_url, rec)
    await stream.run()
    assert stream.reconnects == 1
    assert rec.sleeps == [1.0]
    assert rec.states == [True, False]


async def test_closed_4401_before_subscribe_raises_auth(
    session: aiohttp.ClientSession, stream_api: FakeStream
) -> None:
    async def revoked(ws: web.WebSocketResponse, sub: dict[str, Any]) -> None:
        await ws.close(code=4401)

    stream_api.scripts = [revoked]
    rec = Recorder()
    with pytest.raises(IMatrixAuthError):
        await make_stream(session, stream_api.base_url, rec).run()
    assert rec.states == []


async def test_too_many_streams_backs_off(
    session: aiohttp.ClientSession, stream_api: FakeStream
) -> None:
    async def full(ws: web.WebSocketResponse, sub: dict[str, Any]) -> None:
        await ws.close(code=4429)

    stream_api.scripts = [full]
    rec = Recorder()
    await make_stream(session, stream_api.base_url, rec).run()
    assert rec.sleeps == [60.0]
    assert rec.states == []


async def test_backoff_grows_while_never_subscribed(
    session: aiohttp.ClientSession, stream_api: FakeStream
) -> None:
    stream_api.status = 502
    rec = Recorder()
    rec.stop_after_sleeps = 4
    await make_stream(session, stream_api.base_url, rec).run()
    assert rec.sleeps == [1.0, 2.0, 4.0, 8.0]


@pytest.mark.parametrize(
    ("status", "exc"),
    [
        (401, IMatrixAuthError),
        (403, IMatrixPermissionError),
        (404, IMatrixNotFoundError),
    ],
)
async def test_upgrade_refusals(
    session: aiohttp.ClientSession,
    stream_api: FakeStream,
    status: int,
    exc: type[Exception],
) -> None:
    stream_api.status = status
    with pytest.raises(exc):
        await make_stream(session, stream_api.base_url, Recorder()).run()


async def test_connection_refused_retries(session: aiohttp.ClientSession) -> None:
    rec = Recorder()
    await make_stream(session, "http://127.0.0.1:9", rec).run()
    assert rec.sleeps == [1.0]


async def test_stop_while_connected(session: aiohttp.ClientSession, stream_api: FakeStream) -> None:
    async def hold(ws: web.WebSocketResponse, sub: dict[str, Any]) -> None:
        await ws.send_json(subscribed())
        async for _ in ws:
            pass

    stream_api.scripts = [hold]
    rec = Recorder()
    stream = make_stream(session, stream_api.base_url, rec)
    task = asyncio.create_task(stream.run())
    for _ in range(100):
        if stream.connected:
            break
        await asyncio.sleep(0.01)
    assert stream.connected
    await stream.stop()
    await asyncio.wait_for(task, 2)
    assert rec.sleeps == []
    assert not stream.connected


async def test_stop_before_run(session: aiohttp.ClientSession) -> None:
    stream = make_stream(session, "http://127.0.0.1:9", Recorder())
    await stream.stop()
    await stream.run()


def test_parse_frame_callbacks_without_handler() -> None:
    assert parse_frame({"type": "subscribed"}) == SubscribedFrame(devices=0, after=None)
    assert parse_frame({}) is None


async def test_sync_on_connected(session: aiohttp.ClientSession, stream_api: FakeStream) -> None:
    async def once(ws: web.WebSocketResponse, sub: dict[str, Any]) -> None:
        await ws.send_json(subscribed())
        await ws.close()

    stream_api.scripts = [once]
    calls: list[bool] = []
    rec = Recorder()
    stream = IMatrixStream(
        ApiKeyAuth(session, stream_api.base_url, FAKE_KEY),
        rec.on_frame,
        on_connected=calls.append,
        sleep=rec.sleep,
    )
    rec.stream = stream
    await stream.run()
    assert calls == [True, False]
    no_callback = IMatrixStream(
        ApiKeyAuth(session, stream_api.base_url, FAKE_KEY), rec.on_frame, sleep=rec.sleep
    )
    rec.stream = no_callback
    await no_callback.run()
