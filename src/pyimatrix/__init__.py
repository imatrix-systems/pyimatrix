"""Async client for the iMatrix Cloud integration API."""

from .auth import AbstractAuth, ApiKeyAuth, TokenAuth
from .client import IMatrixClient
from .const import DEFAULT_BASE_URL, SCHEMA_VERSION, VERSION
from .exceptions import (
    IMatrixAuthError,
    IMatrixConnectionError,
    IMatrixError,
    IMatrixNotFoundError,
    IMatrixPermissionError,
    IMatrixRateLimitError,
    IMatrixResponseError,
    IMatrixUnsupportedSchemaError,
)
from .models import (
    Account,
    AlarmLevel,
    AlarmSide,
    Channel,
    ChannelMode,
    ChannelState,
    Connectivity,
    ConnectivityState,
    Device,
    DeviceKind,
    DeviceState,
    Latest,
    Limits,
    PlatformType,
    Position,
    Reading,
)

__version__ = VERSION

__all__ = [
    "DEFAULT_BASE_URL",
    "SCHEMA_VERSION",
    "AbstractAuth",
    "Account",
    "AlarmLevel",
    "AlarmSide",
    "ApiKeyAuth",
    "Channel",
    "ChannelMode",
    "ChannelState",
    "Connectivity",
    "ConnectivityState",
    "Device",
    "DeviceKind",
    "DeviceState",
    "IMatrixAuthError",
    "IMatrixClient",
    "IMatrixConnectionError",
    "IMatrixError",
    "IMatrixNotFoundError",
    "IMatrixPermissionError",
    "IMatrixRateLimitError",
    "IMatrixResponseError",
    "IMatrixUnsupportedSchemaError",
    "Latest",
    "Limits",
    "PlatformType",
    "Position",
    "Reading",
    "TokenAuth",
    "__version__",
]
