---
name: gaggimate-fetch-latest-shot
description: >-
  Download today's espresso shots from a Gaggimate device into ./shots as JSON.
  Use when the user wants shot exports for analysis, Kff tuning, or agent
  workflows; add --latest for a single most recent shot only.
---

# Gaggimate — fetch shots

Pulls shot(s) from the machine over HTTP, parses binary `.slog` files, merges notes when present, and writes **`shots/shot-{id}.json`** at the repo root (same shape as the web UI export).

**Default:** all non-deleted shots from **today** (local calendar day).  
**`--latest`:** only the newest shot on the device.

No extra Python packages. No auth on the LAN API.

## Tool location

- Python: `scripts/gaggimate_fetch_latest_shot.py`
- Shell wrapper: `scripts/gaggimate-fetch-latest-shot.sh`
- Parser: `scripts/parse_binary_shot.py` (mirrors `web/src/pages/ShotHistory/parseBinaryShot.js`)

## Commands

```bash
# Today's shots → ./shots/shot-{id}.json (one file per shot)
./scripts/gaggimate-fetch-latest-shot.sh

# Only the newest shot
./scripts/gaggimate-fetch-latest-shot.sh --latest

# A specific day or timezone
./scripts/gaggimate-fetch-latest-shot.sh --date 2026-06-01
./scripts/gaggimate-fetch-latest-shot.sh --tz Europe/Amsterdam

# Custom host or output dir
GAGGIMATE_HOST=192.168.1.50 ./scripts/gaggimate-fetch-latest-shot.sh
./scripts/gaggimate-fetch-latest-shot.sh --host 192.168.1.50 --out ./shots
```

Stdout prints one path per written file (newest last within the day). Exit `1` if no matching shots; exit `2` if the device is unreachable.

## Agent workflow

1. Run `./scripts/gaggimate-fetch-latest-shot.sh` from the repo root (or `--host` with the user's IP).
2. Read the `shots/shot-*.json` paths printed on stdout.
3. For Kff / thermal analysis, pass file(s) to `scripts/analyze_kff_shot.py`.

## Shot selection

| Mode | Source | Rule |
|------|--------|------|
| Default (today) | `index.bin` | `timestamp` falls on today's local date |
| `--date YYYY-MM-DD` | `index.bin` | Same, for that calendar day |
| `--latest` | `index.bin` | Highest `timestamp` (any day) |
| No `index.bin` | WebSocket `req:history:list` | Same rules on listed entries (slower) |

Shot payload: `GET /api/history/{000000}.slog`. Notes (if any): `GET /api/history/{000000}.json`.

## Output layout

`shots/` is gitignored. Example:

```json
{
  "id": "42",
  "profile": "Turbo Bloom",
  "timestamp": 1717234567,
  "duration": 28000,
  "samples": [{ "t": 0, "tt": 93.0, "ct": 92.5, ... }],
  "volume": 36.2,
  "thermalSettings": { "inletTempC": 22, "combinedKff": 1.0, ... },
  "notes": { "rating": 4, "doseIn": "18", ... }
}
```
