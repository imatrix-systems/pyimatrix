"""Exceptions raised by pyimatrix."""

from __future__ import annotations


class IMatrixError(Exception):
    """Base class for all pyimatrix errors."""


class IMatrixAuthError(IMatrixError):
    """The credential is missing, invalid, expired or revoked (HTTP 401)."""


class IMatrixPermissionError(IMatrixError):
    """The credential is valid but lacks a permission (HTTP 403)."""


class IMatrixNotFoundError(IMatrixError):
    """The resource does not exist or is not visible to the caller (HTTP 404)."""


class IMatrixRateLimitError(IMatrixError):
    """The per-key request limit was exceeded (HTTP 429)."""

    def __init__(self, message: str, retry_after: float | None) -> None:
        """Store the server's Retry-After value in seconds, if it sent one."""
        super().__init__(message)
        self.retry_after = retry_after


class IMatrixConnectionError(IMatrixError):
    """The API could not be reached, timed out, or returned a 5xx status."""


class IMatrixResponseError(IMatrixError):
    """The API returned a body that could not be parsed."""


class IMatrixUnsupportedSchemaError(IMatrixResponseError):
    """The API returned a schemaVersion this library does not understand."""
