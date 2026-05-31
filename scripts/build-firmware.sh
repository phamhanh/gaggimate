#!/usr/bin/env bash
# Build Gaggimate firmware artifacts into out/ for OTA and GitHub Releases.
#
# Usage:
#   ./scripts/build-firmware.sh [--version VERSION] [--display-only] [--skip-web]
#
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

VERSION=""
DISPLAY_ONLY=0
SKIP_WEB=0

usage() {
  cat <<'EOF'
Usage: ./scripts/build-firmware.sh [OPTIONS]

  --version VERSION   Write out/version.txt (default: git describe --tags --exclude nightly)
  --display-only      Skip controller and display-headless builds
  --skip-web          Skip scripts/build_spiffs.sh (web UI already in data/)
  -h, --help          Show this help
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --version)
      VERSION="${2:?--version requires a value}"
      shift 2
      ;;
    --display-only)
      DISPLAY_ONLY=1
      shift
      ;;
    --skip-web)
      SKIP_WEB=1
      shift
      ;;
    -h | --help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage >&2
      exit 1
      ;;
  esac
done

require_file() {
  local path="$1"
  if [[ ! -f "$path" ]]; then
    echo "Missing required build output: $path" >&2
    exit 1
  fi
}

copy_out() {
  local src="$1"
  local dest="$2"
  require_file "$src"
  cp "$src" "$dest"
  printf '  %s (%s bytes)\n' "$dest" "$(wc -c < "$dest" | tr -d ' ')"
}

mkdir -p out

if [[ -n "$VERSION" ]]; then
  echo "$VERSION" > out/version.txt
else
  git describe --tags --exclude nightly > out/version.txt
fi
printf '  out/version.txt (%s)\n' "$(cat out/version.txt)"

if [[ "$SKIP_WEB" -eq 0 ]]; then
  echo "Building web UI (SPIFFS data)..."
  ./scripts/build_spiffs.sh
else
  echo "Skipping web UI build (--skip-web)"
fi

if [[ "$DISPLAY_ONLY" -eq 0 ]]; then
  echo "Building controller..."
  platformio run -e controller
  copy_out .pio/build/controller/firmware.bin out/board-firmware.bin
  copy_out .pio/build/controller/partitions.bin out/board-partitions.bin
  copy_out .pio/build/controller/bootloader.bin out/board-bootloader.bin
fi

echo "Building display..."
platformio run -e display
copy_out .pio/build/display/firmware.bin out/display-firmware.bin
copy_out .pio/build/display/partitions.bin out/display-partitions.bin
copy_out .pio/build/display/bootloader.bin out/display-bootloader.bin

if [[ "$DISPLAY_ONLY" -eq 0 ]]; then
  echo "Building display-headless..."
  platformio run -e display-headless
  copy_out .pio/build/display-headless/firmware.bin out/display-headless-firmware.bin
  copy_out .pio/build/display-headless/partitions.bin out/display-headless-partitions.bin
  copy_out .pio/build/display-headless/bootloader.bin out/display-headless-bootloader.bin
fi

echo "Checking SPIFFS data size..."
python3 scripts/spiffs_budget.py check

echo "Building display filesystem..."
platformio run -t buildfs -e display
copy_out .pio/build/display/spiffs.bin out/display-filesystem.bin

if [[ "$DISPLAY_ONLY" -eq 0 ]]; then
  copy_out .pio/build/display/spiffs.bin out/display-headless-filesystem.bin
fi

echo ""
echo "Required OTA artifacts:"
require_file out/display-firmware.bin
require_file out/display-filesystem.bin
if [[ "$DISPLAY_ONLY" -eq 0 ]]; then
  require_file out/board-firmware.bin
fi

VERSION_FOR_MANIFEST="$(cat out/version.txt)"
python3 scripts/write_release_manifest.py --version "$VERSION_FOR_MANIFEST"

echo ""
echo "Firmware build complete — artifacts in out/"
