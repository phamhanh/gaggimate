# Why this repository is different

I run [Gaggimate](https://github.com/jniebuhr/gaggimate) on my **Iberital Express** (Gaggimate **PCB v1.1**, stock kit — no custom hardware yet). Almost everything different from upstream in this repository was written **with AI assistance** (Claude Code and Cursor) — I did not write the C++ myself. It is tuned for **this one machine**, not as a product fork.

## The machine

The Iberital Express is a Silvia-class single-boiler machine — mechanically similar to the Rancilio Silvia, which Gaggimate also supports (the "Sylva" build). It is a precursor to the Lelit Anna. The boiler is roughly 60 mm wide × 100 mm tall (~280 ml), similar in volume to a Gaggia Classic brass boiler.

I found this machine and confirmed it works with Gaggimate. If you have one and are trying to do the same, the Sylva build profile is your closest starting point. Connectors and thermocouples are the same type as the Silvia — I damaged a few during assembly and sourced replacements through the AliExpress links Gaggimate provides.

## Why this fork exists

I forked [jniebuhr/gaggimate](https://github.com/jniebuhr/gaggimate) and pointed OTA at [my GitHub releases](https://github.com/phamhanh/gaggimate/releases) for one practical reason: **I want to update firmware over Wi‑Fi from my Mac**, not plug in USB every time. The repo is **public only because OTA needs a public release URL** — I am not inviting others to use this firmware on their machines, and I am grateful the Gaggimate team publishes upstream openly.

Upstream docs for building and sourcing the kit stay at [gaggimate.eu](https://gaggimate.eu/). This document is my notes on what I changed and why.

## Releases and OTA

Upstream releases through GitHub Actions and shop channels. I ship from my Mac with **`./scripts/deploy.sh`**: **backup profiles and shots by default**, bump version, build locally, upload bins to GitHub Releases, OTA the machine, and verify both display and controller report the new tag. I wanted **control over when builds ship** without waiting on CI.

If GitHub already has a release but the machine is behind, **`./scripts/update-device.sh`** OTA-updates to the latest tag without rebuilding.

`./scripts/release.sh` is only the internal build step (or `--offline` when the device is unreachable). For a faster ship when SPIFFS has not changed, **`./scripts/deploy.sh --no-backup`**.

I also changed the **System & Updates** page so it stops mixing up "what GitHub says is latest" with "what is actually installed". Save & Refresh hits GitHub immediately; the UI shows **Latest on GitHub** separately from **Controller** and **Display** versions, and warns if Wi‑Fi blocked the check.

Details and flags: [scripts/README.md](../scripts/README.md).

## Deploy without losing profiles and shots

Display OTA **wipes SPIFFS** — profiles and shot history live on the display filesystem, so a normal OTA used to **erase my data**.

**`./scripts/deploy.sh`** backs up from `gaggimate.local` **before every release by default**, bakes `/p/` and `/h/` into `display-filesystem.bin`, publishes, then OTA-flashes from `out/`. After reboot, profiles and history are **inside the filesystem image**, not restored over WebSocket afterward.

**`gaggimate-profiles.sh`** is for when corrupt profile JSON breaks the web profiles page — list and delete over WebSocket without USB.

## WiFi and connectivity

I rely on Wi‑Fi for OTA and daily use at `gaggimate.local`. My machine would drop off home Wi‑Fi, get stuck in AP mode, and mDNS would stop resolving after reconnect — so I could not reach the web UI or ship updates without USB.

Firmware changes vs upstream:

- Longer boot connect timeout before giving up on saved credentials
- Retry home SSID from AP fallback instead of staying trapped in AP mode
- Debounce runtime disconnects before falling back to AP
- Restart mDNS when Wi‑Fi reconnects
- Wi‑Fi status on **Settings** and in `evt:status` so I can see what the display thinks is happening

## Temperature during shots (the big firmware change)

### Why the probe placement matters on this machine

On the Iberital Express, the temperature probe sits near the **cold-water inlet** by design — this is intentional hardware design so the mechanical thermostat fires the heater as early as possible. The machine was built for mechanical on/off control; the probe was never meant to be a brew-temperature sensor. When you add a PID on top of that probe location, you get a mismatch: the PID is reading the coldest part of the boiler, not the group temperature.

During a shot, that reading **lags** what is actually happening at the group.

**The old problem (full PID during flow):** While water was moving, the probe often read **too cold**. PID kept integrating and adding heat. When flow stopped, all that extra heat plus lagging probe feedback produced **overshoot after the shot** — the graph looked cold during flow, then spiked hot afterward.

**What the freeze fixes:** During flow, **P and D no longer update** from that probe; only a **stable I baseline** (latched at shot start) plus **Kff** drive the heater. PID is not “off” — its **updates** are frozen. That stops PID from making the post-shot problem worse.

**What freeze does not fix:** If **Kff is too strong**, it can still put too much heat into the boiler **during** the shot. You may not see that on the probe until flow stops. A **large spike after the shot** (during grace) means **Kff delivered too much during the shot** — tune Kff gain or profile `tt`, not “normal lag.”

### My approach

Developed iteratively with AI:

1. **Before the shot:** wait until the boiler is **stable** (integral settled). Brew screen: an **animated steaming-cup** indicator while warming/settling, then **Ready to brew**.
2. **During the shot:** **freeze PID updates to I only** (P/D stopped from probe) and add **Kff** from flow, `tt`, and inlet temp. The graph may still look **too cold during flow** — the probe lags; that alone is not proof Kff is wrong.
3. **After the shot (grace):** hold the **same I-only baseline**, **no Kff**, still no P/D updates from the probe — so PID cannot pile on extra heat while the probe catches up. **Expected:** temp settles near target. **Spike well above target** → **Kff was too strong during the shot**; lower Kff gain or drop `tt` (e.g. at “The Drop”).
4. **Tune in the profile**, not from live `ct` during flow. After grace, stabilized `ct` is the best retrospective guess at shot temperature.

### What I changed in the controller

- **Removed the Kff safety taper** — only Kff is fast enough to react to incoming water during flow. (That is separate from PID: PID is frozen then; the taper was blocking Kff when we need it.)
- **Stable before a shot** — settle **I** first, then **Kff** handles flow disturbance. Vent and “Ready to brew” wait for **stable** temp.
- **During flow** — latch **I only**; **Kff adds** on measured flow. P/D **updates frozen** — they no longer overreact to a cold-looking probe mid-shot.
- **After flow (grace)** — same **I-only** hold, **no Kff**. Grace stops **PID** from reacting to the lagging probe after flow stops. It does **not** excuse a big overshoot: that heat was already added by **Kff during the shot** if the spike appears.
- **Inlet temp** — manual setting until a real inlet probe exists (see [thermal-kff-tuning.md](thermal-kff-tuning.md)); water **pre-heats in the machine plumbing**, so a room-temp guess is often wrong.
- **Grace period (60 s default)** — measured empirically: ran a test shot, then held the valve open with flow at 0.1 g/s and watched how long it took for temperature to stabilize. Kff was technically active at that trickle but not meaningfully contributing; PID stayed frozen at I. 60 s was where `ct` reliably settled.
- **Kff enable toggle** in Settings — I leave it on.

### What I tried and did not keep

- **Kff lookahead** — profiles are reactive; flow is not predictable before the valve opens.
- **Smooth `tt` ramps** between phases — maybe later; without better sensors it does not help yet.

### Autotune and heater wattage

This fork includes upstream autotune (SIMC, Kff-from-wattage). **I did not autotune this Iberital Express.** The boiler element is **1000 W** (final). Fresh firmware defaults use **Kff = 1.0** in the PID CSV (`combinedKff = 1000 / 1000 W`). See [thermal-kff-tuning.md](thermal-kff-tuning.md) for all Settings names and defaults.

### Planned hardware

Three **1 mm K-type** sensors: grouphead brew path, boiler exit, and **live inlet** (the only one Kff really needs). Challenge: cheap double-ferrule 1 mm fittings.

I added **0.1 °C** dials, a softer thermocouple filter, and **inlet water ±** on the brew screen.

### The real fix: a pre-heater block

The fundamental problem is that cold water enters the boiler during a shot and the inlet probe can only react, never anticipate. The only way to eliminate temperature sag entirely is to **heat the water before it reaches the boiler**.

The plan: take a replacement heater block from any standard cheap espresso machine and plumb it between the pump and the boiler inlet using **6 mm hoses**. A **T-connector** in the line carries a **1 mm thermocouple fitting** with a probe seated at the exit end of the block — inside the block, measuring actual exit temperature. That gives accurate closed-loop control of what the boiler actually receives.

With that in place the control architecture becomes clean:

- **Pre-heater PID + Kff** — handles the thermal load of incoming water; Kff reacts to flow on the inlet side where it actually matters
- **Boiler PID** — back to normal operation, no longer fighting cold-water ingress; the freeze / I-only workaround goes away

Combined with the three K-type probes (grouphead, boiler exit, live inlet), this would give fully independent control of each thermal stage and a genuinely stable shot temperature. The current Kff freeze approach is a software workaround for a hardware problem; this is the hardware solution.

## Pressure between shots

On my boiler, pressure rises at startup and between shots as heat expands leftover air or as water boils. I should use **Flush**, but I often do not, so I added **brew idle pressure vent**: in brew mode, when idle and **stable**, the firmware opens the 3-way valve (pump off) until pressure drops.

Operator intent (boiler expansion): this section. Thresholds: [brew-idle-pressure-vent.md](brew-idle-pressure-vent.md).

## Brew screen — stabilizing animation

Upstream shows a plain `▲` / `▼` arrow and **Stabilizing** while the boiler reaches target temperature before a shot. I replaced that with an **animated espresso cup and rising steam wisps** (theme-recolored LVGL images, same treatment as other icons), plus direction-aware captions **Warming up** and **Settling**. When the boiler is stable, the animation hides and the caption reads **Ready to brew** (unchanged from upstream intent).

The widgets are built at runtime in `DefaultUI.cpp` and parented onto the brew panel — SquareLine-generated screen files stay untouched. Icons are generated by `scripts/gen_steam_icons.py` (dependency-free SDF rasterizer → `TRUE_COLOR_ALPHA` C arrays in `src/display/ui/default/lvgl/images/`).

## Smaller fixes on this machine

- **Pump assist** — Assist only ran after steam was "ready". Opening the steam valve before that let the boiler run dry; **I destroyed a heater element doing this** (see below). Fix: in steam mode, assist runs whenever pressure is below the cutoff (~2 bar), including while the boiler is still heating.
- **Boiler fill** — startup fill runs with the **valve open** to **purge air**; I want the boiler actually full.
- **BLE disconnect grace** — a brief BLE drop sent the display to standby while the UI still looked like brew and the boiler cooled. Now it waits before standby.
- **Web Settings pump flow** at 1 / 9 bar gated on **pressure capability**, not dimming.
- **Profile editor** — insert a phase **after the current one** (pre-infusion splits).

## Two dead heater elements

I killed this boiler's heater element twice. Both are instructive.

**First time — descaling accident.** I soaked the heating element in lactic acid solution during descaling. The acid got into the element itself and destroyed it. Keep descaling solution away from the element ends.

**Second time — pump assist bug + my own mistake.** I was testing the pressure venting code, going back and forth on the machine, and did not notice the pump was never assisting to refill the boiler during steam mode. The boiler ran dry and the element burned out. I had also disabled the steam refilling plugin at the time, which removed the last safeguard. If pump assist had worked correctly there would have been enough water to survive. This is what motivated the pump assist fix above.

## Why this matters

I roast my own coffee and grind it on a motorized hand grinder built for ultra-slow 12 RPM extraction (see [See also](#see-also)). The goal is a genuinely great espresso on a budget — not approximate, not "good enough." I know it is possible. The firmware work here is one piece of that.

But the deeper reason: there is a moment — when the coffee, the beans, the labour, the earth, the effort over generations all come together in a swirling rollercoaster ride of taste and sensation — every chanda new, surprising, revealing, leaving you wondering what just happened. That is why.

## How this was built

I did not write the C++ myself. All firmware changes were made through conversation with **Claude Code** and **Cursor** — describing the problem, reviewing diffs, iterating on behaviour. The PID freeze / Kff approach in particular went through many rounds of "the shot ran too hot / too cool, here is the graph, what should change."

## What I am not doing here

- Maintaining a general fork for other machines
- Changing upstream shop, CLA, or community process

If you landed here by accident, use [upstream Gaggimate](https://github.com/jniebuhr/gaggimate).

## See also

- [thermal-kff-tuning.md](thermal-kff-tuning.md) — PID freeze, Kff, Settings variables
- [README](../README.md) — upstream-shaped overview
- [CONTRIBUTING.md](../CONTRIBUTING.md) — release commands for this fork
- **Motorized MHW-3BOMBER R3 hand grinder** (same author): [Instructables build](https://www.instructables.com/Motorizing-Coffee-Hand-Grinder-MHW-3BOMBER-R3-Into/) · [Reddit thread](https://www.reddit.com/r/espresso/comments/1ret457/diy_motorizing_hand_grinder_mhw3bomber_r3_for/)
- **Dharma talk on coffee and mindfulness** (Plum Village, same author): [YouTube](https://www.youtube.com/watch?v=kfcxVhuTZy0&list=PLaX_vxbhs8fg2xlfYszcY9vAW88Z9uBhV&index=4)
