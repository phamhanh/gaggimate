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
curl -X POST "http://gaggimate.plumvillage.org/api/settings" --data "standbyTimeout=0"
```

2. **Brew mode** on the machine (`tt` is **0** in standby); scripts also POST `startupMode=brew` and `req:change-mode` if already in standby.
3. **`kffEnabled=false`** — idle prep POST omits `kffEnabled` (checkbox off).
4. No pump flow during idle tune (`pidLive.frozen` must stay **0**).

Implementation: [`scripts/pid_tune/session.py`](../scripts/pid_tune/session.py) (`pid_tune_session` wraps the **entire** ladder, which may run many hours).

## API (read this first)

Field names and commands: **[pid-telemetry-api.md](pid-telemetry-api.md)**.

```bash
HOST=gaggimate.plumvillage.org
curl -sS "http://${HOST}/api/status" | python3 -m json.tool   # expect pidLive
python3 -c "from gaggimate_ws import print_status_once; print_status_once('$HOST')"
```

## Unattended five-scenario ladder

Two phases: **tune** each scenario S1→S5 until acceptance, then **one verification lap** S1→S5 (prep + single `tt=92` test, no gain changes). Every `set_pid` **always** writes NVS (`persist=true`) so `/api/status` matches what is being tested.

| ID | Name | Prep cool `tt` | Prep rule | Test |
|----|------|----------------|-----------|------|
| S1 | `near_88` | 88 °C | wait until `ct ≤ 89`; **record 300 s at cool tt** | Step `tt` to **92 °C**, record ramp |
| S2 | `near_85` | 85 °C | wait until `ct ≤ 86`; **record 300 s at cool tt** | Step to 92 °C |
| S3 | `near_60` | 60 °C (via 88→85 if needed) | wait until `ct ≤ 61`; **record 300 s at cool tt** | Step to 92 °C |
| S4 | `near_30` | 30 °C (via 88→85→60 if needed) | wait until `ct ≤ 31`; **record 300 s at cool tt** | Step to 92 °C |
| S5 | `cold_soak` | 0 °C | cool to 0; **10 min plateau** + **1 h soak** | Step to 92 °C |

**Prep rule:** if `ct` is already at or below the scenario’s cool target (+1 °C tolerance), skip cooling **and the hold** and go straight to the 92 °C ramp test. After active cooling, **record 300 s at the cool tt** (`prep_hold_s` in `config.yaml`; `0` disables), then step to 92 °C for the ramp test.

### Run

**Apply + verify:** every PID change is written with `persist=true` and checked against `/api/status` before continuing. See [pid-tune-handover.md](pid-tune-handover.md) if `kp`/`ki`/`kd` do not update.

```bash
HOST=gaggimate.plumvillage.org
python3 scripts/gaggimate_pid_tune.py --host "$HOST"
```

| Flag | Purpose |
|------|---------|
| `--resume` | Continue from `device-data/pid-tune/state.json` (gitignored) |
| `--dry-run` | Read `ct`/`tt`/PID and print planned ladder; no POST/WS changes |
| `--max-hours N` | Safety abort after N hours (state file allows resume) |

### Expectations

- First full ladder often takes **6–12+ hours** depending on starting `ct` and ambient loss—not minutes.
- After tuning accepts all five scenarios, verification runs **one full S1→S5 lap** before declaring success. If a scenario fails verification, that scenario is re-tuned once and verification repeats (max one retry lap).
- Machine must stay powered, filled with water, and reachable on the network.
- Abort if `ct` > **160 °C** or `pidLive.frozen` ≠ 0 during idle tune.

### Resume

Progress is written to `device-data/pid-tune/state.json` after each scenario and iteration. Fields include `phase` (`tuning` | `verification`), `completed`, and `verified`. On disconnect, re-run with `--resume` on the same `--host`.

### Config

Bands, timeouts, and acceptance limits: [`scripts/pid_tune/config.yaml`](../scripts/pid_tune/config.yaml) (JSON-compatible; loaded via `config.py`).

The runner reloads `config.yaml` from disk at the **start of each tune iteration** (immediately before the next PID triplet is applied for prep + ramp). You can edit timeouts, acceptance, and record windows during a long ladder without restarting the CLI; changes apply on the next iteration, not mid-prep or mid-ramp. The verification lap uses config loaded once when tuning finishes (not per verify scenario). Avoid changing scenario `id`s while `state.json` resume is active.

Default cool timeouts per waypoint: 85 / 60 / 30 °C → **3 h** each; 0 °C → 120 min. `verification_laps`: **1** (plus one retry lap after re-tune on verify failure).

### Debug (single scenario)

```bash
python3 scripts/gaggimate_pid_tune.py --host "$HOST" --only near_88
```

Abbreviated overnight test: cool to 85 only + S2 with `--max-hours 1` before a full ladder.

## Scripts

| Script / module | Role |
|-----------------|------|
| [`scripts/gaggimate_pid_tune.py`](../scripts/gaggimate_pid_tune.py) | CLI → `run_full_ladder` |
| [`scripts/pid_tune/runner.py`](../scripts/pid_tune/runner.py) | Ladder state machine, ramp tests, tune loop |
| [`scripts/pid_tune/thermal.py`](../scripts/pid_tune/thermal.py) | `wait_until_ct`, cooling waypoints, 1 h soak |
| [`scripts/pid_tune/scenarios.py`](../scripts/pid_tune/scenarios.py) | Five scenario definitions |
| [`scripts/pid_tune/state.py`](../scripts/pid_tune/state.py) | Resume file I/O |
| [`scripts/gaggimate_ws.py`](../scripts/gaggimate_ws.py) | WebSocket + `read_status_once` HTTP fallback |
| [`scripts/gaggimate_pid_stream.py`](../scripts/gaggimate_pid_stream.py) | Record CSV under `device-data/pid-tune/` |
| [`scripts/pid_tune/metrics.py`](../scripts/pid_tune/metrics.py) | overshoot, time_to_band, `wait_stable` |
| [`scripts/pid_tune/suggest_gains.py`](../scripts/pid_tune/suggest_gains.py) | deterministic next Kp,Ki,Kd |

## Stream only (manual observation)

```bash
python3 scripts/gaggimate_pid_stream.py --host "$HOST" --duration 120 --scenario near_88 --print
```

Columns: `t, ct, tt, p, i, d, out, frozen, kp, ki, kd`.

## Verification

1. **Unit tests** (no device):

```bash
cd scripts && python3 -m unittest pid_tune.test_ladder -v
```

2. **Dry-run** on device:

```bash
python3 scripts/gaggimate_pid_tune.py --host "$HOST" --dry-run
```

3. **Live**: run S1 one evening (`--only near_88`); full ladder overnight with `--resume` if interrupted.

## Safety

- Abort if `ct` > **160 °C**.
- **Every** trial and ramp step calls `req:set-pid` with **`persist=true`** (NVS + controller). Check `/api/status` `kp`/`ki`/`kd` after each apply to confirm.
- Attend the machine for the first abbreviated test; overnight runs need stable power and network.

## Acceptance (per scenario)

From `config.yaml`: overshoot ≤ **0.5 °C**, time-to-band ≤ **240 s**, `pidLive.frozen == 0`. S1/S2 use shorter record windows (120 s) for small steps.

## Plan 1 gap

If `evt:status` lacks `pidLive`, stop and deploy Plan 1 — do not invent fields.
