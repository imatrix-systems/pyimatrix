from datetime import UTC, datetime

import pytest

from pyimatrix import (
    AlarmLevel,
    AlarmSide,
    ChannelMode,
    ConnectivityState,
    DeviceKind,
    IMatrixClient,
    IMatrixResponseError,
    PlatformType,
)

from .conftest import FAKE_KEY, FakeApi, fixture


async def test_get_account(client: IMatrixClient, api: FakeApi) -> None:
    api.add("/account", fixture("account.json"), headers={"RateLimit-Remaining": "119"})
    account = await client.get_account()
    assert account.org_id == 4242
    assert account.org_name == "Example Cold Chain"
    assert "device:read" in account.permissions
    assert account.expires_at == datetime(2027, 6, 1, tzinfo=UTC)
    assert account.limits.requests_per_minute == 120
    assert client.auth.rate_limit_remaining == 119
    headers = api.seen[0].headers
    assert headers["X-API-Key"] == FAKE_KEY
    assert headers["User-Agent"] == "pyimatrix/0.1.0 HomeAssistant/2026.11.2"
    assert headers["Accept"] == "application/json"


async def test_get_account_minimal(client: IMatrixClient, api: FakeApi) -> None:
    api.add("/account", {"schemaVersion": 1, "orgId": "7"})
    account = await client.get_account()
    assert account.org_id == 7
    assert account.key_prefix is None
    assert account.expires_at is None
    assert account.permissions == frozenset()
    assert account.limits.stream_connections == 0


async def test_get_devices_paginates(client: IMatrixClient, api: FakeApi) -> None:
    api.add("/devices", fixture("devices_page1.json"))
    api.add("/devices", fixture("devices_page2.json"))
    devices = await client.get_devices(group_ids=[10, 11])
    assert [d.sn for d in devices] == [1000000001, 1000000002, 1000000003]

    truck, freezer, odd = devices
    assert truck.kind is DeviceKind.VEHICLE
    assert truck.platform_type is PlatformType.CELLULAR
    assert truck.connectivity.state is ConnectivityState.ONLINE
    assert truck.connectivity.is_reachable
    odometer = truck.channel("odometer")
    assert odometer is not None and odometer.is_mapped
    assert truck.channel("missing") is None
    can_odo = truck.channels[1]
    assert can_odo.key == "s617"
    assert not can_odo.is_mapped
    ignition = truck.channels[2]
    assert ignition.mode is ChannelMode.STATEFUL
    assert [s.label for s in ignition.states] == ["Off", "On"]
    assert ignition.state_count is None
    dtc = truck.channels[3]
    assert dtc.states == ()
    assert dtc.state_count == 12128

    assert freezer.via_sn == 1000000001
    assert freezer.connectivity.state is ConnectivityState.ASLEEP
    assert freezer.connectivity.is_reachable
    assert freezer.channels[0].unit == "°C"

    assert odd.kind is DeviceKind.OTHER
    assert odd.platform_type is PlatformType.UNKNOWN
    assert odd.connectivity.state is ConnectivityState.UNKNOWN
    assert not odd.connectivity.is_reachable
    assert odd.product_id is None
    assert odd.group_id is None
    assert odd.via_sn is None

    first, second = api.seen
    assert first.query == {"limit": "500", "groupIds": "10,11"}
    assert second.query["cursor"] == "c2"


async def test_get_devices_no_groups(client: IMatrixClient, api: FakeApi) -> None:
    api.add("/devices", {"schemaVersion": 1, "devices": [{"sn": 5, "channels": [{"sensorId": 9}]}]})
    (device,) = await client.get_devices()
    assert device.channels[0].key == "s9"
    assert device.platform_type is None
    assert "groupIds" not in api.seen[0].query


async def test_get_devices_repeated_cursor(client: IMatrixClient, api: FakeApi) -> None:
    api.add("/devices", {"schemaVersion": 1, "nextCursor": "same", "devices": []})
    with pytest.raises(IMatrixResponseError, match="repeated cursor"):
        await client.get_devices()


async def test_get_latest_by_sns(client: IMatrixClient, api: FakeApi) -> None:
    api.add("/latest", fixture("latest.json"))
    since = datetime(2026, 11, 23, 14, 0, tzinfo=UTC)
    latest = await client.get_latest([1000000001, 1000000002, 1000000001], since=since)
    query = api.seen[0].query
    assert query["sns"] == "1000000001,1000000002"
    assert query["since"] == "2026-11-23T14:00:00+00:00"
    assert latest.server_time == datetime(2026, 11, 23, 15, tzinfo=UTC)

    truck = latest.devices[1000000001]
    assert truck.position is not None
    assert truck.position.latitude == 39.1
    assert truck.position.satellites == 11
    assert truck.position.clock_skew is False
    assert truck.readings[64].value == 120455.2
    assert truck.readings[64].alarm is AlarmLevel.NONE
    assert truck.readings[64].alarm_side is None

    freezer = latest.devices[1000000002]
    assert freezer.position is None
    probe = freezer.readings[250001]
    assert probe.alarm is AlarmLevel.ALARM
    assert probe.alarm_side is AlarmSide.HIGH
    assert probe.clock_skew is True
    odd = freezer.readings[250002]
    assert odd.value == "fault"
    assert odd.alarm is AlarmLevel.UNKNOWN
    assert odd.alarm_side is AlarmSide.UNKNOWN
    assert odd.time == datetime(2026, 11, 23, 11, tzinfo=UTC)
    empty = freezer.readings[250003]
    assert empty.value is None
    assert empty.time is None


