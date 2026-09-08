#!/usr/bin/env bash
# Start the FastAPI backend in development mode.
# The first-party gateway data plane is served in-process under /v1.
set -euo pipefail
cd "$(dirname "$0")/../backend"
VENV="${VENV:-../.venv}"
exec "$VENV/bin/uvicorn" app.main:app --host 0.0.0.0 --port 8400 "$@"
