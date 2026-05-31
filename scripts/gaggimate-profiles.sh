#!/usr/bin/env bash
# Thin wrapper around scripts/gaggimate_profiles.py
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
exec python3 "${ROOT}/scripts/gaggimate_profiles.py" "$@"
