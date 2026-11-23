# Changelog

## 0.1.0 (unreleased)

- REST client for `/api/v1/integration` schemaVersion 1: `get_account`, `get_devices` (cursor
  pagination), `get_latest` (batches of 500 sns, `since` delta polling).
- B2: `get_faults` (faults and J1939 lamps, last 7 days), `get_history` (hourly mean, min, max,
  last; 1-20 sensors, at most 7 days), `get_trips` (at most 31 days).
- `ApiKeyAuth` (`X-API-Key`) and `TokenAuth` (Bearer, base for OAuth2).
- Typed models. Unknown enum values map to `UNKNOWN`. `Channel.state_count` is set in place of
  `states` for stateful channels with more than 64 states.
- Errors map HTTP status to `IMatrixAuthError`, `IMatrixPermissionError`, `IMatrixNotFoundError`,
  `IMatrixRateLimitError` (with `retry_after`), `IMatrixConnectionError`, `IMatrixResponseError`.
