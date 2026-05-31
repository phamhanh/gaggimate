# Gaggimate Development Scripts

This directory contains various utility scripts for development and debugging.

## Ship firmware (default)

### `deploy.sh` — backup → release → OTA → verify

**This is the only command to use for a normal release.** It backs up profiles and shots from the machine by default, builds SPIFFS with that data, publishes to [GitHub Releases](https://github.com/phamhanh/gaggimate/releases), OTA-flashes from `out/`, and verifies display + controller versions match the new tag.

**One-time setup:**

```bash
brew install gh
gh auth login
```

**Typical ship** (clean working tree, device on LAN):

```bash
./scripts/deploy.sh --dry-run
./scripts/deploy.sh -- --yes
```

**Options:**

```bash
./scripts/deploy.sh --no-backup              # faster: skip device pull (stale SPIFFS risk)
./scripts/deploy.sh --release-only -- --yes  # backup + publish, no OTA
./scripts/deploy.sh --update-only            # OTA from local out/ only
./scripts/deploy.sh -- --patch --yes           # semver bump
./scripts/backup-spiffs-data.sh --dry-run    # backup preview only
```

Deploy requires a **clean working tree** (commit or stash first). After OTA, check `http://gaggimate.local/ota` — both **Display** and **Controller** must show the new tag.

See `.cursor/skills/gaggimate-deploy/SKILL.md`.

### `update-device.sh` — GitHub latest → OTA (no build)

When a release already exists on GitHub but the machine is behind (no backup, compile, or publish):

```bash
./scripts/update-device.sh --dry-run
./scripts/update-device.sh
./scripts/update-device.sh --version v1.9.5
```

Not the same as `deploy.sh --update-only` (that uses bins in local `out/`).

### `release.sh` — internal build engine

Called by `deploy.sh`. **Do not run directly** for normal ships — it will exit with a pointer to `deploy.sh`.

Direct use only:

```bash
./scripts/release.sh --dry-run
./scripts/release.sh --build-only
./scripts/release.sh --offline --yes   # device unreachable; stale SPIFFS risk
```

**Build only** (no tag, publish, or push):

```bash
./scripts/build-firmware.sh --version v1.9.1
```

Artifacts land in `out/` (`display-firmware.bin`, `display-filesystem.bin`, `board-firmware.bin`, `release-manifest.json`).

**OTA page (System & Updates):** Save & Refresh checks GitHub; the UI shows **Latest on GitHub** separately from installed versions. See [docs/this-fork.md](../docs/this-fork.md#releases--ota).

## Profile maintenance (device WebSocket)

### `gaggimate_profiles.py` / `gaggimate-profiles.sh`

List, inspect, and delete profiles on a running Gaggimate when the web UI profiles page fails (corrupt JSON, invalid `type`, empty phases, etc.). Uses the firmware WebSocket API; no extra Python packages.

```bash
./scripts/gaggimate-profiles.sh list
./scripts/gaggimate-profiles.sh list --broken-only
./scripts/gaggimate-profiles.sh show <profile-id>
./scripts/gaggimate-profiles.sh delete <id> [<id> ...]
./scripts/gaggimate-profiles.sh delete-broken --dry-run
./scripts/gaggimate-profiles.sh delete-broken --yes

# AP mode or custom IP
./scripts/gaggimate-profiles.sh list --host 4.4.4.1
GAGGIMATE_HOST=192.168.1.50 ./scripts/gaggimate-profiles.sh list
```

See also `.cursor/skills/gaggimate-profiles/SKILL.md` for agent-oriented recovery steps.

## Core Dump Analysis

### `analyze_coredump.py` / `analyze_coredump.sh`

Automated ESP32 core dump analysis for PlatformIO projects.

**Features:**

- Automatically extracts ELF core dump from ESP32 proprietary format
- Uses ESP-IDF GDB tools for detailed analysis
- Shows exact crash location with line numbers
- Displays full call stack (backtrace)
- Shows register values at time of crash
- Lists all threads and their states
- Provides actionable debugging recommendations

**Usage:**

```bash
# Python script (direct)
python3 scripts/analyze_coredump.py <coredump_file> [environment]

# Shell wrapper (simpler)
./scripts/analyze_coredump.sh <coredump_file> [environment]
```

**Examples:**

```bash
# Analyze core dump with default environment (display)
python3 scripts/analyze_coredump.py ~/Downloads/coredump.bin

# Analyze core dump with specific environment
python3 scripts/analyze_coredump.py ~/Downloads/coredump.bin display
python3 scripts/analyze_coredump.py ~/Downloads/coredump.bin controller
python3 scripts/analyze_coredump.py ~/Downloads/coredump.bin display-headless

# Using shell wrapper
./scripts/analyze_coredump.sh ~/Downloads/coredump.bin
./scripts/analyze_coredump.sh ~/Downloads/coredump.bin controller
```

**Requirements:**

- ESP-IDF tools installed (automatic with VS Code ESP-IDF extension)
- PlatformIO project with built firmware
- Python 3.x

**Sample Output:**

```
🚀 ESP32 Core Dump Analyzer
==================================================
Core dump: /home/user/Downloads/coredump.bin
Environment: display

✅ Found GDB: xtensa-esp32s3-elf-gdb
✅ ELF header found at offset: 20
✅ Extracted ELF core dump to: /tmp/tmpXXXXX.elf

================================================================================
🔍 CORE DUMP ANALYSIS
================================================================================
#0  DefaultUI::updateStatusScreen (this=0x3fced5e4) at src/display/ui/default/DefaultUI.cpp:655
655         if (process->getType() != MODE_BREW) {
#1  0x420254df in DefaultUI::loop (this=0x3fced5e4) at src/display/ui/default/DefaultUI.cpp:230
#2  0x42025510 in DefaultUI::loopTask (arg=0x3fced5e4) at src/display/ui/default/DefaultUI.cpp:766
...
```

**Getting Core Dumps:**

1. **From Web Interface:** Visit `http://your-device-ip/`, go to System & Updates, click "Download Core Dump"
2. **From Serial Monitor:** Core dumps appear in terminal output after crashes
3. **From Device Flash:** Use `esptool.py` to read core dump partition

**Interactive Analysis:**
For deeper debugging, use the extracted ELF file with GDB interactively:

```bash
xtensa-esp32s3-elf-gdb .pio/build/display/firmware.elf
(gdb) core-file /tmp/extracted_coredump.elf
(gdb) bt
(gdb) list
(gdb) info locals
(gdb) print variable_name
```
