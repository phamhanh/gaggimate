
#ifndef CONSTANTS_H
#define CONSTANTS_H

#define PING_INTERVAL 1000
#define PROGRESS_INTERVAL 100
#define HOT_WATER_SAFETY_DURATION_MS 120000
#define STEAM_SAFETY_DURATION_MS 600000
#define BREW_MIN_DURATION_MS 5000
#define BREW_MAX_DURATION_MS 300000
#define BREW_SAFETY_DURATION_MS BREW_MAX_DURATION_MS
#define BREW_MIN_VOLUMETRIC 5.0
#define BREW_MAX_VOLUMETRIC 250.0
#define DEFAULT_STANDBY_TIMEOUT_MS 900000
#define MIN_TEMP 0
#define MAX_TEMP 160
#define DEFAULT_TEMPERATURE_OFFSET 0
#define DEFAULT_PRESSURE_SCALING 16.0f
#define DEFAULT_PID "58.397,1.027,249.055,1.0"
#define DEFAULT_PUMP_MODEL_COEFFS "10.205,5.521"
#define DEFAULT_MDNS_NAME "gaggimate"
#define DEFAULT_OTA_CHANNEL "latest"
#define DEFAULT_TIMEZONE "Europe/Rome"
#define DEFAULT_HOME_ASSISTANT_TOPIC "homeassistant"
#define DEFAULT_STEAM_PUMP_PERCENTAGE 4.f
#define DEFAULT_STEAM_PUMP_CUTOFF 2.f
#define MODE_STANDBY 0
#define MODE_BREW 1
#define MODE_STEAM 2
#define MODE_WATER 3
#define MODE_GRIND 4

// Alt Relay / SSR2 Function constants
#define ALT_RELAY_NONE 0
#define ALT_RELAY_GRIND 1
#define ALT_RELAY_STEAM_BOILER 2

#define WIFI_CONNECT_TIMEOUT_MS 45000
#define WIFI_STA_RETRY_INTERVAL_MS 60000
#define WIFI_STA_DISCONNECT_WINDOW_MS 120000
#define WIFI_STA_DISCONNECT_THRESHOLD 3
#define DEFAULT_WIFI_AP_TIMEOUT_MS 600000

// Boot power staging: the PLC's internal 5V supply browns out if backlight,
// WiFi connect bursts and BLE scanning all peak together during boot.
// Backlight is capped (0-16 steps) and BLE start is staggered after WiFi
// until "controller:boot:complete"; WiFi TX is reduced during the initial
// connect and restored to full power once an IP is acquired.
#define BOOT_BRIGHTNESS_CAP 6
#define BOOT_BLE_STAGGER_MS 1500
#define WIFI_BOOT_TX_POWER WIFI_POWER_15dBm

#endif // CONSTANTS_H
