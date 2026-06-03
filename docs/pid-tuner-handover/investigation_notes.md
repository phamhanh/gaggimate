# PID Tuner — Investigation Notes

## Starting hypothesis (from prior session analysis)

The user's complaint:
1. Integral windup: the tuner resets the integral via a Ki=0 trick before each test
2. This is unrealistic — real machines don't get an integral reset before heat-up
3. The tuner should be able to "go through the whole scenario and hit target" without the hack

My prior analysis concluded `_clear_integral_before_apply` was broken because `setControllerPIDGains` doesn't reset the integral. This turned out to be **wrong** — needed firmware verification.

---

## Firmware trace (the key discovery)

### Path: Python → WebSocket → Display firmware → BLE → Controller firmware → Heater

1. `pid_apply.py::apply_and_verify_pid` → `client.set_pid(kp, ki, kd)` [gaggimate_ws.py:266]
2. `gaggimate_ws.py::set_pid` sends `{"tp": "req:set-pid", "kp": ..., "persist": true}` [line 276]
3. `WebUIPlugin.cpp:398-421` handles `req:set-pid`: saves to NVS if persist=true, then calls `controller->getClientController()->sendPidSettings(...)` over BLE
4. `GaggiMateController.cpp:125-135` — BLE `registerPidSettingsCallback` → `heater->setTunings(Kp, Ki, Kd)`
5. **`Heater.cpp:68-74` — `setTunings` calls `simplePid->reset()` IF gains differ**
6. `SimplePID.cpp:126-131` — `reset()` calls `resetFeedbackController()` which zeros `feedback_integralState`

**Conclusion: The Ki=0 hack DOES work.** `setTunings` → `reset()` → integral is zeroed. My prior analysis was wrong on the mechanism. The code path is:
- Set Ki=0 → gains changed → setTunings fires → reset() → integral=0
- Set real Ki → gains changed again → setTunings fires → reset() again → integral=0

So integral is reset TWICE per apply_and_verify_pid call — once per gains change.

### The silent no-op edge case

`setTunings` guard: `if (getKp() != Kp || getKi() != Ki || getKd() != Kd)`. If Ki is **already 0** when `_clear_integral_before_apply` runs, the Ki=0 set is a no-op — no reset. Only the second apply (real Ki) resets the integral. The integral thus carries its pre-apply state until the second BLE message arrives (a few hundred ms). During a cold-start iteration where ki has drifted to near-zero, this matters.

---

## Anti-windup firmware verification

`SimplePID.cpp:70-82`: Clamping anti-windup. When output saturates AND error/output same sign → integral step is undone. This correctly prevents accumulation:
- During cooling waypoints (error = -60°C, output = 0 = min) → anti-windup blocks integration
- During ramp to 92°C (error = +60°C, output = 1000 = max) → anti-windup blocks integration
- Only when ct is near tt (output not saturated) does integral accumulate freely

So the cooling prep phase does NOT corrupt the integral — anti-windup handles it.

---

## What actually breaks scenario convergence

After ruling out the mechanism being broken, the causes of "not going through the whole scenario" are:

### 1. suggest_gains double-fires on large overshoot (RC2)
Both `> 1.0` and `> 0.5` branches are plain `if` — they compound. Net: Kp × 0.675, Kd × 1.725 on severe overshoot. This is a lurch that overreacts, pushing into undershoot territory, then the next iteration's Ki adjustments pull the opposite direction. Gain oscillation rather than convergence.

### 2. acceptance_ok never checks in_band_fraction (RC3)
The final `or` clause is always True. Runs where ct just touches the band for one sample before bouncing away are accepted. This lets a badly-tuned set pass, causing verification to fail.

### 3. near_88 over-cools (RC4)
`waypoints_for_scenario` returns `[85]` for near_88. `cool_to_waypoints` waits until ct≤85. But `prep_done` for near_88 checks ct≤88+tol. So prep reports done at 89, but cooling has already gone to 85. The machine is 3°C colder than the test intends. A run tuned for 88→92 is actually 85→92 — 7% more ramp, skewing overshoot and time-to-band vs. subsequent near_85 tests.

### 4. Frozen hard-abort (RC6)
Any single tick with `frozen=1` (transient BLE dropout during brew-mode idle) aborts the entire ladder. No retries, no tolerance. A 10-hour overnight run dies on a single tick.

### 5. cold_soak_duration_s disconnected (RC5)
Operator configures a 3600s soak in config.yaml. Actual soak uses `scenario.pre_soak_duration_s = 1500.0` (hardcoded). The machine never soaks long enough on machines that need longer cold equilibration.

---

## Hypotheses raised and dropped

**"The Ki=0 trick does nothing"** — DROPPED. Firmware trace confirms it does reset via setTunings→reset().

**"Anti-windup doesn't protect during cooling"** — DROPPED. Saturation anti-windup is in place and correct for the cooling waypoint phase.

**"The accept/reject loop oscillates gains indefinitely"** — PARTIALLY CONFIRMED. The overlapping branches in suggest_gains create large lurches that cause gain oscillation rather than monotone convergence. This is the primary reason "it doesn't go through the whole scenario."

---

## Files examined (12 / 30)

See file_map.txt.
