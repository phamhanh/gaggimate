#ifndef CONTROLLER_H
#define CONTROLLER_H

#include "NimBLEClientController.h"
#include "NimBLEComm.h"
#include "PluginManager.h"
#include "Settings.h"
#include <WiFi.h>
#include <display/core/ProfileManager.h>
#include <display/core/process/Process.h>
#ifndef GAGGIMATE_HEADLESS
#include <display/drivers/Driver.h>
#include <display/ui/default/DefaultUI.h>
#endif

const IPAddress WIFI_AP_IP(4, 4, 4, 1); // the IP address the web server, Samsung requires the IP to be in public space
const IPAddress WIFI_SUBNET_MASK(255, 255, 255, 0); // no need to change: https://avinetworks.com/glossary/subnet-mask/

enum class VolumetricMeasurementSource { INACTIVE, FLOW_ESTIMATION, BLUETOOTH };

class Controller {
  public:
    Controller() = default;

    void setup();
    void connect();
    void loop();
    void loopControl();

    void setMode(int newMode);
    void setTargetTemp(float temperature);
    void setPressureScale();
    void syncPumpConfigToController();
    void setTargetGrindDuration(int duration);
    void setTargetGrindVolume(double volume);
    void syncPidToController();

    int getMode() const;

    float getTargetTemp() const;
    int getTargetGrindDuration() const;
    virtual float getCurrentTemp() const { return currentTemp; }
    bool isActive() const;
    bool isGrindActive() const;
    bool isUpdating() const;
    bool isAutotuning() const;
    bool isReady() const;
    bool isVolumetricAvailable() const;
    bool isSDCard() const { return sdcard; }
    virtual float getTargetPressure() const { return targetPressure; }
    virtual float getTargetFlow() const { return targetFlow; }
    virtual float getCurrentPressure() const { return pressure; }
    virtual float getCurrentPuckFlow() const { return currentPuckFlow; }
    virtual float getCurrentPumpFlow() const { return currentPumpFlow; }
    /** Pump duty 0–100 % (from controller BLE telemetry). */
    virtual float getCurrentPumpPower() const { return currentPumpPower; }
    /** Heater duty 0–1000 (from controller BLE telemetry). */
    virtual float getCurrentHeaterPower() const { return currentHeaterPower; }
    virtual float getPidLiveP() const { return pidLiveP; }
    virtual float getPidLiveI() const { return pidLiveI; }
    virtual float getPidLiveD() const { return pidLiveD; }
    virtual float getPidLiveKff() const { return pidLiveKff; }
    virtual bool isPidLiveFrozen() const { return pidLiveFrozen; }
    virtual bool isPidLivePdMuted() const { return pidLivePdMuted; }
    virtual float getPidLiveKiActive() const { return pidLiveKiActive; }

    /// Idle brew-mode pressure vent latch (UI may reflect valve-open bleed between shots).
    bool isBrewIdleVenting() const { return brewIdleVenting; }
    bool isStableTemp() const { return stableTemp; }
    bool isPidFreezeGraceActive() const {
        return pidFreezeGraceUntil != 0 && millis() < pidFreezeGraceUntil;
    }

    bool isTaskHealthy() const { return is_task_healthy(eTaskGetState(taskHandle)); }

    void autotune(int testTime, int samples, int heaterWattage);
    void startProcess(Process *process);
    Process *getProcess() const { return currentProcess; }
    Process *getLastProcess() const { return lastProcess; }
    Settings &getSettings() { return settings; }
    ProfileManager *getProfileManager() { return profileManager; }
#ifndef GAGGIMATE_HEADLESS
    DefaultUI *getUI() const { return ui; }
#endif
    bool isErrorState() const { return error > 0; }
    int getError() const { return error; }

    // Event callback methods
    void updateLastAction();
    void raiseTemp();
    void lowerTemp();
    void raiseBrewTarget();
    void lowerBrewTarget();
    void raiseGrindTarget();
    void lowerGrindTarget();
    void raiseIncomingWaterTemp();
    void lowerIncomingWaterTemp();
    void activate();
    void deactivate();
    void clear();
    void activateGrind();
    void deactivateGrind();
    void activateStandby();
    void deactivateStandby();
    void onOTAUpdate();
    void onScreenReady();
    void onTargetToggle();
    void onTargetChange(ProcessTarget target);
    void onProfileSave() const;
    void onProfileSaveAsNew();
    void onVolumetricMeasurement(double measurement, VolumetricMeasurementSource source);
    void setVolumetricOverride(bool override) { volumetricOverride = override; }
    bool isBluetoothScaleHealthy() const;
    void onFlush();
    int getWaterLevel() const {
        float reversedLevel = static_cast<float>(settings.getEmptyTankDistance()) -
                              static_cast<float>(std::min(settings.getEmptyTankDistance(), tofDistance));
        return static_cast<int>((reversedLevel - settings.getFullTankDistance()) /
                                static_cast<float>(settings.getEmptyTankDistance() - settings.getFullTankDistance()) * 100.0f);
    };
    int getTofDistance() const { return tofDistance; }

