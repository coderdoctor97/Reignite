#!/usr/bin/env bash
# Start the FastAPI backend in development mode with auto-reload.
set -euo pipefail
cd "$(dirname "$0")/../backend"
source .venv/bin/activate
exec uvicorn app.main:app --host 0.0.0.0 --port 8400 --reload
