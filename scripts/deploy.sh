#!/usr/bin/env bash
# Back up device data → release → intelligent OTA
#
# Usage:
#   ./scripts/deploy.sh [OPTIONS] [-- RELEASE_ARGS...]
#
# Examples:
#   ./scripts/deploy.sh
#   ./scripts/deploy.sh --dry-run
#   ./scripts/deploy.sh --release-only -- --patch
#   ./scripts/deploy.sh --update-only
#   ./scripts/deploy.sh --no-backup -- --build-only
#
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
exec python3 "${ROOT}/scripts/gaggimate_deploy.py" "$@"
