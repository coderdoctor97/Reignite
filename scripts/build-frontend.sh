#!/usr/bin/env bash
# Build the frontend for production.
set -euo pipefail
cd "$(dirname "$0")/../frontend"
exec npm run build
