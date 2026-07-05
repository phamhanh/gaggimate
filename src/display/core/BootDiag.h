#pragma once

#include <Arduino.h>

/**
 * Persistent boot diagnostics for the display board.
 *
 * The display sometimes fails to boot on the machine's internal 5V supply
 * while booting fine on USB — a power-delivery (brownout) signature. Because
 * serial is unreachable in that state, each boot records why the chip last
 * reset and how far the previous boot progressed to NVS, readable later over
 * USB serial or /api/status once the board is on a healthy supply.
 */
namespace BootDiag {

// Stages a boot passes through; the last recorded value tells how far the
// previous boot got before it died.
enum Stage : uint8_t {
    STAGE_EARLY = 0,  // entered setup()
    STAGE_SCREEN = 1, // panel + UI up, radios not started
    STAGE_RADIOS = 2, // WiFi + BLE init complete
};

// Call first thing in setup(): reads the previous boot's reason and stage,
// bumps persistent counters, logs a summary line, and marks STAGE_EARLY.
void begin();

void markStage(Stage stage);

const char *lastResetReasonName();
uint32_t brownoutCount();
uint32_t crashCount();
uint8_t previousBootStage();

} // namespace BootDiag
