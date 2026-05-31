---
name: gaggimate-deploy
description: >-
  Default ship workflow: scripts/deploy.sh backs up from gaggimate.local (by default), publishes
  to GitHub, OTA-updates, and verifies versions. Use when user says deploy, release, OTA,
  update the machine, or push firmware without losing profiles or shot history.
---

# Gaggimate deploy (same as release)

**Deploy and release are the same workflow.** `./scripts/deploy.sh` is the only command agents should use to ship new firmware.

**Alias skill:** [`gaggimate-release`](gaggimate-release/SKILL.md) — same steps, different trigger words.

Profiles and shot history survive display OTA because device data is **baked into `display-filesystem.bin`** before release. **Backup runs by default** before every release unless the user passes `--no-backup`.

## Default command

```bash
git status                              # must be clean
./scripts/deploy.sh --dry-run           # preview backup + version + OTA
./scripts/deploy.sh -- --yes            # backup → release → OTA (~10–15 min)
```

**Do not pass `--no-backup` unless the user explicitly wants the faster path** (SPIFFS may ship stale `data/p` or seed profiles).

## Default steps (agents)

1. **Backup** — profiles + shots from `gaggimate.local` → `data/p/`, `data/h/`
2. **Release** — `release.sh` (internal) → SPIFFS budget → GitHub upload → push
3. **OTA** — flash built artifacts from `out/`; wait for device GitHub `latestVersion`; **verify** both components on released tag

## Required post-deploy check

- `http://gaggimate.local/ota`: `displayVersion` and `controllerVersion` must match the tag just shipped
- On failure: `./scripts/update-device.sh` or `./scripts/deploy.sh --update-only`

## Catch up without rebuilding

When GitHub already has the release but the machine is behind:

```bash
./scripts/update-device.sh --dry-run
./scripts/update-device.sh
```

**Not** the same as `deploy.sh --update-only` (that flashes bins from local `out/`).

## Tool location

| Script | Role |
|--------|------|
| `scripts/deploy.sh` | **Only public ship command** |
| `scripts/update-device.sh` | GitHub latest → OTA → verify |
| `scripts/backup-spiffs-data.sh` | Back up `/p/` and `/h/` → `data/p`, `data/h` |
| `scripts/gaggimate_ota.py` | Shared OTA poll / flash / verify |
| `scripts/gaggimate_ws.py` | WebSocket client |
| `out/release-manifest.json` | Built artifacts + version (after release) |

## CLI reference

```bash
./scripts/deploy.sh [OPTIONS] [-- RELEASE_ARGS...]

--host HOST              Default: gaggimate.local (or $GAGGIMATE_HOST)
--release-only             Back up + release; skip OTA (default backup unless --no-backup)
--update-only              OTA from existing out/ + verify
--no-backup                Skip device backup (user-requested faster path)
--ota-respect-device       Only OTA if *UpdateAvailable (avoid after fresh release)
--dry-run                  Show plan only
--timeout SEC              OTA wait (default 600)

Pass-through to release.sh after --:
  ./scripts/deploy.sh -- --patch --yes
```

## Edge cases

| When | Command |
|------|---------|
| Ship new code | `./scripts/deploy.sh -- --yes` |
| Machine behind GitHub | `./scripts/update-device.sh` |
| Faster ship, SPIFFS OK | `./scripts/deploy.sh --no-backup -- --yes` |
| Publish only | `./scripts/deploy.sh --release-only -- --yes` |
| Finish partial OTA from `out/` | `./scripts/deploy.sh --update-only` |
| Device offline | `./scripts/release.sh --offline --yes` |

## SPIFFS layout

| Local | On device | Content |
|-------|-----------|---------|
| `data/w/` | `/w/` | Web UI |
| `data/p/` (gitignored) | `/p/` | Profiles from backup |
| `profiles/seed/` | — | Fallback if `data/p/` empty |
| `data/h/` | `/h/` | Shot history |

## Preserved vs not

| Data | In SPIFFS backup? | Survives display OTA? |
|------|-------------------|------------------------|
| Profile files | Yes | Yes |
| Shot history | Yes | Yes |
| NVS (WiFi, PID, selection) | NVS | Yes |

## Agent workflow

1. `./scripts/deploy.sh --dry-run` — backup step present, device reachable, version bump.
2. Clean working tree; ask user to commit if dirty.
3. `./scripts/deploy.sh -- --yes`.
4. Verify `/ota` versions and profiles/history on the web UI.

## Related skills

| Skill | Role |
|-------|------|
| `gaggimate-release` | Same workflow (alias triggers) |
| `gaggimate-profiles` | Manual profile repair |
