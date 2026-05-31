---
name: gaggimate-release
description: >-
  Build and publish Gaggimate firmware releases locally via scripts/release.sh.
  Use when the user mentions releasing firmware, OTA updates, GitHub Releases,
  bumping version, or publishing bins from their Mac.
---

# Gaggimate local release

Releases are built on the developer Mac and published to GitHub Releases — not via CI.

## Tool location

- Release orchestrator: `scripts/release.sh`
- Firmware build: `scripts/build-firmware.sh`
- Version embedding: `scripts/auto_firmware_version.py` (via PlatformIO pre-build; uses `git describe --tags --exclude nightly`)

## Pre-flight checklist

1. **Clean working tree** — required. Dirty trees produce `vX.Y.Z-dirty` in firmware and break OTA semver checks.
2. **Push commits first** — `./scripts/release.sh` pushes `master` + tag at the end.
3. **Verify origin** — must be `git@github.com:phamhanh/gaggimate.git`.
4. **gh authenticated** — `gh auth login` (one-time after `brew install gh`).

## Typical workflow

```bash
git status                      # must be clean
git push origin master
./scripts/release.sh --dry-run  # verify next version
./scripts/release.sh --yes      # build + publish (~5–10 min)
```

## CLI reference

| Flag | Effect |
|------|--------|
| `--patch` / `--minor` / `--major` | Semver bump (default: patch) |
| `--yes` | Skip confirmation |
| `--dry-run` | Print plan only |
| `--build-only` | Build to `out/`, no tag/publish/push |
| `--no-push` | Tag + build + publish, skip git push |
| `--display-only` | Skip controller + headless builds |
| `--force` | Replace existing tag/release |
| `vX.Y.Z` | Explicit version override |

## Build order (matches former CI)

1. `scripts/build_spiffs.sh` (web UI → SPIFFS data)
2. `platformio run -e controller`
3. `platformio run -e display`
4. `platformio run -e display-headless` (unless `--display-only`)
5. `platformio run -t buildfs -e display`
6. Copy artifacts to `out/` with OTA names

**Critical:** Tag is created **before** PlatformIO compile so `BUILD_GIT_VERSION` matches the release tag.

## OTA asset names

Device OTA expects (see `WebUIPlugin.cpp`):

- `display-firmware.bin`
- `display-filesystem.bin`
- `board-firmware.bin`

Release URL: `https://github.com/phamhanh/gaggimate/releases/` (`WebUIPlugin.h`).

OTA offers update when GitHub version **>** device `BUILD_GIT_VERSION`.

## Device update warnings

| Method | SPIFFS / profiles |
|--------|-------------------|
| `platformio run -e display -t upload` | Safe — switches OTA URL; does **not** wipe SPIFFS |
| OTA from web UI | **Wipes profiles + shot history** — backup first |
| `platformio run -e display -t uploadfs` | **Wipes SPIFFS** — backup first |

## Rollback on failure

If release fails after local tagging:

```bash
git tag -d vX.Y.Z
```

Remote tag is only pushed after a successful build + publish (unless `--no-push` stopped earlier).

## Agent workflow

1. Run `./scripts/release.sh --dry-run` to confirm version and steps.
2. Ensure working tree is clean; if not, stop and ask user to commit/stash.
3. For test builds without publishing: `./scripts/release.sh --build-only`.
4. For full release: `./scripts/release.sh --yes` (or with explicit flags user requested).
5. Verify release at `https://github.com/phamhanh/gaggimate/releases/latest`.
