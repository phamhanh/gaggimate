#!/usr/bin/env bash
# Back up device data (default) → release → OTA + verify
#
# Usage:
#   ./scripts/deploy.sh [OPTIONS] [-- RELEASE_ARGS...]
#
# Examples:
#   ./scripts/deploy.sh                              # backup + release + OTA (default)
#   ./scripts/deploy.sh --dry-run
#   ./scripts/deploy.sh -- --patch --yes
#   ./scripts/deploy.sh --no-backup                  # faster: skip device pull
#   ./scripts/deploy.sh --release-only               # backup + release, skip OTA
#   ./scripts/deploy.sh --update-only                # OTA from existing out/ only
#   ./scripts/deploy.sh --update-only --display-only
#   ./scripts/deploy.sh --controller-only -- --yes
#
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
exec python3 "${ROOT}/scripts/gaggimate_deploy.py" "$@"
