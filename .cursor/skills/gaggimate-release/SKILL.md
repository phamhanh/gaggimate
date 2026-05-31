---
name: gaggimate-release
description: >-
  Release Gaggimate firmware via scripts/deploy.sh (backup, publish, OTA). Use when
  the user mentions releasing firmware, OTA updates, GitHub Releases, bumping version,
  deploy, or publishing bins from their Mac.
---

# Gaggimate local release

Releases are built on the developer Mac and published to GitHub Releases — not via CI.

## Default workflow

**Run `./scripts/deploy.sh`.** It does everything:

1. **Backup** — profiles and shot history from `gaggimate.local` → `data/p/`, `data/h/`
2. **Release** — build firmware, bake SPIFFS, publish to GitHub
3. **OTA** — update the machine from that release

```bash
git status                    # must be clean
git push origin master
./scripts/deploy.sh --dry-run # preview backup counts + next version
./scripts/deploy.sh           # backup → release → OTA (~10–15 min)
```

Pass release flags after `--`:

```bash
./scripts/deploy.sh -- --patch --yes
./scripts/deploy.sh -- --minor --yes
```

**Do not** run bare `./scripts/release.sh` for normal releases. It never contacts the device and silently reuses stale gitignored `data/p/` / `data/h/` if present.

Full deploy details: [`gaggimate-deploy`](gaggimate-deploy/SKILL.md).

## Exceptions

| When | Command |
|------|---------|
| Publish to GitHub only, no OTA | `./scripts/deploy.sh --release-only -- --yes` |
| Machine offline; accept stale/seed SPIFFS | `./scripts/release.sh --yes` (user must confirm) |
| Test build, no publish | `./scripts/deploy.sh --no-backup --release-only -- --build-only` |
| OTA from existing `out/` | `./scripts/deploy.sh --update-only` |

If `data/p/` is empty and backup is skipped, `build_spiffs.sh` falls back to `profiles/seed/` — factory defaults, not live device data.

## Tool location

| Script | Role |
|--------|------|
| `scripts/deploy.sh` | **Default** — backup → release → OTA |
| `scripts/backup-spiffs-data.sh` | Backup only |
| `scripts/release.sh` | Build + publish only (internal / offline edge case) |
| `scripts/build-firmware.sh` | Firmware build (called by `release.sh`) |
| `scripts/auto_firmware_version.py` | Version embedding (`git describe --tags --exclude nightly`) |

## Pre-flight checklist

1. **Device on LAN** — `gaggimate.local` (or `--host` / `$GAGGIMATE_HOST`)
2. **Clean working tree** — dirty trees produce `vX.Y.Z-dirty` and break OTA semver checks
3. **Commits pushed** — deploy pushes `master` + tag at the end
4. **Origin** — `git@github.com:phamhanh/gaggimate.git`
5. **gh authenticated** — `gh auth login`

## CLI reference (`release.sh` pass-through)

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

Deploy flags: `--release-only`, `--update-only`, `--no-backup`, `--host`, `--dry-run` — see gaggimate-deploy skill.

## Build order

1. Device backup → `data/p/`, `data/h/`
2. `build_spiffs.sh` (web UI + backed-up data)
3. `spiffs_budget.py`
4. PlatformIO: controller → display → display-headless → buildfs
5. Copy OTA artifacts to `out/`
6. GitHub release upload
7. OTA to device

Tag is created **before** compile so `BUILD_GIT_VERSION` matches the release tag.

## OTA asset names

- `display-firmware.bin`
- `display-filesystem.bin`
- `board-firmware.bin`

Release URL: `https://github.com/phamhanh/gaggimate/releases/`

## Rollback on failure

```bash
git tag -d vX.Y.Z
```

Remote tag is pushed only after successful build + publish.

## Agent workflow

1. Run `./scripts/deploy.sh --dry-run` — confirm device reachable, backup counts, next version.
2. Ensure working tree is clean; stop and ask user to commit/stash if not.
3. Run `./scripts/deploy.sh -- --yes` (default: backup + publish + OTA).
4. Use `--release-only` only if user explicitly does not want the machine updated.
5. Never run bare `release.sh` unless machine is offline and user confirms stale SPIFFS is OK.
6. Verify GitHub release and device on `http://gaggimate.local` (profiles + shot history intact).
