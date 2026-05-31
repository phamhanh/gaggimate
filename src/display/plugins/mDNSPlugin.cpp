#include "mDNSPlugin.h"
#include "../core/Controller.h"
#include "../core/Event.h"
#include <ESPmDNS.h>
#include <WiFi.h>
#include <esp_log.h>
#include <version.h>

static constexpr char LOG_TAG[] = "mDNSPlugin";

void mDNSPlugin::setup(Controller *controller, PluginManager *pluginManager) {
    this->controller = controller;
    pluginManager->on("controller:wifi:connect", [this](Event const &event) { start(event); });
    pluginManager->on("controller:wifi:disconnect", [this](Event const &) { stop(); });
}

void mDNSPlugin::stop() {
    if (!mdnsActive) {
        return;
    }
    MDNS.end();
    mdnsActive = false;
    ESP_LOGI(LOG_TAG, "mDNS responder stopped");
}

void mDNSPlugin::start(Event const &event) {
    stop();

    const int apMode = event.getInt("AP");
    if (apMode) {
        return;
    }
    if (!MDNS.begin(controller->getSettings().getMdnsName().c_str())) {
        ESP_LOGE(LOG_TAG, "Error setting up mDNS responder");
        return;
    }

    // Advertise HTTP service for web interface
    MDNS.addService("http", "tcp", 80);

    // Advertise custom gaggimate service for Home Assistant discovery
    MDNS.addService("gaggimate", "tcp", 80);

    // Add service metadata as TXT records
    MDNS.addServiceTxt("gaggimate", "tcp", "version", BUILD_GIT_VERSION);
    MDNS.addServiceTxt("gaggimate", "tcp", "type", "espresso_machine");

    mdnsActive = true;
    ESP_LOGI(LOG_TAG, "mDNS responder started with service advertisement");
}
