from datetime import UTC, datetime

from pyimatrix import EventType, FaultSeverity, IMatrixClient

from .conftest import FakeApi, fixture


async def test_start_cursor(client: IMatrixClient, api: FakeApi) -> None:
    api.add("/events", {"schemaVersion": 1, "nextAfter": 900, "hasMore": False})
    page = await client.get_events()
    assert page.next_after == 900
    assert page.events == ()
    assert api.seen[0].query == {}


async def test_events_page(client: IMatrixClient, api: FakeApi) -> None:
    api.add("/events", fixture("events.json"))
    page = await client.get_events(100, types=[EventType.DTC_SET, "lamp_changed"], limit=50)
    assert api.seen[0].query == {
        "after": "100",
        "types": "dtc_set,lamp_changed",
        "limit": "50",
    }
    assert page.next_after == 107
    assert page.has_more is True
    dtc_set, dtc_cleared, lamp, fence, alert, odd = page.events

    assert dtc_set.type is EventType.DTC_SET
    assert dtc_set.time == datetime(2026, 11, 23, 14, tzinfo=UTC)
    fault = dtc_set.fault
    assert fault is not None
    assert fault.id == "j1939:0:110:0@1763906400000"
    assert fault.severity is FaultSeverity.CRITICAL
    assert dtc_set.lamps is None

    cleared = dtc_cleared.fault
    assert cleared is not None
    assert cleared.display_code == "P0420"
    assert dtc_cleared.data["inferred"] is True

    assert lamp.fault is None
    assert lamp.lamps is not None
    assert lamp.lamps.red_stop is True

    assert fence.type is EventType.GEOFENCE_ENTER
    assert fence.data["geofenceName"] == "Yard"
    assert alert.type is EventType.ALERT_RAISED
    assert alert.sn == 1000000002

    assert odd.type is EventType.UNKNOWN
    assert odd.raw_type == "teleport"
    assert odd.data == {}
    assert odd.time is None
