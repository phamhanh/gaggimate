# PID tuning agent (Plan 2)

Python workflow to tune **idle/preheat PID** using the Plan 1 telemetry API. Do not change firmware in Plan 2 unless the contract in [pid-telemetry-api.md](pid-telemetry-api.md) is missing.

**Prerequisite:** Plan 1 deployed on display + controller (`pidLive` on `evt:status` and `GET /api/status`).

## Hosts

- `gaggimate.plumvillage.org` (tunnel)
- `192.168.51.2` (LAN)

## User baseline (NVS on this machine)

**`Kp=34`, `Ki=0.27`, `Kd=80`** — currently **overshoots**. Flash `DEFAULT_PID` in `constants.h` is not authoritative.

## Goals

| Metric | Target |
|--------|--------|
| Band | ±0.5 °C (stretch ±0.2 °C) |
| Time to band | ~60 s ideal, 240 s max |
| Overshoot | minimize; prefer slow over overshoot |

**Out of scope:** Kff tuning ([thermal-kff-tuning.md](thermal-kff-tuning.md)), autotune UI, shot-download loop.

## Session setup

Every tuning run (scripts do this automatically; restore on exit):

1. **Disable auto-standby** — `POST /api/settings` with `standbyTimeout=0` so firmware will not call `activateStandby()` while idle (`standbyTimeout > 0` is required in `Controller.cpp`). On finish, restore the previous value from GET.

```bash
# Start (scripts / manual)
curl -X POST "http://gaggimate.plumvillage.org/api/settings" --data "standbyTimeout=0"

# End — use the seconds value from GET /api/settings (field standbyTimeout)
curl -X POST "http://gaggimate.plumvillage.org/api/settings" --data "standbyTimeout=30"
```

2. **Brew mode** on the machine (`tt` is **0** in standby); scripts also POST `startupMode=brew` and `req:change-mode` if already in standby.
3. **`kffEnabled=false`** — idle prep POST omits `kffEnabled` (checkbox off).
4. No pump flow during idle tune (`pidLive.frozen` must stay **0**).

Implementation: [`scripts/pid_tune/session.py`](../scripts/pid_tune/session.py) (`pid_tune_session` context manager).

## API (read this first)

Field names and commands: **[pid-telemetry-api.md](pid-telemetry-api.md)**.

```bash
HOST=gaggimate.plumvillage.org
curl -sS "http://${HOST}/api/status" | python3 -m json.tool   # expect pidLive
python3 scripts/gaggimate_ws.py  # use print_status_once from another module:
python3 -c "from gaggimate_ws import print_status_once; print_status_once('$HOST')"
```

## Scripts

| Script | Role |
|--------|------|
| [`scripts/gaggimate_ws.py`](../scripts/gaggimate_ws.py) | `stream_status`, `set_pid`, `set_target_temp`, HTTP settings/status |
| [`scripts/gaggimate_pid_stream.py`](../scripts/gaggimate_pid_stream.py) | Record CSV under `device-data/pid-tune/` |
| [`scripts/pid_tune/metrics.py`](../scripts/pid_tune/metrics.py) | overshoot, time_to_band, settle |
| [`scripts/pid_tune/suggest_gains.py`](../scripts/pid_tune/suggest_gains.py) | deterministic next Kp,Ki,Kd |
| [`scripts/gaggimate_pid_tune.py`](../scripts/gaggimate_pid_tune.py) | scenario runner + suggestion loop |

## Workflows

### A — Stream only

```bash
HOST=gaggimate.plumvillage.org
python3 scripts/gaggimate_pid_stream.py --host "$HOST" --duration 120 --scenario cold_start --print
```

Columns: `t, ct, tt, p, i, d, out, frozen, kp, ki, kd`.

### B — Single step test (`preheat_stable`)

Soak at 85 °C, step to 93 °C, score overshoot and time-to-band:

```bash
python3 scripts/gaggimate_pid_tune.py --host "$HOST" --scenario preheat_stable --iterations 1
```

### C — Iteration (deterministic, no LLM)

```bash
python3 scripts/gaggimate_pid_tune.py --host "$HOST" --scenario preheat_stable --iterations 10
# On acceptable run:
python3 scripts/gaggimate_pid_tune.py --host "$HOST" --scenario preheat_stable --iterations 1 --persist
```

Between `cold_start` runs the boiler may need manual cool-down.

## Scenarios

| Tag | Use |
|-----|-----|
| `cold_start` | Low probe temp → ramp to target; score time-to-band and overshoot |
| `preheat_stable` | Soak + small step 85→93 °C |
| `post_shot_grace` | Stream after shot; watch `frozen` and post-grace `ct` vs `tt` (manual shot) |
| `post_standby` | Optional wake from standby (document `tt` glitch from 0) |

## Safety

- Abort if `ct` > **160 °C**.
- Attend the machine during automated steps.
- Use `persist=false` for trials; `persist=true` only when accepting gains.

## Acceptance

One `preheat_stable` run: in ±0.5 °C band within 240 s, overshoot ≤ 0.5 °C (tighten to 0.2 °C later if desired).

## Plan 1 gap

If `evt:status` lacks `pidLive`, stop and deploy Plan 1 — do not invent fields.
