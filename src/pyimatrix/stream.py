"""Live stream client for GET /api/v1/integration/stream (backend B4)."""

from __future__ import annotations

import asyncio
import json
import logging
import random
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import aiohttp

from .auth import AbstractAuth
from .exceptions import (
    IMatrixAuthError,
    IMatrixConnectionError,
    IMatrixError,
    IMatrixNotFoundError,
    IMatrixPermissionError,
)
from .models import Connectivity, Event, Position, Reading, parse_time

_LOGGER = logging.getLogger(__package__)

STREAM_PATH = "/stream"
ALL_TYPES = ("values", "position", "connectivity", "events")
CLOSE_UNAUTHORIZED = 4401
CLOSE_TOO_MANY_STREAMS = 4429
MIN_BACKOFF = 1.0
MAX_BACKOFF = 300.0
TOO_MANY_STREAMS_BACKOFF = 60.0


@dataclass(frozen=True, slots=True)
class ValuesFrame:
    """New readings of one device, in canonical units."""

    sn: int
    ingest_at: datetime | None
    readings: dict[int, Reading]


@dataclass(frozen=True, slots=True)
class PositionFrame:
    """A new GPS fix of one device."""

    sn: int
    ingest_at: datetime | None
    position: Position


@dataclass(frozen=True, slots=True)
class ConnectivityFrame:
    """A change in a device's check-in state."""

    sn: int
    connectivity: Connectivity


@dataclass(frozen=True, slots=True)
class EventFrame:
    """One feed event. event.id is the cursor to resume from."""

    event: Event


@dataclass(frozen=True, slots=True)
class SubscribedFrame:
    """The server accepted a subscription."""

    devices: int
    after: int | None


@dataclass(frozen=True, slots=True)
class ReplayDoneFrame:
    """Replay after `after` finished. If truncated, page the rest with /events."""

    replayed: int
    next_after: int
    truncated: bool


@dataclass(frozen=True, slots=True)
class ErrorFrame:
    """The server rejected a client message. The socket stays open."""

    code: str
    message: str


Frame = (
    ValuesFrame
    | PositionFrame
    | ConnectivityFrame
    | EventFrame
    | SubscribedFrame
    | ReplayDoneFrame
    | ErrorFrame
)


def _values(raw: dict[str, Any], data: dict[str, Any]) -> Frame:
    return ValuesFrame(
        sn=int(raw["sn"]),
        ingest_at=parse_time(raw.get("ingestAt")),
        readings={int(k): Reading.from_dict(v) for k, v in data.items()},
    )


def _position(raw: dict[str, Any], data: dict[str, Any]) -> Frame:
    return PositionFrame(
        sn=int(raw["sn"]),
        ingest_at=parse_time(raw.get("ingestAt")),
        position=Position.from_dict(data),
    )


def _connectivity(raw: dict[str, Any], data: dict[str, Any]) -> Frame:
    return ConnectivityFrame(sn=int(raw["sn"]), connectivity=Connectivity.from_dict(data))


def _event(raw: dict[str, Any], data: dict[str, Any]) -> Frame:
    return EventFrame(
        event=Event.from_dict(
            {
                "id": raw["eventId"],
                "sn": raw["sn"],
                "type": data.get("type"),
                "ts": data.get("ts"),
                "data": data.get("data"),
            }
        )
    )


def _subscribed(raw: dict[str, Any], data: dict[str, Any]) -> Frame:
    after = data.get("after")
    return SubscribedFrame(
        devices=int(data.get("devices", 0)), after=None if after is None else int(after)
    )


def _replay_done(raw: dict[str, Any], data: dict[str, Any]) -> Frame:
    return ReplayDoneFrame(
        replayed=int(data.get("replayed", 0)),
        next_after=int(data["nextAfter"]),
        truncated=bool(data.get("truncated", False)),
    )


def _error(raw: dict[str, Any], data: dict[str, Any]) -> Frame:
    return ErrorFrame(code=str(data.get("code")), message=str(data.get("message")))


_PARSERS: dict[str, Callable[[dict[str, Any], dict[str, Any]], Frame]] = {
    "values": _values,
    "position": _position,
    "connectivity": _connectivity,
    "event": _event,
    "subscribed": _subscribed,
    "replay_done": _replay_done,
    "error": _error,
}


def parse_frame(raw: dict[str, Any]) -> Frame | None:
    """Parse one server frame. Unknown frame types return None."""
    parser = _PARSERS.get(str(raw.get("type")))
    return None if parser is None else parser(raw, raw.get("data") or {})


