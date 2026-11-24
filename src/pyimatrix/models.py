"""Typed models for the iMatrix integration API (schemaVersion 1)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Self

from .exceptions import IMatrixResponseError


class _ForwardCompatEnum(StrEnum):
    """StrEnum that maps unknown server values to UNKNOWN instead of raising."""

    @classmethod
    def parse(cls, value: Any) -> Self:
        try:
            return cls(value)
        except ValueError:
            return cls("unknown")


class DeviceKind(_ForwardCompatEnum):
    """What a device is, derived by the cloud from its product."""

    VEHICLE = "vehicle"
    ENVIRONMENTAL = "environmental"
    TRACKER = "tracker"
    GATEWAY = "gateway"
    OTHER = "other"

    @classmethod
    def parse(cls, value: Any) -> DeviceKind:
        """Map unknown kinds to OTHER (the backend's own catch-all)."""
        try:
            return cls(value)
        except ValueError:
            return cls.OTHER


class PlatformType(_ForwardCompatEnum):
    """Radio platform of the device's product."""

    BLE = "ble"
    WIFI = "wifi"
    CELLULAR = "cellular"
    UNKNOWN = "unknown"


class ConnectivityState(_ForwardCompatEnum):
    """Cloud classification of when the device last checked in."""

    ONLINE = "online"
    ASLEEP = "asleep"
    LATE = "late"
    OFFLINE = "offline"
    NEVER = "never"
    UNKNOWN = "unknown"


class ChannelMode(_ForwardCompatEnum):
    """Scalar channels carry numbers. Stateful channels carry a state value."""

    SCALAR = "scalar"
    STATEFUL = "stateful"
    UNKNOWN = "unknown"


class AlarmLevel(_ForwardCompatEnum):
    """Threshold level of a reading."""

    NONE = "none"
    ADVISORY = "advisory"
    WARNING = "warning"
    ALARM = "alarm"
    UNKNOWN = "unknown"


class AlarmSide(_ForwardCompatEnum):
    """Which threshold a reading crossed."""

    LOW = "low"
    HIGH = "high"
    UNKNOWN = "unknown"


def parse_time(value: Any) -> datetime | None:
    """Parse an ISO 8601 string into an aware UTC datetime. None stays None."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise IMatrixResponseError(f"expected ISO 8601 string, got {type(value).__name__}")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as err:
        raise IMatrixResponseError(f"invalid timestamp {value!r}") from err
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _require(data: dict[str, Any], key: str) -> Any:
    try:
        return data[key]
    except KeyError as err:
        raise IMatrixResponseError(f"missing field {key!r}") from err


def _opt_float(value: Any) -> float | None:
    return None if value is None else float(value)


@dataclass(frozen=True, slots=True)
class Limits:
    """Per-key limits the server enforces."""

    requests_per_minute: int
    stream_connections: int


@dataclass(frozen=True, slots=True)
class Account:
    """The org and credential the client is acting as."""

    org_id: int
    org_name: str | None
    key_prefix: str | None
    permissions: frozenset[str]
    expires_at: datetime | None
    limits: Limits

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Account:
        """Build from a GET /account body."""
        limits = data.get("limits") or {}
        return cls(
            org_id=int(_require(data, "orgId")),
            org_name=data.get("orgName"),
            key_prefix=data.get("keyPrefix"),
            permissions=frozenset(data.get("permissions") or ()),
            expires_at=parse_time(data.get("expiresAt")),
            limits=Limits(
                requests_per_minute=int(limits.get("requestsPerMinute", 0)),
                stream_connections=int(limits.get("streamConnections", 0)),
            ),
        )


@dataclass(frozen=True, slots=True)
class Connectivity:
    """Check-in state of a device."""

    state: ConnectivityState
    last_seen: datetime | None
    next_expected: datetime | None

    @property
    def is_reachable(self) -> bool:
        """True while the cloud still expects the device to report (spec 817 FR-H7)."""
        return self.state in (
            ConnectivityState.ONLINE,
            ConnectivityState.ASLEEP,
            ConnectivityState.LATE,
        )

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Connectivity:
        """Build from a connectivity object."""
        return cls(
            state=ConnectivityState.parse(data.get("state")),
            last_seen=parse_time(data.get("lastSeen")),
            next_expected=parse_time(data.get("nextExpected")),
        )


@dataclass(frozen=True, slots=True)
class ChannelState:
    """One allowed value of a stateful channel."""

    value: int
    label: str | None


@dataclass(frozen=True, slots=True)
class Channel:
    """One sensor on a device. sensor_id is stable and never reused."""

    sensor_id: int
    key: str
    name: str | None
    quantity_kind: str | None
    unit: str | None
    data_type: str | None
    mode: ChannelMode
    states: tuple[ChannelState, ...]
    # Set instead of states when a stateful channel has more than 64 states.
    state_count: int | None = None

    @property
    def is_mapped(self) -> bool:
        """True when the cloud gave this channel a semantic key, not s<sensorId>."""
        return self.key != f"s{self.sensor_id}"

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Channel:
        """Build from a channel object."""
        sensor_id = int(_require(data, "sensorId"))
        state_count = data.get("stateCount")
        return cls(
            sensor_id=sensor_id,
            key=str(data.get("key") or f"s{sensor_id}"),
            name=data.get("name"),
            quantity_kind=data.get("quantityKind"),
            unit=data.get("unit"),
            data_type=data.get("dataType"),
            mode=ChannelMode.parse(data.get("mode")),
            states=tuple(
                ChannelState(value=int(s["value"]), label=s.get("label"))
                for s in data.get("states") or ()
            ),
            state_count=None if state_count is None else int(state_count),
        )


@dataclass(frozen=True, slots=True)
class Device:
    """A device the credential can see."""

    sn: int
    name: str | None
    kind: DeviceKind
    product_id: int | None
    product_name: str | None
    platform_type: PlatformType | None
    firmware: str | None
    group_id: int | None
    group_path: str | None
    via_sn: int | None
    via_seen_at: datetime | None
    connectivity: Connectivity
    channels: tuple[Channel, ...]

    def channel(self, key: str) -> Channel | None:
        """Return the channel with a semantic key, if the device has one."""
        return next((c for c in self.channels if c.key == key), None)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Device:
        """Build from a device object."""
        platform = data.get("platformType")
        product_id = data.get("productId")
        group_id = data.get("groupId")
        via_sn = data.get("viaSn")
        return cls(
            sn=int(_require(data, "sn")),
            name=data.get("name"),
            kind=DeviceKind.parse(data.get("kind")),
            product_id=None if product_id is None else int(product_id),
            product_name=data.get("productName"),
            platform_type=None if platform is None else PlatformType.parse(platform),
            firmware=data.get("firmware"),
            group_id=None if group_id is None else int(group_id),
            group_path=data.get("groupPath"),
            via_sn=None if via_sn is None else int(via_sn),
            via_seen_at=parse_time(data.get("viaSeenAt")),
            connectivity=Connectivity.from_dict(data.get("connectivity") or {}),
            channels=tuple(Channel.from_dict(c) for c in data.get("channels") or ()),
        )


@dataclass(frozen=True, slots=True)
class Position:
    """One GPS fix."""

    latitude: float
    longitude: float
    altitude_m: float | None
    heading_deg: float | None
    speed_kmh: float | None
    hdop: float | None
    satellites: int | None
    fix_time: datetime | None
    clock_skew: bool

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Position:
        """Build from a position object."""
        satellites = data.get("satellites")
        return cls(
            latitude=float(_require(data, "lat")),
            longitude=float(_require(data, "lng")),
            altitude_m=_opt_float(data.get("altitudeM")),
            heading_deg=_opt_float(data.get("headingDeg")),
            speed_kmh=_opt_float(data.get("speedKmh")),
            hdop=_opt_float(data.get("hdop")),
            satellites=None if satellites is None else int(satellites),
            fix_time=parse_time(data.get("fixTime")),
            clock_skew=bool(data.get("clockSkew", False)),
        )


@dataclass(frozen=True, slots=True)
class Reading:
    """Latest value of one channel, in the channel's canonical unit."""

    value: float | str | None
    time: datetime | None
    alarm: AlarmLevel
    alarm_side: AlarmSide | None
    clock_skew: bool

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Reading:
        """Build from a value object."""
        side = data.get("alarmSide")
        raw = data.get("v")
        value: float | str | None
        if raw is None or isinstance(raw, str):
            value = raw
        else:
            value = float(raw)
        return cls(
            value=value,
            time=parse_time(data.get("t")),
            alarm=AlarmLevel.parse(data.get("alarm", "none")),
            alarm_side=None if side is None else AlarmSide.parse(side),
            clock_skew=bool(data.get("clockSkew", False)),
        )


@dataclass(frozen=True, slots=True)
class DeviceState:
    """Latest connectivity, position and readings of one device."""

    connectivity: Connectivity
    position: Position | None
    readings: dict[int, Reading]

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DeviceState:
        """Build from one entry of GET /latest devices."""
        position = data.get("position")
        return cls(
            connectivity=Connectivity.from_dict(data.get("connectivity") or {}),
            position=None if position is None else Position.from_dict(position),
            readings={
                int(sensor_id): Reading.from_dict(v)
                for sensor_id, v in (data.get("values") or {}).items()
            },
        )


@dataclass(frozen=True, slots=True)
class Latest:
    """A GET /latest response."""

    server_time: datetime | None
    devices: dict[int, DeviceState]

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Latest:
        """Build from a GET /latest body."""
        return cls(
            server_time=parse_time(data.get("serverTime")),
            devices={
                int(sn): DeviceState.from_dict(d) for sn, d in (data.get("devices") or {}).items()
            },
        )


class FaultProtocol(_ForwardCompatEnum):
    """Diagnostic protocol of a fault."""

    J1939 = "j1939"
    OBD2 = "obd2"
    UNKNOWN = "unknown"


class FaultStatus(_ForwardCompatEnum):
    """Active: a SET was seen in the window. Reported: present, SET not seen."""

    ACTIVE = "active"
    REPORTED = "reported"
    UNKNOWN = "unknown"


class FaultSeverity(_ForwardCompatEnum):
    """Severity iMatrix assigns to a fault."""

    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"
    UNKNOWN = "unknown"


def _opt_bool(value: Any) -> bool | None:
    return None if value is None else bool(value)


def _opt_int(value: Any) -> int | None:
    return None if value is None else int(value)


@dataclass(frozen=True, slots=True)
class Lamps:
    """J1939 dashboard lamps. None means not available or reserved."""

    mil: bool | None
    red_stop: bool | None
    amber: bool | None
    protect: bool | None

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> Lamps | None:
        """Build from a lamps object, or None."""
        if data is None:
            return None
        return cls(
            mil=_opt_bool(data.get("mil")),
            red_stop=_opt_bool(data.get("redStop")),
            amber=_opt_bool(data.get("amber")),
            protect=_opt_bool(data.get("protect")),
        )


@dataclass(frozen=True, slots=True)
class Fault:
    """One active or reported fault. id is opaque and stable per occurrence."""

    id: str
    display_code: str
    name: str | None
    protocol: FaultProtocol
    status: FaultStatus
    severity: FaultSeverity
    ecu: str | None
    source_address: int | None
    spn: int | None
    fmi: int | None
    code: str | None
    occurrence: int | None
    set_time: datetime | None
    last_seen: datetime | None
    lamps: Lamps | None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Fault:
        """Build from a fault object."""
        return cls(
            id=str(_require(data, "id")),
            display_code=str(data.get("displayCode") or data.get("code") or data["id"]),
            name=data.get("name"),
            protocol=FaultProtocol.parse(data.get("protocol")),
            status=FaultStatus.parse(data.get("status")),
            severity=FaultSeverity.parse(data.get("severity")),
            ecu=data.get("ecu"),
            source_address=_opt_int(data.get("sourceAddress")),
            spn=_opt_int(data.get("spn")),
            fmi=_opt_int(data.get("fmi")),
            code=data.get("code"),
            occurrence=_opt_int(data.get("occurrence")),
            set_time=parse_time(data.get("setTs")),
            last_seen=parse_time(data.get("lastSeenTs")),
            lamps=Lamps.from_dict(data.get("lamps")),
        )


@dataclass(frozen=True, slots=True)
class Faults:
    """A GET /devices/:sn/faults response (last 7 days, newest first)."""

    sn: int
    lamps: Lamps | None
    lamps_time: datetime | None
    active_count: int | None
    truncated: bool
    faults: tuple[Fault, ...]

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Faults:
        """Build from a faults body."""
        return cls(
            sn=int(_require(data, "sn")),
            lamps=Lamps.from_dict(data.get("lamps")),
            lamps_time=parse_time(data.get("lampsTime")),
            active_count=_opt_int(data.get("activeDtcCount")),
            truncated=bool(data.get("truncated", False)),
            faults=tuple(Fault.from_dict(f) for f in data.get("faults") or ()),
        )


@dataclass(frozen=True, slots=True)
class HistoryPoint:
    """One UTC hour of a channel. Hours with no data are absent."""

    start: datetime
    mean: float
    min: float
    max: float
    last: float
    count: int


@dataclass(frozen=True, slots=True)
class HistorySeries:
    """Hourly points of one channel, in its canonical unit."""

    unit: str | None
    quantity_kind: str | None
    truncated: bool
    points: tuple[HistoryPoint, ...]


@dataclass(frozen=True, slots=True)
class History:
    """A GET /devices/:sn/history response."""

    sn: int
    start: datetime | None
    end: datetime | None
    series: dict[int, HistorySeries]

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> History:
        """Build from a history body."""
        series: dict[int, HistorySeries] = {}
        for sensor_id, raw in (data.get("series") or {}).items():
            points = []
            for p in raw.get("points") or ():
                start = parse_time(_require(p, "t"))
                if start is None:
                    raise IMatrixResponseError("history point without a time")
                points.append(
                    HistoryPoint(
                        start=start,
                        mean=float(p["mean"]),
                        min=float(p["min"]),
                        max=float(p["max"]),
                        last=float(p["last"]),
                        count=int(p.get("n", 0)),
                    )
                )
            series[int(sensor_id)] = HistorySeries(
                unit=raw.get("unit"),
                quantity_kind=raw.get("quantityKind"),
                truncated=bool(raw.get("truncated", False)),
                points=tuple(points),
            )
        return cls(
            sn=int(_require(data, "sn")),
            start=parse_time(data.get("from")),
            end=parse_time(data.get("to")),
            series=series,
        )


class TripStatus(_ForwardCompatEnum):
    """Whether a trip is still running."""

    ACTIVE = "active"
    COMPLETED = "completed"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class LatLng:
    """A plain coordinate pair."""

    latitude: float
    longitude: float

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> LatLng | None:
        """Build from {lat, lng}, or None."""
        if data is None:
            return None
        return cls(latitude=float(data["lat"]), longitude=float(data["lng"]))


@dataclass(frozen=True, slots=True)
class Trip:
    """One trip. An active trip has no end."""

    id: str
    status: TripStatus
    start: datetime | None
    end: datetime | None
    distance_km: float | None
    duration_s: int | None
    start_position: LatLng | None
    end_position: LatLng | None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Trip:
        """Build from a trip object."""
        return cls(
            id=str(_require(data, "id")),
            status=TripStatus.parse(data.get("status")),
            start=parse_time(data.get("start")),
            end=parse_time(data.get("end")),
            distance_km=_opt_float(data.get("distanceKm")),
            duration_s=_opt_int(data.get("durationS")),
            start_position=LatLng.from_dict(data.get("startPosition")),
            end_position=LatLng.from_dict(data.get("endPosition")),
        )


class EventType(_ForwardCompatEnum):
    """Event types in the integration event feed."""

    CONNECTIVITY_CHANGED = "connectivity_changed"
    DTC_SET = "dtc_set"
    DTC_CLEARED = "dtc_cleared"
    LAMP_CHANGED = "lamp_changed"
    IGNITION_ON = "ignition_on"
    IGNITION_OFF = "ignition_off"
    GEOFENCE_ENTER = "geofence_enter"
    GEOFENCE_EXIT = "geofence_exit"
    GEOFENCE_DWELL = "geofence_dwell"
    ALERT_RAISED = "alert_raised"
    ALERT_RESOLVED = "alert_resolved"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class Event:
    """One event. id is unique per occurrence and increases monotonically."""

    id: int
    type: EventType
    raw_type: str
    sn: int
    time: datetime | None
    data: dict[str, Any]

    @property
    def fault(self) -> Fault | None:
        """The fault carried by dtc_set and dtc_cleared, else None."""
        if self.type not in (EventType.DTC_SET, EventType.DTC_CLEARED):
            return None
        return Fault.from_dict({**self.data, "id": self.data.get("faultId")})

    @property
    def lamps(self) -> Lamps | None:
        """The new lamp state carried by lamp_changed, else None."""
        if self.type is not EventType.LAMP_CHANGED:
            return None
        return Lamps.from_dict(self.data.get("lamps"))

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Event:
        """Build from an event object."""
        raw_type = str(data.get("type"))
        return cls(
            id=int(_require(data, "id")),
            type=EventType.parse(raw_type),
            raw_type=raw_type,
            sn=int(_require(data, "sn")),
            time=parse_time(data.get("ts")),
            data=dict(data.get("data") or {}),
        )


@dataclass(frozen=True, slots=True)
class EventPage:
    """A GET /events response. Always store next_after as the new cursor."""

    next_after: int
    has_more: bool
    events: tuple[Event, ...]

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EventPage:
        """Build from an events body."""
        return cls(
            next_after=int(_require(data, "nextAfter")),
            has_more=bool(data.get("hasMore", False)),
            events=tuple(Event.from_dict(e) for e in data.get("events") or ()),
        )
