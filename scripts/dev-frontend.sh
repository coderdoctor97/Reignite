#!/usr/bin/env bash
# Start the Vite frontend dev server.
set -euo pipefail
cd "$(dirname "$0")/../frontend"
exec npm run dev -- --host 0.0.0.0
