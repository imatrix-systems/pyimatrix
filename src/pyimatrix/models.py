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
    GATEWAY = "gateway"
    OTHER = "other"
    UNKNOWN = "unknown"


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
