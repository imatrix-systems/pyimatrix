"""Client for the iMatrix integration API."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime

from .auth import AbstractAuth
from .const import DEVICE_PAGE_SIZE, MAX_DEVICES_PER_LATEST
from .exceptions import IMatrixResponseError
from .models import Account, Device, DeviceState, Latest


def _csv(values: Iterable[int]) -> str:
    return ",".join(str(v) for v in values)


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
