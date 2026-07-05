# Handover: display brownout on controller/PLC power — NOT resolved

**Date:** 2026-07-05 · **Status:** open. Firmware mitigations shipped and help, but the
display **still gets stuck in a boot loop** when powered from the machine's internal 5V
(the "other ESP32" / GaggiMate controller PCB). Rock solid on USB.

## The problem in one line

The panel is the **larger LilyGo T-RGB (2.7/2.8″ class)**, but the GaggiMate kit's internal
5V budget was sized for the **2.1″** panel. The bigger backlight + RGB scan-out draws more than
the shared supply can deliver at boot, so the ESP32-S3 brownout detector keeps resetting it.

## Hard evidence (read this first)

`GET http://gaggimate.local/api/status` → `boot` object (added this session, `BootDiag`):

```
boot: { resetReason: "brownout", prevStage: 1, brownouts: 91, crashes: 2 }
```

- `resetReason: brownout` — the ESP32-S3 brownout detector is firing. Definitive.
- `prevStage: 1` — the previous boot reached **STAGE_SCREEN** (panel + LVGL + backlight up) but
  died **before STAGE_RADIOS** (2). So it browns out at the screen-init power peak, before/at WiFi+BLE.
- `brownouts: 91` and climbing — chronic, not a one-off. It occasionally wins a boot (that's why
  it's sometimes reachable), but on PLC power it mostly loops.

Stages are defined in `src/display/core/BootDiag.h` (`STAGE_EARLY=0`, `STAGE_SCREEN=1`, `STAGE_RADIOS=2`),
marked in `src/display/core/Controller.cpp` (`BootDiag::markStage(...)`).

## What has been tried this session (all shipped in v1.9.24 / on master)

| Commit | Change | Effect |
|--------|--------|--------|
| `236d51c7` | Boot power **staging**: backlight capped 6/16 until radios up; WiFi connects at 15 dBm then full power on got-IP; BLE start staggered 1.5 s after WiFi. `BootDiag` reset-reason/stage counters in NVS → `/api/status`. | Helps it eventually win the boot race; not enough. |
| `9b7be9ef` | Shrank LVGL draw buffer 60→20 lines (freed 38 KB internal SRAM); OTA-TLS heap-headroom guard; `heap` in `/api/status`. | Fixed a separate WiFi-wedge bug. |
| `ab154a21` | Steady-state draw: LVGL refresh 100→**25 fps**, CPU 240→**160 MHz**. | Cuts running current; boot still fails. |
| `17fe7a43` | 15 s task WDT + paced backup client. | Fixed backup task_wdt panic. |
| `5d01fa26` | Defer OTA TLS check while serving bulk downloads. | Fixed WiFi wedge during backup. |

Backlight steps 0–16 live in `src/display/drivers/LilyGo-T-RGB/LilyGo_RGBPanel.cpp` (`setBrightness`).
Boot constants: `src/display/core/constants.h` (`BOOT_BRIGHTNESS_CAP`, `BOOT_BLE_STAGGER_MS`, `WIFI_BOOT_TX_POWER`).
Boot sequence: `Controller::setup()` → `setupPanel()` → `ui->init()` (STAGE_SCREEN) → `connect()` →
`setupWifi()` → stagger → `setupBluetooth()` (STAGE_RADIOS) in `src/display/core/Controller.cpp`.

## Remaining firmware levers (ranked, for the next agent)

The boot dies at STAGE_SCREEN, so attack the screen-init current peak and the reset trigger:

1. **Backlight fully OFF during boot** (biggest UI power draw). Currently capped at 6/16 but still on.
   Try `setBrightness(0)` until `controller:boot:complete`, then ramp to configured. Test if it boots dark
   then lights up. Edit `DefaultUI::setupPanel()` / the `controller:boot:complete` handler in
   `src/display/ui/default/DefaultUI.cpp`.
2. **Lower/disable the brownout detector threshold** — *highest-impact but riskiest*. The resets ARE the
   BOD firing; if the dips are transient, riding through them may let it boot. Set
   `CONFIG_ESP_BROWNOUT_DET_LVL` lower, or disable BOD, via an sdkconfig override. **Caveat:** a real
   undervoltage during a flash write can corrupt flash — document and test carefully. Build config is in
   `platformio.ini` `[display_common]`/`[env:display]` `build_flags` (no sdkconfig override present yet).
3. **80 MHz CPU during boot**, raise to 160 after radios (min 80 for WiFi). Lower boot inrush.
   `setCpuFrequencyMhz(80)` in early `setup()`, bump in `controller:boot:complete`.
4. **Add settle delays between init stages** so inrush peaks don't stack (let supply caps recover):
   small `delay()`s between panel init, `WiFi.begin`, and BLE init.
5. **Lower boot WiFi TX further** — `WIFI_BOOT_TX_POWER` is 15 dBm; try `WIFI_POWER_8_5dBm` for the connect burst only.

Realistically these are diminishing returns. **The durable fix is hardware** (below).

## The real fix is hardware (tell the user)

- **Power the display from a separate 5V USB phone charger.** It's proven rock-solid on USB.
- **Do NOT feed it from the controller ESP32's USB-C** (user asked): that VBUS comes off the same
  internal 5V rail through a diode drop — same or worse.
- If they want it on internal power: stiffer 5V supply, thicker/shorter cable, better connector, and/or
  a big bulk capacitor across the display's 5V input to survive inrush.

## Deploy / OTA state — IMPORTANT

- **v1.9.24 is built and published** to GitHub (`https://github.com/phamhanh/gaggimate/releases/tag/v1.9.24`),
  master + tag pushed. Working tree clean.
- **Display firmware** was flashed over USB this session and already contains all 5 fixes (== v1.9.24 content).
- **Controller was NOT updated — still v1.9.22.** The `--update-only` OTA pass was interrupted before it ran.
- **Known issue (see memory `controller-ota-needs-targeted-update`):** `deploy.sh`'s combined OTA often
  leaves the controller behind; it needs a **targeted** pass. To finish it (display on stable USB power):
  ```
  ./scripts/deploy.sh --update-only --controller-only --host 192.168.51.2   # from out/ (v1.9.24 built)
  # or, from GitHub latest:
  ./scripts/update-device.sh --controller-only
  ```
  Then verify BOTH at `http://gaggimate.local/ota`.
- **Backups are safe:** fresh profiles+shots (13 profiles, 123 shots) captured to `data/p` `data/h` and a
  snapshot under `device-data/snapshots/` before the release, so the baked `display-filesystem.bin` is current.

## Deploy/backup gotchas learned this session

- Device IP is **192.168.51.2**; mDNS (`gaggimate.local`) is flaky — pass `--host 192.168.51.2`.
- On PLC power the supply can't sustain a full backup (browns out mid-transfer). **Do deploy/OTA with the
  display on USB power.**
- deploy.sh version bump: default patch from latest origin tag → next is **v1.9.25**.

## Diagnostics available

- `GET /api/status` → `boot` (reset reason/stage/counters) and `heap` (free/min/maxBlock internal SRAM).
- `GET /api/core-dump` → raw ELF core dump (20-byte flash header prefix). Analyze:
  `~/.venvs/default/bin/python -m esp_coredump info_corefile --gdb ~/.platformio/packages/toolchain-xtensa-esp32s3/bin/xtensa-esp32s3-elf-gdb --core <file> --core-format raw .pio/build/display/firmware.elf`
- Serial over USB: `/dev/cu.usbmodem101` @ 115200; `[BootDiag]` line prints reset reason + counters each boot.
- Build+flash display: `~/.venvs/default/bin/pio run -e display -t upload --upload-port /dev/cu.usbmodem101`

## Related memory files

`display-plc-power-brownout`, `display-wifi-tls-heap-exhaustion`, `controller-ota-needs-targeted-update`.
