"""Client for the iMatrix integration API."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime

from .auth import AbstractAuth
from .const import DEVICE_PAGE_SIZE, MAX_DEVICES_PER_LATEST, MAX_HISTORY_SENSORS
from .exceptions import IMatrixResponseError
from .models import Account, Device, DeviceState, Faults, History, Latest, Trip


def _csv(values: Iterable[int]) -> str:
    return ",".join(str(v) for v in values)


def _window(start: datetime | None, end: datetime | None) -> dict[str, str]:
    params: dict[str, str] = {}
    if start is not None:
        params["from"] = start.isoformat()
    if end is not None:
        params["to"] = end.isoformat()
    return params


class IMatrixClient:
    """Read-only client. One instance per credential."""

    def __init__(self, auth: AbstractAuth) -> None:
        """Wrap an auth object that performs the requests."""
        self.auth = auth

    async def get_account(self) -> Account:
        """Return the org and permissions of the credential."""
        return Account.from_dict(await self.auth.request("/account"))

    async def get_devices(self, group_ids: Iterable[int] | None = None) -> list[Device]:
        """Return every visible device, following pagination. group_ids include subgroups."""
        params: dict[str, str] = {"limit": str(DEVICE_PAGE_SIZE)}
        if group_ids is not None:
            params["groupIds"] = _csv(group_ids)
        devices: list[Device] = []
        seen_cursors: set[str] = set()
        while True:
            body = await self.auth.request("/devices", params)
            devices.extend(Device.from_dict(d) for d in body.get("devices") or ())
            cursor = body.get("nextCursor")
            if not cursor:
                return devices
            if cursor in seen_cursors:
                raise IMatrixResponseError("device pagination returned a repeated cursor")
            seen_cursors.add(cursor)
            params["cursor"] = str(cursor)

    async def get_latest(
        self,
        sns: Iterable[int] | None = None,
        *,
        group_ids: Iterable[int] | None = None,
        since: datetime | None = None,
    ) -> Latest:
        """Return latest state. sns over 500 are split into several requests and merged."""
        base: dict[str, str] = {}
        if group_ids is not None:
            base["groupIds"] = _csv(group_ids)
        if since is not None:
            base["since"] = since.isoformat()
        if sns is None:
            return Latest.from_dict(await self.auth.request("/latest", base))

        sn_list = list(dict.fromkeys(sns))
        if not sn_list:
            return Latest(server_time=None, devices={})
        merged: dict[int, DeviceState] = {}
        server_time = None
        for start in range(0, len(sn_list), MAX_DEVICES_PER_LATEST):
            chunk = sn_list[start : start + MAX_DEVICES_PER_LATEST]
            body = await self.auth.request("/latest", {**base, "sns": _csv(chunk)})
            part = Latest.from_dict(body)
            merged.update(part.devices)
            # The earliest server time is the safe "since" for the next poll.
            if part.server_time and (server_time is None or part.server_time < server_time):
                server_time = part.server_time
        return Latest(server_time=server_time, devices=merged)

    async def get_faults(self, sn: int) -> Faults:
        """Return active and reported faults of the last 7 days, and the lamps."""
        return Faults.from_dict(await self.auth.request(f"/devices/{int(sn)}/faults"))

    async def get_history(
        self,
        sn: int,
        sensor_ids: Iterable[int],
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> History:
        """Return hourly history. Server limits: 1-20 sensors, at most 7 days."""
        ids = list(dict.fromkeys(sensor_ids))
        if not 1 <= len(ids) <= MAX_HISTORY_SENSORS:
            raise ValueError(f"sensor_ids must hold 1 to {MAX_HISTORY_SENSORS} ids")
        params = {"sensorIds": _csv(ids), "bucket": "1h", **_window(start, end)}
        return History.from_dict(await self.auth.request(f"/devices/{int(sn)}/history", params))

    async def get_trips(
        self, sn: int, start: datetime | None = None, end: datetime | None = None
    ) -> list[Trip]:
        """Return trips, newest first. Server limit: 31 days, default the last 7."""
        body = await self.auth.request(f"/devices/{int(sn)}/trips", _window(start, end))
        return [Trip.from_dict(t) for t in body.get("trips") or ()]
