---
name: gaggimate-deploy
description: >-
  Ship Gaggimate firmware via scripts/deploy.sh (backup by default, publish, OTA, verify).
  Use when user says deploy, release, OTA, update the machine, GitHub Releases, bump version,
  or push firmware without losing profiles or shot history.
---

# Gaggimate deploy and release

**Deploy and release are the same workflow.** `./scripts/deploy.sh` is the only command agents should use to ship new firmware.

Profiles and shot history survive display OTA because device data is **baked into `display-filesystem.bin`** before release. **Backup runs by default** before every release unless the user passes `--no-backup`.

**Never run `./scripts/release.sh` for a normal ship** — use `deploy.sh`. Exception: `--offline` when the device is unreachable and the user accepts stale SPIFFS. `release.sh` is internal (build engine); `deploy.sh` sets `GAGGIMATE_FROM_DEPLOY=1` when calling it.

## Default command

```bash
git status                              # must be clean; device on LAN for backup
./scripts/deploy.sh --dry-run           # preview backup + version + OTA
./scripts/deploy.sh -- --yes            # backup → release → OTA (~10–15 min)
./scripts/deploy.sh -- --patch --yes    # semver bump if requested
```

**Do not pass `--no-backup` unless the user explicitly wants the faster path** (SPIFFS may ship stale `data/p` or seed profiles).

## Shell execution

Deploy scripts are long-running (~10–15 min). **Run them in the foreground with visible terminal output** — the user must see progress in the terminal panel.

- **Do not** start `./scripts/deploy.sh`, `./scripts/update-device.sh`, `./scripts/backup-spiffs-data.sh`, or related ship scripts in the background (`block_until_ms: 0`, background shell, or equivalent hidden execution).
- Use a long `block_until_ms` (e.g. `900000` for 15 minutes) or poll the same shell with `Await` until the script exits — keep one terminal session so output stays visible.
- Do not suppress or redirect stdout/stderr away from the user; surface key milestones (backup, build, publish, OTA, verify) from the live terminal output.

## Pre-flight checklist

1. **Device on LAN** — `gaggimate.local` (or `--host` / `$GAGGIMATE_HOST`)
2. **Clean working tree** — dirty trees produce `vX.Y.Z-dirty` and break OTA semver checks; apply **`gaggimate-commit`** skill and commit first if needed
3. **Commits pushed** — deploy pushes `master` + tag at the end
4. **Origin** — `git@github.com:phamhanh/gaggimate.git`
5. **gh authenticated** — `gh auth login`

## Agent workflow

1. `./scripts/deploy.sh --dry-run` — backup step present, device reachable, version bump.
2. Clean working tree; ask user to commit if dirty.
3. `./scripts/deploy.sh -- --yes`.
4. Verify `/ota` versions and profiles/history on the web UI.

Steps: **Backup** (profiles + shots → `data/p/`, `data/h/`) → **Release** (tag, build, GitHub upload, push) → **OTA** (flash `out/`, poll GitHub tag, verify display + controller).

## Required post-deploy check

- `http://gaggimate.local/ota`: `displayVersion` and `controllerVersion` must match the tag just shipped
- On failure: `./scripts/update-device.sh` or `./scripts/deploy.sh --update-only`
- Manual `/ota` in the browser — fallback only if scripted OTA fails

## Catch up without rebuilding

When GitHub already has the release but the machine is behind:

```bash
./scripts/update-device.sh --dry-run
./scripts/update-device.sh
./scripts/update-device.sh --controller-only
```

**Not** the same as `deploy.sh --update-only` (that flashes bins from local `out/`).

Partial OTA (one component only; verify matches what was flashed):

```bash
./scripts/deploy.sh --update-only --display-only
./scripts/deploy.sh --controller-only -- --yes
```

On `deploy.sh`, `--display-only` / `--controller-only` before `--` control **OTA only**. The same names after `--` go to `release.sh` and control **build** scope only.

## Tool location

| Script | Role |
|--------|------|
| `scripts/deploy.sh` | **Only public ship command** — backup (default) → release → OTA → verify |
| `scripts/update-device.sh` | GitHub latest → OTA → verify (no build) |
| `scripts/backup-spiffs-data.sh` | Back up `/p/` and `/h/` → `data/p`, `data/h` |
| `scripts/release.sh` | Internal / `--offline` / `--build-only` / `--dry-run` only |
| `scripts/build-firmware.sh` | Firmware build (called by `release.sh`) |
| `scripts/gaggimate_ota.py` | Shared OTA poll / flash / verify |
| `scripts/gaggimate_ws.py` | WebSocket client |
| `out/release-manifest.json` | Built artifacts + version (after release) |

## CLI reference

```bash
./scripts/deploy.sh [OPTIONS] [-- RELEASE_ARGS...]

--host HOST              Default: gaggimate.local (or $GAGGIMATE_HOST)
--release-only           Back up + release; skip OTA (default backup unless --no-backup)
--update-only            OTA from existing out/ + verify
--no-backup              Skip device backup (user-requested faster path)
--ota-respect-device     Only OTA if *UpdateAvailable (avoid after fresh release)
--display-only           OTA display only; skip controller (needs display bins in out/)
--controller-only        OTA controller only; skip display (needs board-firmware in out/)
--dry-run                Show plan only
--timeout SEC            OTA wait (default 600)

Pass-through to release.sh after --:
  ./scripts/deploy.sh -- --patch --yes
```

### `release.sh` pass-through flags (after `--`)

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

## Edge cases

| When | Command |
|------|---------|
| Ship new code | `./scripts/deploy.sh -- --yes` |
| Machine behind GitHub | `./scripts/update-device.sh` |
| Faster ship, SPIFFS OK | `./scripts/deploy.sh --no-backup -- --yes` |
| Publish only | `./scripts/deploy.sh --release-only -- --yes` |
| Test compile, no publish | `./scripts/deploy.sh --release-only -- --build-only` |
| Finish partial OTA from `out/` | `./scripts/deploy.sh --update-only` |
| Controller-only OTA from `out/` | `./scripts/deploy.sh --update-only --controller-only` |
| Display-only OTA from `out/` | `./scripts/deploy.sh --update-only --display-only` |
| Build all, OTA display only | `./scripts/deploy.sh --display-only -- --yes` |
| Controller-only catch-up (GitHub) | `./scripts/update-device.sh --controller-only` |
| Device offline | `./scripts/release.sh --offline --yes` |
| Backup only | `./scripts/backup-spiffs-data.sh` |

## OTA asset names

- `display-firmware.bin`
- `display-filesystem.bin`
- `board-firmware.bin`

Release URL: `https://github.com/phamhanh/gaggimate/releases/`

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

## Related skills

| Skill | Role |
|-------|------|
| `gaggimate-commit` | Update fork docs before commit; clean tree required for deploy |
| `gaggimate-profiles` | Manual profile repair |
