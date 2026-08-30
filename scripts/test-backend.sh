#!/usr/bin/env bash
# Run backend tests.
set -euo pipefail
cd "$(dirname "$0")/../backend"
source .venv/bin/activate
exec python -m pytest tests/ -v "$@"
