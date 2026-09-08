#!/usr/bin/env bash
# Start the local demo provider (mock OpenAI API + dashboard) on port 5900.
# Used for end-to-end testing without real credentials.
set -euo pipefail
cd "$(dirname "$0")/../backend"
VENV="${VENV:-../.venv}"
exec "$VENV/bin/python" -m app.demo.demo_provider
