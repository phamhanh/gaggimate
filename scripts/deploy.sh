#!/usr/bin/env bash
# Back up device data → release → intelligent OTA
#
# Usage:
#   ./scripts/deploy.sh [OPTIONS] [-- RELEASE_ARGS...]
#
# Examples:
#   ./scripts/deploy.sh                              # backup + release + OTA
#   ./scripts/deploy.sh --no-backup                  # release + OTA (keep data/p, data/h)
#   ./scripts/deploy.sh --no-backup --release-only   # release only
#   ./scripts/deploy.sh --dry-run
#   ./scripts/deploy.sh --update-only
#   ./scripts/deploy.sh --no-backup -- --build-only
#
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
exec python3 "${ROOT}/scripts/gaggimate_deploy.py" "$@"
