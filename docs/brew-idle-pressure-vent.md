# Brew idle pressure vent

## Purpose

On pressure-capable GaggiMate setups, the puck-line pressure sensor can read non-zero **between shots** while the UI is in **brew mode**. Previously the display firmware always commanded **valve closed** and **pump off** when idle, so residual pressure could not bleed off before the next pull.

This feature automatically opens the **3-way solenoid** (pump remains off) when idle pressure is high enough, and closes it again once pressure has dropped—using hysteresis so the valve does not flutter near the threshold.

## Behavior

| Condition | Action |
|-----------|--------|
| `MODE_BREW`, no active process, controller reports pressure capability | Vent logic may run |
| Measured pressure **>** high threshold | Latch vent **on** (valve open, pump 0) |
| Measured pressure **<** low threshold while latched | Latch vent **off** (resume normal idle: valve closed, pump 0) |
| Any active process (brew / steam / water / grind on `currentProcess`) | Normal process output only; vent logic skipped |
| Mode leaves brew (`standby`, steam, etc.) | Vent latch cleared |

Pressure readings use the same `pressure` value already received over BLE from the controller (`Controller::setupBluetooth` sensor callback).

## Thresholds (bar)

Defined as `constexpr` in `Controller::updateControl()` in [`src/display/core/Controller.cpp`](src/display/core/Controller.cpp):

- **`BREW_IDLE_VENT_PRESSURE_HIGH_BAR`** (**`0.26`**): latch vent **on** only when pressure rises **above** this—reduces nuisance cycling vs a low threshold (e.g. 0.10 bar).
- **`ventPressureLowBar`** (Web Settings, default **`0.01`**): latch vent **off** when pressure drops **below** this. Raise to e.g. **0.10–0.20** for a shorter hiss if bleeding to ~0 bar takes too long. Must stay **below** `ventPressureBar`.

Keep **HIGH > LOW**. Tune both if sensor idle noise or drip/noise trade-offs change.

### Drip and noise

Opening the valve bleeds the group path: **some hiss is normal**. **Higher** trapped pressure before vent can correlate with **slightly more** drip at the brew head vs marginal readings; both are still **pump-off** relief, not an active shot. Behavior varies by machine geometry and puck wetness.

## Brew screen title (LilyGo / LVGL)

While idle vent is latched, [`DefaultUI::setupReactive()`](src/display/ui/default/DefaultUI.cpp) toggles [`ui_BrewScreen_mainLabel3`](src/display/ui/default/lvgl/screens/ui_BrewScreen.c):

| State | Label text |
|-------|------------|
| Normal brew idle (`brewIdleVenting` false or no pressure kit) | **`Brew`** |
| Idle vent active (`pressureAvailable` + `brewIdleVenting`) | **`Bleeding group pressure`** / **`Valve open — pump off`** (two lines) |

[`Controller::isBrewIdleVenting()`](src/display/core/Controller.h) exposes the latch for UI snapshots (`setupState` + rerender block). **Steam → brew:** entering steam clears the latch via `setMode`; returning to brew shows **`Brew`** until pressure again rises above **HIGH** and vent re-latches—then the title switches to the vent copy like any other idle vent cycle.

## Shot start

When a brew starts, `isActive()` becomes true immediately for control purposes: **`updateControl()` stops taking the vent branch** and sends valve/pump targets from `BrewProcess` (simple or advanced). There can be a **short** window (one control period / BLE write) where the last idle command still applied; in practice the next process command overrides this.

## Caveats

- Goal is **near-zero** pressure within sensor noise when LOW is very small; very noisy sensors may need a slightly higher LOW.
- Brief valve-open idle vent may produce drips—similar to relieving pressure manually.
- Only applies when `systemInfo.capabilities.pressure` is true (pressure-capable controller firmware/hardware).

## Related code

- State flag: `Controller::brewIdleVenting` in [`src/display/core/Controller.h`](src/display/core/Controller.h)
- Logic: [`src/display/core/Controller.cpp`](src/display/core/Controller.cpp) (`updateControl`, `setMode`)

## Cursor plans

- Core vent + thresholds: `.cursor/plans/brew_idle_pressure_vent_6706faa3.plan.md`
- UI copy (title reuse): `.cursor/plans/brew_vent_ui_message_52c3fd5e.plan.md`
