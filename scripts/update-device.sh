#!/usr/bin/env bash
# OTA display + controller to latest GitHub release (no backup, build, or publish)
#
# Usage:
#   ./scripts/update-device.sh [OPTIONS]
#
# Examples:
#   ./scripts/update-device.sh --dry-run
#   ./scripts/update-device.sh
#   ./scripts/update-device.sh --version v1.9.5
#   ./scripts/update-device.sh --host 192.168.51.2
#   ./scripts/update-device.sh --ota-display-only
#   ./scripts/update-device.sh --ota-controller-only
#
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
exec python3 "${ROOT}/scripts/gaggimate_update_device.py" "$@"
