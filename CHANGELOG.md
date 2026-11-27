# Changelog

## 0.1.0 (2026-11-26)

- REST client for `/api/v1/integration` schemaVersion 1: `get_account`, `get_devices` (cursor
  pagination), `get_latest` (batches of 500 sns, `since` delta polling).
- B2: `get_faults` (faults and J1939 lamps, last 7 days), `get_history` (hourly mean, min, max,
  last; 1-20 sensors, at most 7 days), `get_trips` (at most 31 days).
- B3: `get_events` (cursor feed; `Event.fault` and `Event.lamps` helpers).
- B4: `IMatrixStream` live WebSocket client (reconnect with jittered backoff from 1 s to 5 min,
  resume from the last event id, 4401 raises `IMatrixAuthError`, 4429 backs off 60 s,
  HTTP 404 raises `IMatrixNotFoundError` when the server has the stream off).
- Floor plans (spec 822): `get_floor_plans`, `get_floor_plan_desired`, `put_ha_devices`,
  `delete_ha_devices`, `get_floor_plan_level_card` (level SVG and markers for dashboards),
  `post_floor_plan_ha_changes` (two-way sync).
- `ApiKeyAuth` (`X-API-Key`) and `TokenAuth` (Bearer, base for OAuth2).
- `DeviceKind.TRACKER` (backend #3410). Unknown kinds parse as `OTHER`.
- Typed models. Unknown enum values map to `UNKNOWN`. `Channel.state_count` is set in place of
  `states` for stateful channels with more than 64 states.
- Errors map HTTP status to `IMatrixAuthError`, `IMatrixPermissionError`, `IMatrixNotFoundError`,
  `IMatrixRateLimitError` (with `retry_after`), `IMatrixConnectionError`, `IMatrixResponseError`.
