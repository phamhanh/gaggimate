# PID telemetry API (Plan 1 contract)

Live boiler PID terms and stored gains exposed on **`evt:status`** (WebSocket, ~2 Hz) and **`GET /api/status`** (HTTP). Plan 2 tuning agents must use these field names.

## Design choices

| ID | Choice | Decision |
|----|--------|----------|
| A | BLE transport | **A1** — extend 12-field CSV on `SENSOR_DATA_UUID` (backward compatible: ≥7 fields) |
| B | Frozen semantics | When `frozen=1`, report **actual contributing terms**: `p=0`, `i=latched I sum`, `d=0`, `kff=live disturbance FF` |
| C | WS payload | **C1** — merge `pidLive` into `evt:status` (same helper as HTTP) |
| D | Web UI | **D0** — `/pid-monitor` charts `ct`/`tt` and `pidLive`; scalars from `evt:status` |
| E | Set PID | **E1** — `req:set-pid` WebSocket with optional `persist` |

## Data path

Controller `SimplePID` (1 Hz) → `Heater` getters → BLE sensor notify (~4 Hz) → display `Controller` → `evt:status` / `/api/status` (2 Hz).

## Status fields

### Top-level (stored + power)

| Field | Type | Meaning |
|-------|------|---------|
| `ct` | float | Current boiler temp (°C) |
| `tt` | float | Target temp (°C) |
| `kp`, `ki`, `kd` | float | Stored NVS PID gains |
| `kffGain` | float | Stored disturbance FF gain (0 when Kff disabled in settings) |
| `kff` | float | **Alias** of `kffGain` (backward compatibility) |
| `pumpPower` | float | Pump duty 0–100 % |
| `heaterPower` | float | Heater command 0–1000 |
| `out` | float | **Alias** of `heaterPower` |
| `totalPower` | float | Combined 0–200 scale: `pumpPower + heaterPower/10` |

### `pidLive` object (from controller, ~4 Hz source, 2 Hz on LAN)

| Field | Type | Meaning |
|-------|------|---------|
| `p` | float | P term (controller units, same scale as `out`) |
| `i` | float | I term |
| `d` | float | D term |
| `kff` | float | **Live** disturbance feedforward output (not stored gain) |
| `out` | float | Total heater command 0–1000 |
| `frozen` | 0/1 | PID freeze latched (I-only + Kff during shot) |

**Naming rule:** top-level `kffGain` / `kff` = stored setting; `pidLive.kff` = live FF output.

## BLE sensor CSV (`SENSOR_DATA_UUID`)

12 comma-separated floats (3 decimal places), fields 1–7 unchanged:

```
temp, pressure, puckFlow, pumpFlow, puckResistance, pumpPower, heaterPower,
pidP, pidI, pidD, kffOut, frozen
```

Display firmware accepts ≥5 fields (legacy) or ≥7 (power telemetry) or 12 (full PID).

## WebSocket commands

### `req:set-pid`

Apply PID gains to the controller. Optionally persist to NVS.

```json
{
  "tp": "req:set-pid",
  "kp": 34.0,
  "ki": 0.27,
  "kd": 80.0,
  "kff": 1.0,
  "persist": true
}
```

- `ki`, `kd`, `kff` optional — omitted fields keep current stored values.
- `persist` defaults to `true`. When `false`, gains are sent over BLE only (not saved).
- Routed via `Settings::buildPidBlePayloadFromGains()` → `PID_CONTROL_CHAR_UUID`.

### `req:set-target-temp`

Set target temperature for the current mode (clamped to `MIN_TEMP`–`MAX_TEMP`).

```json
{
  "tp": "req:set-target-temp",
  "temp": 93.0
}
```

Calls `Controller::setTargetTemp()`; the next control loop pushes setpoint over BLE.

## Verification

```bash
HOST=gaggimate.plumvillage.org   # or 192.168.51.2
curl -sS "http://${HOST}/api/status" | python3 -m json.tool
# Expect pidLive + kp, ki, kd, kffGain, heaterPower/out
```

Brew mode, Kff disabled, no flow: `pidLive.frozen=0`, `pidLive.out` tracks heat demand.

## User NVS baseline (this machine)

Overshooting baseline: **`kp=34, ki=0.27, kd=80`**. Flash default `DEFAULT_PID` in constants is not authoritative.
