#include "AutoWakeupPlugin.h"
#include <display/core/constants.h>
#include <esp_log.h>

const String LOG_TAG = F("AutoWakeupPlugin");

AutoWakeupPlugin::AutoWakeupPlugin() {}

void AutoWakeupPlugin::setup(Controller *controller, PluginManager *pluginManager) {
    this->controller = controller;
    this->pluginManager = pluginManager;
    this->settings = &controller->getSettings();

    ESP_LOGI(LOG_TAG.c_str(), "Auto-wakeup plugin initialized");

    pluginManager->on("settings:changed", [this](const Event &event) {
        if (settings->isAutoWakeupEnabled()) {
            ESP_LOGI(LOG_TAG.c_str(), "Auto-wakeup enabled with %d schedule(s)", settings->getAutoWakeupSchedules().size());
        } else {
            ESP_LOGI(LOG_TAG.c_str(), "Auto-wakeup disabled");
            wasInReadyWindow = false;
        }
    });
}

void AutoWakeupPlugin::loop() {
    if (!settings->isAutoWakeupEnabled() || settings->getAutoWakeupSchedules().empty()) {
        wasInReadyWindow = false;
        return;
    }

    const unsigned long now = millis();

    if (now - lastAutoWakeupCheck > AUTO_WAKEUP_CHECK_INTERVAL) {
        lastAutoWakeupCheck = now;

        if (isTimeValid()) {
            checkReadyWindows();
        }
    }
}

void AutoWakeupPlugin::checkReadyWindows() {
    const bool inWindow = settings->isCurrentlyInReadyWindow();
    const int mode = controller->getMode();

    if (inWindow && mode == MODE_STANDBY) {
        ESP_LOGI(LOG_TAG.c_str(), "Ready window active, switching to brew mode");
        controller->setMode(MODE_BREW);
        pluginManager->trigger("autowakeup:activated", "time", "window");
    }

    if (!inWindow && wasInReadyWindow && mode != MODE_STANDBY) {
        ESP_LOGI(LOG_TAG.c_str(), "Ready window ended, switching to standby");
        controller->activateStandby();
        pluginManager->trigger("autowakeup:deactivated", "time", "window");
    }

    if (mode == MODE_STANDBY) {
        checkLegacyPointWakeup();
    }

    wasInReadyWindow = inWindow;
}

void AutoWakeupPlugin::checkLegacyPointWakeup() {
    const String currentTime = getCurrentTimeString();
    const int currentDayOfWeek = getCurrentDayOfWeek();

    if (lastCheckedTime == currentTime) {
        return;
    }
    lastCheckedTime = currentTime;

    for (const AutoWakeupSchedule &schedule : settings->getAutoWakeupSchedules()) {
        if (schedule.hasWindow())
            continue;

        if (schedule.time == currentTime && schedule.isDayEnabled(currentDayOfWeek)) {
            ESP_LOGI(LOG_TAG.c_str(), "Auto-wakeup schedule matched (time: %s, day: %d), switching to brew mode",
                     schedule.time.c_str(), currentDayOfWeek);

            controller->setMode(MODE_BREW);
            pluginManager->trigger("autowakeup:activated", "time", schedule.time);
            return;
        }
    }
}

bool AutoWakeupPlugin::isTimeValid() {
    time_t now;
    struct tm timeinfo;
    time(&now);
    localtime_r(&now, &timeinfo);

    return timeinfo.tm_year > (2020 - 1900);
}

String AutoWakeupPlugin::getCurrentTimeString() {
    time_t now;
    struct tm timeinfo;
    time(&now);
    localtime_r(&now, &timeinfo);

    char currentTime[6];
    strftime(currentTime, sizeof(currentTime), "%H:%M", &timeinfo);

    return String(currentTime);
}

int AutoWakeupPlugin::getCurrentDayOfWeek() {
    time_t now;
    struct tm timeinfo;
    time(&now);
    localtime_r(&now, &timeinfo);

    int dayOfWeek = timeinfo.tm_wday;
    if (dayOfWeek == 0) {
        return 7;
    }
    return dayOfWeek;
}
