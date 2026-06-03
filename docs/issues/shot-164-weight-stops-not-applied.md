# Shot 164 — weight stops not applied as expected

**Status:** Investigated (no firmware fix committed)  
**Date:** 2026-06-03  
**Firmware:** Controller v1.9.14, Display v1.9.15  
**Shot:** `shots/shot-164.json` (id 164)  
**Profile:** Deep Bloom Rocker 8bar (`aAFOFNf3z1`)

---

## Summary

During shot 164, brew-by-weight was active and the Bluetooth scale was connected, but phase transitions did not follow the weight stops the operator expected:

1. **Compression** — did not end on first drip (~0.1–0.2 g); ran full **35 s** duration.
2. **Ramp to Brew** — no stop at **6 g** on the scale (max **5.7 g** in phase; **6.1 g** only after Extraction started).
3. **Other targets** — Extraction / Slow Finish timing vs `weight` / `predicted_weight` also worth monitoring (see below).

This is **not** caused by upstream-original firmware dropping `"type": "weight"` — **v1.9.15 display firmware parses `weight` and `predicted_weight`.**

---

## Evidence (shot log)

| Phase | Expected (operator) | Profile on device (2026-06-03) | Shot behavior |
|-------|---------------------|--------------------------------|---------------|
| Compression | Stop ~**0.1 g** first drip | `weight` gte **0.2** | First log weight **0.2 g** at **33.25 s**; phase ends **35.0 s** (duration) |
| Ramp to Brew | Stop **6 g** (per description) | **No `targets`** on phase | Max **5.7 g** in ramp; **6.1 g** at **49.5 s** in Extraction |
| Extraction | — | `weight` gte **20** | Ends ~**19.9 g** logged, **12.75 s** (not 60 s duration) |
| Slow Finish | — | `predicted_weight` gte **28** | Max **27.5 g** in phase; **28.1 g** in Grace |

**System flags during Compression when log shows v = 0.2 g:**

- `shotStartedVolumetric`: true  
- `currentlyVolumetric`: true  
- `bluetoothScaleConnected`: true  
- `volumetricAvailable`: true  

So: volumetric mode and a weight target on the current phase were active; the phase still did not end on the logged 0.2 g reading.

---

## What the code does (v1.9.15)

### Stop comparison is `>=`, not equality

```cpp
// src/display/models/profile.h — Target::isReached
if (operator_ == TargetOperator::GTE) {
    return input >= value;
}
```

**0.2 g measured with 0.1 g target should stop.** There is no “only stop when measured == target” logic.

### Weight target types are loaded on this build

```cpp
// src/display/models/profile.h — parseProfile
if (type == "weight") {
    target.type = TargetType::TARGET_TYPE_WEIGHT;
} else if (type == "predicted_weight" || type == "volumetric") {
    target.type = TargetType::TARGET_TYPE_PREDICTED_WEIGHT;
}
```

Unknown types are skipped (`continue`). **`weight` is known on fork / v1.9.15** — not dropped like on upstream-original (`volumetric` only).

### Phase end uses raw scale volume; shot log rounds

| Path | Value |
|------|--------|
| `BrewProcess::isCurrentPhaseFinished` | `currentVolume` — **raw** float from scale via `updateVolume()` |
| Shot log field `v` | `encodeUnsigned(weight, WEIGHT_SCALE=10)` — **0.1 g steps**, round half-up |

So the analyzer can show **0.2 g** while firmware still compares **raw** e.g. **0.15–0.19 g**:

| Raw scale (firmware) | Shot log `v` | `>= 0.2` target | `>= 0.1` target |
|----------------------|--------------|-----------------|-----------------|
| 0.15–0.19 | 0.2 | false | true |
| ≥ 0.20 | 0.2 | true | true |
| 0.35–0.39 | 0.4 | true | true |

Shot 164 Compression: **0.2 g** in log for ~1.75 s, then **0.4 g**, then Hold at **35 s** — **consistent with a 0.2 g target and raw readings below 0.2 until ~0.35 g.**

If the operator’s target was truly **0.1 g**, raw **≥ 0.15 g** (logged as 0.2 g) should still stop; that did not happen → treat as **open** unless the on-screen threshold was **0.2 g** (see verification).

### Ramp 6 g — configuration gap, not scale

Profile **description** says “Ramp to 8 bar until 6 gram”, but **Ramp to Brew** phase JSON has **no `targets` array** — only `duration: 12` and pressure transition. Firmware never evaluates 6 g for that phase. Description text is not read by `isFinished`.

### `pro` profile type

`type: pro` allows **duration fallback** when weight is not reached. When weight **is** reached (`isReached` true), the phase should still end immediately — `pro` does not block that.

---

## Likely root causes (ranked)

1. **Compression — log vs stop mismatch (0.2 g target)**  
   Saved threshold **0.2 g** + raw scale **0.15–0.19 g** logged as **0.2 g** → stop not triggered until raw crosses **0.2** (log may show **0.4 g**).

2. **Compression — operator set 0.1 g but UI/file had 0.2 g**  
   Re-read profile JSON and brew screen `X / Y g` during Compression.

3. **Compression — if 0.1 g confirmed on UI and scale clearly ≥ 0.1 g**  
   Possible bug: `currentVolume` not updated in `BrewProcess` while shot log still records Bluetooth weight (`onVolumetricMeasurement` source filter). Needs trace if reproduced.

4. **Ramp — missing target in profile JSON**  
   Add `{ "type": "weight", "operator": "gte", "value": 6 }` on Ramp (use `weight`, not `predicted_weight`, for “on cup now” — see `docs/this-fork.md`).

---

## Verification checklist (next occurrence)

- [ ] Note **Display / Controller** versions on Settings screen  
- [ ] During Compression: brew UI shows **`current / X g`** (not seconds-only bar)  
- [ ] Export profile JSON: confirm `phases[].targets` for each phase (not description only)  
- [ ] Pull shot: `./scripts/gaggimate-fetch-latest-shot.sh --latest`  
- [ ] Compare phase end time to first sample where `v` crosses threshold  
- [ ] If log shows threshold but phase runs to `duration`: compare **raw** scale reading on brew screen at that instant vs logged `v`

---

## Optional fix (not implemented)

Align stop comparison with shot-log quantization so UI/log/analyzer match firmware stops, e.g. compare rounded 0.1 g steps or add small epsilon:

- In `Phase::isFinished` for weight types, or  
- In `BrewProcess::isCurrentPhaseFinished` before calling `isFinished`

Files: `src/display/models/profile.h`, `src/display/core/process/BrewProcess.h`, `src/display/plugins/ShotHistoryPlugin.cpp` (`encodeUnsigned`).

---

## References

- Shot: `shots/shot-164.json`  
- Profile: `./scripts/gaggimate-profiles.sh show aAFOFNf3z1`  
- Fork doc: `docs/this-fork.md` — `weight` vs `predicted_weight`  
- Schema: `schema/profile.json`  
- Phase stop algorithm (analyzer): `web/src/pages/ShotAnalyzer/docs/PhaseEndStop_Algorithm_English.md`  
- Investigation plan: `.cursor/plans/shot_164_weight_thresholds_5ae879bc.plan.md`

---

## Operator notes (from shot 164)

Rating 5; dose 16 g → 28.1 g. Notes mention no stop at 6 g on ramp, interest in flow-controlled ramp vs pressure ramp, possible preinfusion length change.
