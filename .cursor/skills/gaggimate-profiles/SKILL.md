---
name: gaggimate-profiles
description: >-
  List, inspect, or delete Gaggimate espresso profiles over the device WebSocket
  API when the web UI profiles page fails or profiles are corrupt. Use when the
  user mentions broken profiles, profile list not loading, delete-broken, or
  gaggimate.local profile recovery.
---

# Gaggimate profile maintenance

The firmware stores profiles as `/p/<id>.json` on SPIFFS. The web UI loads them via WebSocket (`req:profiles:list`). Corrupt entries (e.g. `type: "null"`, label `"null"`, or `phases: []` on non-utility profiles) can break the profiles page.

There is **no `/admin` route** and **no auth** on the LAN API. Use the project CLI instead of the broken UI.

## Tool location

- Python: `scripts/gaggimate_profiles.py` (stdlib only)
- Shell wrapper: `scripts/gaggimate-profiles.sh`

## Commands

```bash
# List all profiles (tab-separated)
./scripts/gaggimate-profiles.sh list

# Only profiles that look broken
./scripts/gaggimate-profiles.sh list --broken-only

# Show full JSON for one profile
./scripts/gaggimate-profiles.sh show AZbmJRldaT

# Delete specific ids
./scripts/gaggimate-profiles.sh delete JrHBMhpkl5 JuXn3QJ4Nf

# Auto-delete broken profiles (prompts unless -y)
./scripts/gaggimate-profiles.sh delete-broken --dry-run
./scripts/gaggimate-profiles.sh delete-broken --yes

# Different host
GAGGIMATE_HOST=192.168.1.50 ./scripts/gaggimate-profiles.sh list
./scripts/gaggimate-profiles.sh list --host 4.4.4.1
```

## Broken profile heuristics

Flagged when any of:

- `label` empty or `"null"`
- `type` empty or `"null"`
- `phases` count is 0 and `utility` is false

Utility profiles (e.g. backflush) may legitimately have many phases; empty extraction profiles are unsafe.

## Safety

- Refuses to delete the **currently selected** profile unless `--force`.
- `delete-broken` asks for confirmation unless `--yes` / `-y`.
- Always run `list --broken-only` or `delete-broken --dry-run` before deleting.

## Agent workflow

1. Run `list` or `list --broken-only` against `gaggimate.local` (or user-provided IP).
2. If the profiles page is broken, prefer `delete-broken --yes` after dry-run, or `delete <ids>` for known bad ids.
3. Verify with `list` and optionally open `http://gaggimate.local/profiles` in the browser.

## WebSocket API (reference)

| Message | Purpose |
|---------|---------|
| `req:profiles:list` | List all profiles |
| `req:profiles:load` + `id` | Load one |
| `req:profiles:delete` + `id` | Delete file |

Implemented in `src/display/plugins/WebUIPlugin.cpp` → `handleProfileRequest`.
