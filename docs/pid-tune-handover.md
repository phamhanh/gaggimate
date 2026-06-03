# PID ladder handover — apply + verify gate

## Problem (2026-06)

The unattended ladder was tuning blind:

1. **`persist=false`** — trial gains never appeared in `/api/status` (`kp`/`ki`/`kd` read NVS only).
2. **Apply only at ramp start** — during cool/prep logs showed old gains while state file had new ones.
3. **No verification** — script assumed `req:set-pid` worked; no check against HTTP status.

## Required behavior (current code)

Every PID change must:

1. Send **`req:set-pid` with `persist=true`** (NVS + BLE).
2. **Poll `GET /api/status`** until `kp`/`ki`/`kd` match expected (10 s timeout).
3. **Abort with clear error** if status never updates.

Implementation: [`scripts/pid_tune/pid_apply.py`](../scripts/pid_tune/pid_apply.py) → `apply_and_verify_pid()`.

### When verify runs

| When | Label in log |
|------|----------------|
| Ladder start (after brew mode) | `ladder preflight` |
| Before each scenario prep | `{scenario} before prep` |
| Before each ramp record | `{scenario} ramp` |
| After each `suggest_gains` | `{scenario} after suggest` |
| Ladder success (verification done) | `ladder final` |

If verify fails, the ladder **stops immediately** — do not continue tuning on wrong gains.

## Manual preflight (same check as the script)

```bash
HOST=192.168.51.2   # must match --host on the ladder command

# 1. Current PID
curl -sS "http://${HOST}/api/status" | python3 -c "import sys,json; d=json.load(sys.stdin); print('kp',d['kp'],'ki',d['ki'],'kd',d['kd'])"

# 2. Run ladder — first lines must include:
#    ladder preflight: verified (Kp, Ki, Kd) on device
```

Use the **same host** for `--host` and curl (tunnel `gaggimate.plumvillage.org` vs LAN `192.168.51.2` are the same machine but verify the one you are actually tuning).

## Expected log pattern (good)

```
ladder preflight: set (28.78, 0.27, 88.2), verifying on /api/status …
ladder preflight: verified (28.78, 0.27, 88.2) on device
near_88: === iteration 1/12 gains=(28.78, 0.27, 88.2) ===
near_88 before prep: set (28.78, 0.27, 88.2), verifying …
near_88 before prep: verified (28.78, 0.27, 88.2) on device
near_88: device before prep — ct=… Kp=28.78 Ki=0.270 Kd=88 …
```

If `device before prep` still shows **Kp=34** after `verified`, the status poll and WS tick are out of sync — treat as firmware bug.

## Failure modes

| Symptom | Likely cause |
|---------|----------------|
| `PID apply failed: expected … status still shows 34…` | Old script still running; or firmware without working `req:set-pid` + persist; or wrong `--host` |
| Preflight passes, P/I/D still 0 | Plan 1 `pidLive` incomplete — tuning still works from `ct`/`tt`; live terms optional |
| Verify OK but machine heats like old PID | Controller BLE not connected; NVS updated but boiler still on old RAM gains — check display↔controller link |

## Firmware contract

- [`docs/pid-telemetry-api.md`](pid-telemetry-api.md) — `req:set-pid`, `persist`, status `kp`/`ki`/`kd` from `settings.getPid()`.
- [`WebUIPlugin.cpp`](../src/display/plugins/WebUIPlugin.cpp) — `persist=true` → `settings.setPid()` + `sendPidSettings()`.

## Resume after fix

```bash
python3 scripts/gaggimate_pid_tune.py --host "$HOST" --resume
```

State file `device-data/pid-tune/state.json` has trial gains; preflight will apply and verify them before continuing.

## Files touched for apply+verify

- `scripts/pid_tune/pid_apply.py` — verify helper
- `scripts/pid_tune/runner.py` — calls verify at each apply point
- `scripts/gaggimate_ws.py` — `set_pid` default `persist=True`
