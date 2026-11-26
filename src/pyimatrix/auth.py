"""Credential handling and the HTTP request layer."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Any

import aiohttp

from .const import API_PREFIX, DEFAULT_TIMEOUT, SCHEMA_VERSION, USER_AGENT_PRODUCT, VERSION
from .exceptions import (
    IMatrixAuthError,
    IMatrixConnectionError,
    IMatrixNotFoundError,
    IMatrixPermissionError,
    IMatrixRateLimitError,
    IMatrixResponseError,
    IMatrixUnsupportedSchemaError,
)

_LOGGER = logging.getLogger(__package__)


def _retry_after(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        return max(float(value), 0.0)
    except ValueError:
        return None


class AbstractAuth(ABC):
    """Sends authenticated requests. Subclasses supply the credential header."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        base_url: str,
        *,
        timeout: float = DEFAULT_TIMEOUT,
        user_agent: str | None = None,
    ) -> None:
        """Use an injected session. The caller owns and closes it."""
        self._session = session
        self._base_url = base_url.rstrip("/")
        self._timeout = aiohttp.ClientTimeout(total=timeout)
        self._ws_timeout = aiohttp.ClientWSTimeout(ws_close=10)
        agent = f"{USER_AGENT_PRODUCT}/{VERSION}"
        self._user_agent = f"{agent} {user_agent}" if user_agent else agent
        self.rate_limit_remaining: int | None = None

    @abstractmethod
    async def async_get_auth_headers(self) -> dict[str, str]:
        """Return the header that carries the credential."""

    async def request(self, path: str, params: dict[str, str] | None = None) -> dict[str, Any]:
        """GET an integration API path and return the parsed, version-checked body."""
        return await self.send("GET", path, params=params)

    async def send(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str] | None = None,
        json: Any = None,
    ) -> dict[str, Any]:
        """Call an integration API path with any method and return the version-checked body.

        Writes exist only for the opt-in floor plan device sharing (spec 822 FR-28).
        """
        url = f"{self._base_url}{API_PREFIX}{path}"
        headers = {
            "Accept": "application/json",
            "User-Agent": self._user_agent,
            **await self.async_get_auth_headers(),
        }
        try:
            async with self._session.request(
                method, url, params=params, json=json, headers=headers, timeout=self._timeout
            ) as resp:
                self._record_rate_limit(resp)
                _LOGGER.debug("%s %s -> %s", method, path, resp.status)
                await self._raise_for_status(resp)
                try:
                    body = await resp.json(content_type=None)
                except ValueError as err:
                    raise IMatrixResponseError(f"invalid JSON from {path}") from err
        except TimeoutError as err:
            raise IMatrixConnectionError(f"timeout calling {path}") from err
        except aiohttp.ClientError as err:
            raise IMatrixConnectionError(f"error calling {path}: {err}") from err
        if not isinstance(body, dict):
            raise IMatrixResponseError(f"expected a JSON object from {path}")
        if body.get("schemaVersion") != SCHEMA_VERSION:
            raise IMatrixUnsupportedSchemaError(
                f"{path} returned schemaVersion {body.get('schemaVersion')!r}, "
                f"expected {SCHEMA_VERSION}"
            )
        return body

    async def ws_connect(self, path: str) -> aiohttp.ClientWebSocketResponse:
        """Open an authenticated WebSocket on an integration API path.

        Upgrade refusals map like REST: 401 to IMatrixAuthError, 403 to
        IMatrixPermissionError, 404 (stream disabled on the server) to
        IMatrixNotFoundError. aiohttp answers server pings (autoping).
        """
        url = f"{self._base_url}{API_PREFIX}{path}"
        headers = {"User-Agent": self._user_agent, **await self.async_get_auth_headers()}
        try:
            return await self._session.ws_connect(
                url, headers=headers, heartbeat=None, autoping=True, timeout=self._ws_timeout
            )
        except aiohttp.WSServerHandshakeError as err:
            if err.status == 401:
                raise IMatrixAuthError(f"HTTP {err.status}") from err
            if err.status == 403:
                raise IMatrixPermissionError(f"HTTP {err.status}") from err
            if err.status == 404:
                raise IMatrixNotFoundError(f"HTTP {err.status}") from err
            raise IMatrixConnectionError(f"stream upgrade failed: HTTP {err.status}") from err
        except (TimeoutError, aiohttp.ClientError) as err:
            raise IMatrixConnectionError(f"stream connect failed: {err}") from err

    def _record_rate_limit(self, resp: aiohttp.ClientResponse) -> None:
        remaining = resp.headers.get("RateLimit-Remaining")
        if remaining is not None and remaining.isdigit():
            self.rate_limit_remaining = int(remaining)

    @staticmethod
    async def _raise_for_status(resp: aiohttp.ClientResponse) -> None:
        status = resp.status
        if status < 400:
            return
        message = f"HTTP {status}"
        if status == 401:
            raise IMatrixAuthError(message)
        if status == 403:
            raise IMatrixPermissionError(message)
        if status == 404:
            raise IMatrixNotFoundError(message)
        if status == 429:
            raise IMatrixRateLimitError(message, _retry_after(resp.headers.get("Retry-After")))
        if status >= 500:
            raise IMatrixConnectionError(message)
        raise IMatrixResponseError(message)


class ApiKeyAuth(AbstractAuth):
    """Authenticates with an iMatrix API key (header X-API-Key)."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        base_url: str,
        api_key: str,
        **kwargs: Any,
    ) -> None:
        """Store the key. It is never logged."""
        super().__init__(session, base_url, **kwargs)
        self._api_key = api_key

    async def async_get_auth_headers(self) -> dict[str, str]:
        """Return the X-API-Key header."""
        return {"X-API-Key": self._api_key}


class TokenAuth(AbstractAuth):
    """Base for OAuth2: subclasses return a valid access token, sent as a Bearer header."""

    @abstractmethod
    async def async_get_access_token(self) -> str:
        """Return a valid access token, refreshing it first if needed."""

    async def async_get_auth_headers(self) -> dict[str, str]:
        """Return the Authorization header."""
        return {"Authorization": f"Bearer {await self.async_get_access_token()}"}
