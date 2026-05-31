# Gaggimate Development Scripts

This directory contains various utility scripts for development and debugging.

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
