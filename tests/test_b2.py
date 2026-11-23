from datetime import UTC, datetime

import pytest

from pyimatrix import (
    FaultProtocol,
    FaultSeverity,
    FaultStatus,
    IMatrixClient,
    IMatrixNotFoundError,
    TripStatus,
)

from .conftest import FakeApi, fixture

START = datetime(2026, 11, 23, tzinfo=UTC)
END = datetime(2026, 11, 23, 3, tzinfo=UTC)


async def test_get_faults(client: IMatrixClient, api: FakeApi) -> None:
    api.add("/devices/1000000001/faults", fixture("faults.json"))
    faults = await client.get_faults(1000000001)
    assert faults.sn == 1000000001
    assert faults.active_count == 2
    assert faults.truncated is False
    assert faults.lamps is not None
    assert faults.lamps.mil is True
    assert faults.lamps.red_stop is False
    assert faults.lamps.amber is None
    assert faults.lamps_time == datetime(2026, 11, 23, 14, tzinfo=UTC)

    j1939, obd, odd = faults.faults
    assert j1939.protocol is FaultProtocol.J1939
    assert j1939.status is FaultStatus.ACTIVE
    assert j1939.severity is FaultSeverity.CRITICAL
    assert (j1939.source_address, j1939.spn, j1939.fmi) == (0, 110, 0)
    assert j1939.display_code == "SPN 110 FMI 0"
    assert j1939.lamps is not None and j1939.lamps.red_stop is True
    assert obd.status is FaultStatus.REPORTED
    assert obd.code == "P0420"
    assert obd.lamps is None
    assert obd.last_seen is None
    assert odd.protocol is FaultProtocol.UNKNOWN
    assert odd.status is FaultStatus.UNKNOWN
    assert odd.severity is FaultSeverity.UNKNOWN
    assert odd.display_code == "x@1"
    assert odd.set_time is None


async def test_get_faults_without_lamps(client: IMatrixClient, api: FakeApi) -> None:
    api.add("/devices/5/faults", {"schemaVersion": 1, "sn": 5, "lamps": None})
    faults = await client.get_faults(5)
    assert faults.lamps is None
    assert faults.faults == ()
    assert faults.active_count is None


async def test_get_faults_not_found(client: IMatrixClient, api: FakeApi) -> None:
    api.add(
        "/devices/7/faults",
        {"schemaVersion": 1, "error": {"code": "DEVICE_NOT_FOUND"}},
        status=404,
    )
    with pytest.raises(IMatrixNotFoundError):
        await client.get_faults(7)


async def test_get_history(client: IMatrixClient, api: FakeApi) -> None:
    api.add("/devices/1000000001/history", fixture("history.json"))
    history = await client.get_history(1000000001, [64, 150, 64], START, END)
    assert api.seen[0].query == {
        "sensorIds": "64,150",
        "bucket": "1h",
        "from": START.isoformat(),
        "to": END.isoformat(),
    }
    assert history.start == START
    odo = history.series[64]
    assert odo.unit == "km"
    assert [p.last for p in odo.points] == [101.0, 103.0]
    assert odo.points[1].start == datetime(2026, 11, 23, 2, tzinfo=UTC)
    assert odo.points[0].count == 60
    assert history.series[150].truncated is True
    assert history.series[150].points == ()


async def test_get_history_defaults(client: IMatrixClient, api: FakeApi) -> None:
    api.add("/devices/1/history", {"schemaVersion": 1, "sn": 1})
    history = await client.get_history(1, [64])
    assert api.seen[0].query == {"sensorIds": "64", "bucket": "1h"}
    assert history.series == {}
    assert history.end is None


async def test_get_history_point_without_time(client: IMatrixClient, api: FakeApi) -> None:
    body = {
        "schemaVersion": 1,
        "sn": 1,
        "series": {"64": {"points": [{"t": None, "mean": 1, "min": 1, "max": 1, "last": 1}]}},
    }
    api.add("/devices/1/history", body)
    with pytest.raises(Exception, match="without a time"):
        await client.get_history(1, [64])


@pytest.mark.parametrize("ids", [[], list(range(21))])
async def test_get_history_sensor_limits(client: IMatrixClient, ids: list[int]) -> None:
    with pytest.raises(ValueError, match="1 to 20"):
        await client.get_history(1, ids)


async def test_get_trips(client: IMatrixClient, api: FakeApi) -> None:
    api.add("/devices/1000000001/trips", fixture("trips.json"))
    active, done = await client.get_trips(1000000001, START)
    assert api.seen[0].query == {"from": START.isoformat()}
    assert active.status is TripStatus.ACTIVE
    assert active.end is None
    assert active.distance_km == 12.4
    assert active.start_position is not None
    assert active.start_position.latitude == 39.0
    assert active.end_position is None
    assert done.status is TripStatus.COMPLETED
    assert done.duration_s is None
    assert done.end_position is not None


async def test_get_trips_empty(client: IMatrixClient, api: FakeApi) -> None:
    api.add("/devices/2/trips", {"schemaVersion": 1, "sn": 2})
    assert await client.get_trips(2) == []
    assert api.seen[0].query == {}
