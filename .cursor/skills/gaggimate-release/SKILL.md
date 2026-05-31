---
name: gaggimate-release
description: >-
  Ship Gaggimate firmware via scripts/deploy.sh (backup by default, publish, OTA, verify).
  Use when the user mentions releasing firmware, OTA updates, GitHub Releases, bumping version,
  deploy, or publishing bins from their Mac.
---

# Gaggimate release (same as deploy)

**Release and deploy are the same workflow.** One command ships committed firmware to the machine with profiles and shots preserved.

**Alias skill:** [`gaggimate-deploy`](gaggimate-deploy/SKILL.md) — same steps, different trigger words.

## Default command

```bash
git status                              # must be clean; device on LAN for backup
./scripts/deploy.sh --dry-run           # preview backup counts, version, OTA plan
./scripts/deploy.sh -- --yes            # backup → release → OTA (~10–15 min)
./scripts/deploy.sh -- --patch --yes    # semver bump if requested
```

**Do not pass `--no-backup` unless the user explicitly wants the faster path.**

**Never run `./scripts/release.sh` for a normal ship** — use `deploy.sh`. Exception: `--offline` when the device is unreachable and the user accepts stale SPIFFS.

`release.sh` is internal (build engine). `deploy.sh` sets `GAGGIMATE_FROM_DEPLOY=1` when calling it.

## Default steps (agents)

1. **Backup** — profiles + shots from `gaggimate.local` → `data/p/`, `data/h/`
2. **Release** — tag, build (SPIFFS includes backup), publish to GitHub, push refs
3. **OTA** — flash all components built in `out/`; poll until device sees new GitHub tag; **verify** display + controller match released tag

## Required post-deploy check

- Open or query `http://gaggimate.local/ota`: `displayVersion` and `controllerVersion` must equal the tag just shipped
- If mismatch, report failure; suggest `./scripts/update-device.sh` or `./scripts/deploy.sh --update-only`

**Manual `/ota` in the browser** — fallback only if scripted OTA fails.

## Edge cases

| When | Command |
|------|---------|
| Ship new code (build + publish + OTA) | `./scripts/deploy.sh -- --yes` |
| Machine behind GitHub latest (no build) | `./scripts/update-device.sh` |
| Faster ship, SPIFFS unchanged | `./scripts/deploy.sh --no-backup -- --yes` (user-requested only) |
| Publish only, no OTA | `./scripts/deploy.sh --release-only -- --yes` |
| OTA from local `out/` after partial OTA | `./scripts/deploy.sh --update-only` |
| Test compile, no publish | `./scripts/deploy.sh --release-only -- --build-only` |
| Device offline (user confirms stale SPIFFS) | `./scripts/release.sh --offline --yes` |
| Backup only | `./scripts/backup-spiffs-data.sh` |

## Tool location

| Script | Role |
|--------|------|
| `scripts/deploy.sh` | **Only public ship command** — backup (default) → release → OTA → verify |
| `scripts/update-device.sh` | Latest GitHub tag → OTA → verify (no build) |
| `scripts/backup-spiffs-data.sh` | Backup only |
| `scripts/release.sh` | Internal / `--offline` / `--build-only` / `--dry-run` only |
| `scripts/build-firmware.sh` | Firmware build (called by `release.sh`) |

## Pre-flight checklist

1. **Device on LAN** — `gaggimate.local` (or `--host` / `$GAGGIMATE_HOST`)
2. **Clean working tree** — dirty trees produce `vX.Y.Z-dirty` and break OTA semver checks
3. **Commits pushed** — deploy pushes `master` + tag at the end
4. **Origin** — `git@github.com:phamhanh/gaggimate.git`
5. **gh authenticated** — `gh auth login`

## CLI reference (`release.sh` pass-through after `--`)

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

## OTA asset names

- `display-firmware.bin`
- `display-filesystem.bin`
- `board-firmware.bin`

Release URL: `https://github.com/phamhanh/gaggimate/releases/`

## Agent workflow

1. Run `./scripts/deploy.sh --dry-run` — confirm device reachable, backup step, next version, OTA plan.
2. Ensure working tree is clean; stop and ask user to commit/stash if not.
3. Run `./scripts/deploy.sh -- --yes` (default includes backup).
4. Confirm `/ota` shows both components on the new tag; profiles and shot history intact.
