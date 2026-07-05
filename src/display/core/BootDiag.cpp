#include "BootDiag.h"

#include <Preferences.h>
#include <esp_system.h>

namespace BootDiag {

namespace {
Preferences prefs;
esp_reset_reason_t lastReason = ESP_RST_UNKNOWN;
uint32_t brownouts = 0;
uint32_t crashes = 0;
uint8_t prevStage = STAGE_RADIOS;

const char *reasonName(esp_reset_reason_t reason) {
    switch (reason) {
    case ESP_RST_POWERON:
        return "poweron";
    case ESP_RST_EXT:
        return "external";
    case ESP_RST_SW:
        return "software";
    case ESP_RST_PANIC:
        return "panic";
    case ESP_RST_INT_WDT:
        return "int_wdt";
    case ESP_RST_TASK_WDT:
        return "task_wdt";
    case ESP_RST_WDT:
        return "wdt";
    case ESP_RST_DEEPSLEEP:
        return "deepsleep";
    case ESP_RST_BROWNOUT:
        return "brownout";
    case ESP_RST_SDIO:
        return "sdio";
    default:
        return "unknown";
    }
}
} // namespace

void begin() {
    lastReason = esp_reset_reason();

    prefs.begin("bootdiag", false);
    prevStage = prefs.getUChar("stage", STAGE_RADIOS);
    brownouts = prefs.getUInt("brownouts", 0);
    crashes = prefs.getUInt("crashes", 0);

    if (lastReason == ESP_RST_BROWNOUT) {
        brownouts++;
        prefs.putUInt("brownouts", brownouts);
    } else if (lastReason == ESP_RST_PANIC || lastReason == ESP_RST_INT_WDT || lastReason == ESP_RST_TASK_WDT ||
               lastReason == ESP_RST_WDT) {
        crashes++;
        prefs.putUInt("crashes", crashes);
    }

    prefs.putUChar("stage", STAGE_EARLY);

    Serial.printf("[BootDiag] reset reason: %s, previous boot reached stage %u, brownouts: %u, crashes: %u\n",
                  reasonName(lastReason), prevStage, brownouts, crashes);
}

void markStage(Stage stage) { prefs.putUChar("stage", stage); }

const char *lastResetReasonName() { return reasonName(lastReason); }

uint32_t brownoutCount() { return brownouts; }

uint32_t crashCount() { return crashes; }

uint8_t previousBootStage() { return prevStage; }

} // namespace BootDiag
