#!/usr/bin/env bash
# Download today's shots from the device into ./shots as JSON (--latest for one).
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
exec python3 "${ROOT}/scripts/gaggimate_fetch_latest_shot.py" "$@"
