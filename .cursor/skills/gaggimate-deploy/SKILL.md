---
name: gaggimate-deploy
description: >-
  Default release workflow: scripts/deploy.sh backs up from gaggimate.local, publishes
  to GitHub, and OTA-updates the machine. Use when user says deploy, release, release
  and update, or push firmware without losing profiles or shot history.
---

# Gaggimate deploy

**This is the default workshop.** `./scripts/deploy.sh` does all three steps:

1. **Backup** — profiles and shots from `gaggimate.local`
2. **Release** — build firmware, bake SPIFFS, publish to GitHub
3. **OTA** — update the machine from that release

Profiles and shot history survive because device data is **baked into `display-filesystem.bin`** before OTA. No post-OTA WebSocket restore is required.

Semver, build order, and offline exceptions: [`gaggimate-release`](gaggimate-release/SKILL.md).

## Tool location

| Script | Role |
|--------|------|
| `scripts/deploy.sh` | Full deploy orchestrator |
| `scripts/backup-spiffs-data.sh` | Back up `/p/` and `/h/` from device → `data/p`, `data/h` |
| `scripts/gaggimate_ws.py` | Shared WebSocket client (profiles, history, OTA) |
| `scripts/spiffs_budget.py` | Abort build if `data/` exceeds SPIFFS partition |
| `out/release-manifest.json` | Built artifacts + SPIFFS counts (after `build-firmware.sh`) |

## Typical workflow

```bash
git status                    # must be clean — deploy stops immediately if dirty
git commit -am "..."          # commit firmware changes before deploy
./scripts/deploy.sh --dry-run # preview (no clean-tree check in dry-run)
./scripts/deploy.sh           # backup from device → release → OTA (~10–15 min)
```

## CLI reference

```bash
./scripts/deploy.sh [OPTIONS] [-- RELEASE_ARGS...]

--host HOST           Default: gaggimate.local (or $GAGGIMATE_HOST)
--release-only        Back up + release; skip OTA
--update-only         OTA only (uses existing out/)
--no-backup           Skip device backup (use existing data/p or profiles/seed fallback)
--ota-flash-built     Attempt OTA for all components in out/ (ignore device *UpdateAvailable)
--dry-run             Show plan only
--timeout SEC         OTA wait (default 600)

Pass-through to release.sh after --:
  ./scripts/deploy.sh -- --patch --display-only
```

**Backup only:**

```bash
./scripts/backup-spiffs-data.sh --dry-run
./scripts/backup-spiffs-data.sh --host 192.168.1.50
./scripts/backup-spiffs-data.sh --no-snapshot
```

## Flow

1. **Backup** — WebSocket profiles; HTTP `index.bin`, `*.slog`, notes `*.json` when present
2. **Snapshot** — `device-data/snapshots/{deviceVersion}-{timestamp}/` (gitignored; optional to commit)
3. **Release** — `release.sh` → `build_spiffs.sh` → `spiffs_budget` check → `buildfs` → GitHub upload
4. **OTA** — Only if manifest built an artifact **and** device reports `*UpdateAvailable`; `req:ota-start` with `cp: display` / `controller`

## SPIFFS layout

| Local | On device | Content |
|-------|-----------|---------|
| `data/w/` | `/w/` | Web UI (from `build_spiffs.sh`) |
| `data/p/` (gitignored) | `/p/` | Profile JSON from device backup |
| `profiles/seed/` | — | Factory defaults copied if `data/p/` empty |
| `data/h/` | `/h/` | Shot `.slog`, notes `.json`, `index.bin` |

Partition budget: **3,538,944 bytes** (`0x360000` on `default_16MB.csv`), with 128 KB reserved for metadata (`spiffs_budget.py`).

## Preserved vs not

| Data | In SPIFFS backup? | Survives display OTA? |
|------|-------------------|------------------------|
| Profile files | Yes | Yes — in FS image |
| Shot history | Yes | Yes — in FS image |
| Selected profile, favorites, order | NVS | Yes — NVS not wiped |
| WiFi, PID, etc. | NVS | Yes |

## Agent workflow

1. Run `./scripts/deploy.sh --dry-run` to confirm device reachability, backup counts, and next version.
2. Ensure working tree is clean; stop and ask user to commit/stash if not.
3. Run `./scripts/deploy.sh -- --yes` (backup + publish + OTA). Add `--patch` / `--minor` / `--major` after `--` if requested.
4. Use `--release-only` only when user explicitly does not want OTA.
5. After deploy, verify profiles and shot history on `http://gaggimate.local`.

## OTA warnings

Display OTA **replaces the entire SPIFFS image**. With deploy, that image **includes** your backed-up `/p/` and `/h/` data. Without deploy, OTA still wipes user data on the device.

`platformio run -e display -t upload` does **not** wipe SPIFFS; `uploadfs` does.

## Related skills

| Skill | Role |
|-------|------|
| `gaggimate-release` | Release semantics; bare `release.sh` only without device |
| `gaggimate-profiles` | Manual profile repair |
| **gaggimate-deploy** | Backup → bake → release → OTA |