    void onVolumetricDelete();
    bool isLowWaterLevel() const { return getWaterLevel() < 20; };

    SystemInfo getSystemInfo() const { return systemInfo; }

    NimBLEClientController *getClientController() { return &clientController; }

    bool isWifiApFallback() const { return isApConnection; }
    bool isWifiConnected() const { return !isApConnection && WiFi.status() == WL_CONNECTED; }
    String getWifiIp() const;
    String getWifiModeString() const;
    int getWifiRssi() const;
    String getWifiLastDisconnectReasonName() const;

  private:
    // Initialization methods
#ifndef GAGGIMATE_HEADLESS
    void setupPanel();
#endif
    void setupBluetooth();
    void setupInfos();
    void setupWifi();
    void registerWifiEvents();
    bool waitForWifiConnect(unsigned long timeoutMs);
    void configureNtp();
    void startApFallback();
    void attemptStaReconnectFromAp();
    void onWifiGotIp();
    void onWifiDisconnected(WiFiEventInfo_t info);

    // Functional methods
    void updateControl();
    void updateStableTemp();

    // Event handlers
    void onTempRead(float temperature);

    // brew button
    void handleBrewButton(int brewButtonStatus);

    // steam button
    void handleSteamButton(int steamButtonStatus);
    void handleProfileUpdate();

    // Private Attributes
#ifndef GAGGIMATE_HEADLESS
    DefaultUI *ui = nullptr;
    Driver *driver = nullptr;
#endif
    NimBLEClientController clientController;
    hw_timer_t *timer = nullptr;
    Settings settings;
    PluginManager *pluginManager{};
    ProfileManager *profileManager{};

    int mode = MODE_BREW;
    float currentTemp = 0;
    float pressure = 0.0f;
    float targetPressure = 0.0f;
    float currentPuckFlow = 0.0f;
    float currentPumpFlow = 0.0f;
    float currentPumpPower = 0.0f;
    float currentHeaterPower = 0.0f;
    float pidLiveP = 0.0f;
    float pidLiveI = 0.0f;
    float pidLiveD = 0.0f;
    float pidLiveKff = 0.0f;
    bool pidLiveFrozen = false;
    bool pidLivePdMuted = false;
    float pidLiveKiActive = 0.0f;
    float targetFlow = 0.0f;
    int tofDistance = 0;

    SystemInfo systemInfo{};

    Process *currentProcess = nullptr;
    Process *lastProcess = nullptr;

    unsigned long grindActiveUntil = 0;
    unsigned long lastPing = 0;
    unsigned long lastProgress = 0;
    unsigned long lastAction = 0;
    bool loaded = false;
    bool updating = false;
    bool autotuning = false;
    bool isApConnection = false;
    bool staRetryExhausted = false;
    bool wifiEventsRegistered = false;
    unsigned long lastStaRetryMs = 0;
    unsigned long apFallbackStartMs = 0;
    int lastDisconnectReason = 0;
    int staDisconnectCount = 0;
    unsigned long staDisconnectWindowStartMs = 0;
    bool initialized = false;
    bool screenReady = false;
    bool waitingForController = false;
    unsigned long connectStartTime = 0;
    unsigned long bleDisconnectTime = 0; // millis() when BLE last dropped; 0 = connected
    int modeBeforeDisconnect = -1;       // mode to restore grace check against; -1 = none pending
    bool volumetricOverride = false;
    bool processCompleted = false;
    bool steamReady = false;
    bool sdcard = false;
    int error = 0;

    /// Latched while brew-mode idle vent is active (valve commanded open, pump 0). Cleared when
    /// leaving MODE_BREW or when puck pressure drops below LOW threshold—see updateControl().
    bool brewIdleVenting = false;
    bool stableTemp = false;
    unsigned long stableBandSinceMs = 0;
    unsigned long pidFreezeGraceUntil = 0;

    // Bluetooth scale connection monitoring
    VolumetricMeasurementSource currentVolumetricSource = VolumetricMeasurementSource::INACTIVE;
    unsigned long lastBluetoothMeasurement = 0;
    static const unsigned long BLUETOOTH_GRACE_PERIOD_MS = 1500; // 1.5 second grace period
    static const unsigned long CONTROLLER_WAITING_TIMEOUT_MS = 10000;

    xTaskHandle taskHandle;

    static void loopTask(void *arg);
};

#endif // CONTROLLER_H