async def test_get_latest_by_group(client: IMatrixClient, api: FakeApi) -> None:
    api.add("/latest", {"schemaVersion": 1, "serverTime": None, "devices": {}})
    latest = await client.get_latest(group_ids=[3])
    assert latest.devices == {}
    assert api.seen[0].query == {"groupIds": "3"}


async def test_get_latest_empty_sns(client: IMatrixClient, api: FakeApi) -> None:
    latest = await client.get_latest([])
    assert latest.devices == {}
    assert api.seen == []


async def test_get_latest_chunks_over_500(client: IMatrixClient, api: FakeApi) -> None:
    def body(sn: int, server_time: str | None) -> dict[str, object]:
        return {
            "schemaVersion": 1,
            "serverTime": server_time,
            "devices": {str(sn): {"connectivity": {"state": "online"}, "values": {}}},
        }

    api.add("/latest", body(1, "2026-11-23T15:00:05Z"))
    api.add("/latest", body(501, "2026-11-23T15:00:01Z"))
    api.add("/latest", body(1001, None))
    latest = await client.get_latest(range(1, 1102))
    assert set(latest.devices) == {1, 501, 1001}
    assert latest.server_time == datetime(2026, 11, 23, 15, 0, 1, tzinfo=UTC)
    assert [len(s.query["sns"].split(",")) for s in api.seen] == [500, 500, 101]


async def test_floor_plans_and_desired(client: IMatrixClient, api: FakeApi) -> None:
    """Spec 822 FR-22: list plans and fetch the desired HA state with instance and policy."""
    plan = {"id": "p1", "groupId": 7, "name": "Cold store", "revision": 4}
    api.add("/floor-plans", {"schemaVersion": 1, "list": [plan]})
    api.add(
        "/floor-plans/p1/ha-desired",
        {
            "schemaVersion": 1,
            "revision": 4,
            "desired": {
                "floors": [{"fpm_id": "L0", "name": "Ground Floor", "level": 0}],
                "areas": [{"fpm_ids": ["s1"], "name": "Freezer"}],
                "labels": [],
                "device_assignments": [],
                "loss_report": {"dropped": [], "approximated": [], "notes": []},
            },
        },
    )
    plans = await client.get_floor_plans()
    assert plans[0].id == "p1" and plans[0].group_id == 7 and plans[0].revision == 4
    desired = await client.get_floor_plan_desired(
        "p1", instance_id="inst-0001", policy={"connector_mode": "split"}
    )
    assert desired.revision == 4
    assert desired.areas[0]["name"] == "Freezer"


async def test_ha_devices_put_and_delete(client: IMatrixClient, api: FakeApi) -> None:
    """Spec 822 FR-28, FR-29: the opt-in device list is a PUT and a DELETE."""
    api.add("/ha-devices", {"schemaVersion": 1, "count": 1})
    count = await client.put_ha_devices(
        "inst-0001",
        [
            {
                "haDeviceId": "d1",
                "name": "Hall light",
                "integrationDomain": "hue",
                "categories": ["light"],
            }
        ],
    )
    assert count == 1
    await client.delete_ha_devices("inst-0001")


async def test_floor_plan_level_card(client: IMatrixClient, api: FakeApi) -> None:
    """Spec 822 FR-33: a level drawn for an HA dashboard, markers as viewBox percentages."""
    api.add(
        "/floor-plans/p1/levels/l1/card",
        {
            "schemaVersion": 1,
            "revision": 5,
            "level": {"id": "l1", "name": "Ground", "ordinal": 0},
            "levels": [
                {"id": "l1", "name": "Ground", "ordinal": 0},
                {"id": "l2", "name": "Upstairs", "ordinal": 1},
            ],
            "svg": "<svg/>",
            "markers": [
                {
                    "placementId": "pl1",
                    "deviceId": "d1",
                    "name": "Freezer probe",
                    "category": "temperature_sensor",
                    "x": 12.5,
                    "y": 40,
                    "identifiers": [{"scheme": "imatrix_sn", "value": "123"}],
                    "entityHints": ["sensor.freezer"],
                }
            ],
        },
    )
    card = await client.get_floor_plan_level_card("p1", "l1", instance_id="inst-0001")
    assert card.revision == 5 and card.level.name == "Ground"
    assert [lv.ordinal for lv in card.levels] == [0, 1]
    marker = card.markers[0]
    assert (marker.placement_id, marker.x, marker.y) == ("pl1", 12.5, 40.0)
    assert marker.identifiers == [{"scheme": "imatrix_sn", "value": "123"}]
    assert marker.entity_hints == ["sensor.freezer"]