class IMatrixStream:
    """Keeps one stream open, reconnecting with jittered backoff.

    Tracks the last event id it delivered and resubscribes with it as `after`, so no
    event is lost across a reconnect. Values are not replayed by the server: the
    caller should re-read /latest when `on_connected` fires after a gap.
    """

    def __init__(  # noqa: PLR0913 (keyword-only options)
        self,
        auth: AbstractAuth,
        on_frame: Callable[[Frame], None],
        *,
        types: Iterable[str] = ALL_TYPES,
        after: int | None = None,
        on_connected: Callable[[bool], Awaitable[None] | None] | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        jitter: Callable[[], float] = random.random,
    ) -> None:
        """Create a stream. `after` is the last event id already processed."""
        self._auth = auth
        self._on_frame = on_frame
        self._types = list(types)
        self.last_event_id = after
        self._on_connected = on_connected
        self._sleep = sleep
        self._jitter = jitter
        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._stopped = False
        self.connected = False
        self._reached = False
        self.reconnects = 0

    async def stop(self) -> None:
        """Stop the stream and close the socket."""
        self._stopped = True
        if self._ws is not None:
            await self._ws.close()

    async def run(self) -> None:
        """Run until stop(). Raises on errors that a retry cannot fix.

        IMatrixAuthError: credential rejected or revoked (HTTP 401, or close 4401
            before the session subscribed). A 4401 after subscribing means the
            access token expired, so the stream reconnects with a fresh one.
        IMatrixPermissionError: credential lacks permission (HTTP 403).
        IMatrixNotFoundError: the server has the stream turned off (HTTP 404).
        """
        backoff = MIN_BACKOFF
        while not self._stopped:
            try:
                reached = await self._run_once()
            except (IMatrixAuthError, IMatrixPermissionError, IMatrixNotFoundError):
                raise
            except _TooManyStreams:
                reached, backoff = False, max(backoff, TOO_MANY_STREAMS_BACKOFF)
            except IMatrixError as err:
                _LOGGER.debug("Stream dropped: %s", err)
                reached = self._reached
            if self._stopped:
                return
            if reached:
                backoff = MIN_BACKOFF
            self.reconnects += 1
            await self._sleep(backoff * (0.5 + self._jitter()))
            backoff = min(backoff * 2, MAX_BACKOFF)

    async def _run_once(self) -> bool:
        """Connect, subscribe and read until the socket closes. True once subscribed."""
        subscribed = self._reached = False
        self._ws = await self._auth.ws_connect(STREAM_PATH)
        try:
            await self._ws.send_json(
                {"op": "subscribe", "sns": "all", "types": self._types, "after": self.last_event_id}
            )
            async for msg in self._ws:
                if msg.type is not aiohttp.WSMsgType.TEXT:
                    continue
                try:
                    frame = parse_frame(json.loads(msg.data))
                except (ValueError, KeyError, TypeError, IMatrixError) as err:
                    _LOGGER.debug("Ignoring a stream frame that did not parse: %s", err)
                    continue
                if frame is None:
                    continue
                if isinstance(frame, SubscribedFrame) and not subscribed:
                    subscribed = self._reached = self.connected = True
                    await self._notify(True)
                elif isinstance(frame, EventFrame):
                    self.last_event_id = frame.event.id
                elif isinstance(frame, ReplayDoneFrame):
                    self.last_event_id = frame.next_after
                self._on_frame(frame)
            code = self._ws.close_code
        finally:
            if self.connected:
                self.connected = False
                await self._notify(False)
            await self._ws.close()
            self._ws = None
        self._raise_for_close(code, subscribed)
        return subscribed

    def _raise_for_close(self, code: int | None, subscribed: bool) -> None:
        """Turn the close code of a session into the error run() acts on."""
        if code == CLOSE_UNAUTHORIZED:
            if subscribed:
                # The server closes at the access token's expiry (every 15 min for OAuth).
                # Reconnect: ws_connect asks the auth for a fresh token. A revoked
                # credential then fails the upgrade with 401 and raises there.
                raise IMatrixConnectionError("stream closed: token expired")
            raise IMatrixAuthError("stream closed: credential revoked or expired")
        if code == CLOSE_TOO_MANY_STREAMS:
            raise _TooManyStreams
        if not self._stopped:
            raise IMatrixConnectionError(f"stream closed with code {code}")

    async def _notify(self, connected: bool) -> None:
        if self._on_connected is not None:
            result = self._on_connected(connected)
            if result is not None:
                await result


class _TooManyStreams(IMatrixError):
    """Close 4429: this credential already has the maximum number of streams."""
