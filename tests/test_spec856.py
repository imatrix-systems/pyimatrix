"""Spec 856: sensor order, visibility, virtual sensors and configVersion."""

from pyimatrix import EventType, IMatrixClient

from .conftest import FakeApi, fixture

DEVICE = {
    "sn": 7,
    "name": "Kitchen",
    "kind": "environmental",
    "configVersion": "0123456789abcdef",
    "channels": [
        {"sensorId": 3, "key": "humidity", "mode": "scalar", "visible": True, "position": 0},
        {"sensorId": 1, "key": "temperature", "mode": "scalar", "visible": False, "position": 1},
        {
            "sensorId": 5001,
            "key": "s5001",
            "name": "Dew point",
            "mode": "scalar",
            "visible": True,
            "position": 2,
            "virtual": True,
            "decimals": 1,
        },
    ],
}


async def test_devices_carry_order_visibility_and_version(
    client: IMatrixClient, api: FakeApi
) -> None:
    api.add("/devices", {"schemaVersion": 1, "devices": [DEVICE], "nextCursor": None})
    [device] = await client.get_devices(sns=[7, 8])
    assert api.seen[0].query["sns"] == "7,8"
    assert device.config_version == "0123456789abcdef"
    hum, temp, dew = device.channels
    assert (hum.visible, hum.position, hum.virtual) == (True, 0, False)
    assert (temp.visible, temp.position) == (False, 1)
    assert (dew.virtual, dew.decimals, dew.position) == (True, 1, 2)


async def test_older_server_defaults(client: IMatrixClient, api: FakeApi) -> None:
    api.add("/devices", fixture("devices_page2.json"))
    devices = await client.get_devices()
    channel = devices[0].channels[0]
    assert channel.visible is True
    assert channel.position is None
    assert channel.virtual is False
    assert channel.decimals is None
    assert devices[0].config_version is None
    assert "sns" not in api.seen[0].query


async def test_latest_version_and_error(client: IMatrixClient, api: FakeApi) -> None:
    api.add(
        "/latest",
        {
            "schemaVersion": 1,
            "serverTime": "2026-11-28T12:00:00Z",
            "devices": {
                "7": {
                    "connectivity": {"state": "online"},
                    "configVersion": "fedcba9876543210",
                    "values": {
                        "5001": {
                            "v": None,
                            "t": "2026-11-28T11:59:00Z",
                            "alarm": "none",
                            "alarmSide": None,
                            "error": {"code": "missing_input", "message": "A has no value"},
                        },
                        "1": {
                            "v": 21.5,
                            "t": "2026-11-28T11:59:00Z",
                            "alarm": "none",
                            "alarmSide": None,
                        },
                    },
                }
            },
        },
    )
    latest = await client.get_latest([7])
    state = latest.devices[7]
    assert state.config_version == "fedcba9876543210"
    assert state.readings[5001].value is None
    assert state.readings[5001].error_code == "missing_input"
    assert state.readings[5001].error_message == "A has no value"
    assert state.readings[1].error_code is None


async def test_device_config_changed_event(client: IMatrixClient, api: FakeApi) -> None:
    api.add(
        "/events",
        {
            "schemaVersion": 1,
            "nextAfter": 2,
            "hasMore": False,
            "events": [
                {
                    "id": 1,
                    "sn": 7,
                    "type": "device_config_changed",
                    "ts": "2026-11-28T12:00:00Z",
                    "data": {"configVersion": "abc"},
                },
                {"id": 2, "sn": 7, "type": "dtc_set", "ts": "2026-11-28T12:00:00Z", "data": {}},
            ],
        },
    )
    page = await client.get_events(0)
    changed, other = page.events
    assert changed.type is EventType.DEVICE_CONFIG_CHANGED
    assert changed.config_version == "abc"
    assert other.config_version is None
