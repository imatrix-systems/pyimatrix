# pyimatrix

Async Python client for the iMatrix Cloud integration API (`/api/v1/integration/`). Used by the
[iMatrix Home Assistant integration](https://github.com/imatrix-systems/ha-imatrix).

Status: pre-release scaffold. The API contract is defined in iMatrix spec 817.

## Development

```bash
uv sync
uv run pytest
uv run ruff check && uv run mypy src
```
