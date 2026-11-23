# Changelog

## 0.1.0 (unreleased)

- REST client for `/api/v1/integration` schemaVersion 1: `get_account`, `get_devices` (cursor
  pagination), `get_latest` (batches of 500 sns, `since` delta polling).
- `ApiKeyAuth` (`X-API-Key`) and `TokenAuth` (Bearer, base for OAuth2).
- Typed models. Unknown enum values map to `UNKNOWN`.
- Errors map HTTP status to `IMatrixAuthError`, `IMatrixPermissionError`, `IMatrixNotFoundError`,
  `IMatrixRateLimitError` (with `retry_after`), `IMatrixConnectionError`, `IMatrixResponseError`.
