#ifndef HEATER_H
#define HEATER_H
#include "Autotune/Autotune.h"
#include "Max31855Thermocouple.h"
#include "TemperatureSensor.h"
#include <SimplePID/SimplePID.h>
#include <freertos/FreeRTOS.h>
#include <freertos/task.h>

enum class PIDLibrary { Legacy, Nimrod };

constexpr float MAX_AUTOTUNE_TEMP = 125.0f;
constexpr float TUNER_OUTPUT_SPAN = 1000.0f;
// Setpoint at/above which the boiler is making steam. Brew and hot-water
// setpoints never exceed ~100°C, while steam runs ~130–160°C, so this cleanly
// separates the two regimes. Used to inhibit the brew-oriented PID freeze
// during steaming (see Heater::loopPid).
constexpr float STEAM_FREEZE_INHIBIT_TEMP = 110.0f;

using heater_error_callback_t = std::function<void()>;
using heater_autotune_fail_callback_t = std::function<void()>;
// Kff = combinedKff = output units per watt (disturbance feedforward), derived
// in Heater::loopAutotune as 1000 / heaterWattage when wattage > 0; 0 when the
// caller didn't supply a wattage.
using pid_result_callback_t = std::function<void(float Kp, float Ki, float Kd, float Kff)>;

class Heater {
  public:
    Heater(TemperatureSensor *sensor, uint8_t heaterPin, const heater_error_callback_t &error_callback,
           const pid_result_callback_t &pid_callback,
           const heater_autotune_fail_callback_t &autotune_fail_callback = nullptr);
    void setup();
    void loop();

    void setSetpoint(float setpoint);
    float getSetpoint() { return setpoint; };
    /** Heater duty command 0–1000 (matches PID `output` / soft-PWM window). */
    float getOutput() const { return output; }
    float getPidP() const { return simplePid ? simplePid->getLastP() : 0.0f; }
    float getPidI() const { return simplePid ? simplePid->getLastI() : 0.0f; }
    float getPidD() const { return simplePid ? simplePid->getLastD() : 0.0f; }
    /** Raw integrator state (NOT Ki*state); stays informative while Ki=0. */
    float getPidIntegralState() const { return simplePid ? simplePid->getIntegralState() : 0.0f; }
    float getKffOutput() const { return lastKffOutput; }
    bool isPidFrozenLatched() const { return freezeLatched; }
    void setTunings(float Kp, float Ki, float Kd);
    void autotune(int testTimeSec, int windowSize, int heaterWattage);

    // Thermal feedforward control
    void setThermalFeedforward(float *pumpFlowPtr = nullptr, float incomingWaterTemp = 23.0f, int *valveStatusPtr = nullptr);
    void setFeedforwardScale(float combinedKff); // Set combined Kff value (output units per watt)
    void setPidFreezeGraceMs(uint32_t graceMs);
    void setPidFreezeEnabled(bool enabled);
    void setPidGraceEnabled(bool enabled);
    void setKffEnabled(bool enabled);
    void setIncomingWaterTemp(float tempC);
    // 3-zone idle PID configuration (heating gains come via setTunings).
    void setZoneBands(float belowC, float aboveC);
    void setStabGains(float Kp, float Ki, float Kd);
    void setCoolGains(float Kp, float Ki, float Kd);
    int getActiveZone() const;
    float getActiveKi() const;

  private:
    void setupPid();
    void setupAutotune(int testTimeSec, int windowSize, int heaterWattage);
    /** PID + thermal FF; latched P+I+D from first valve open until post-shot grace ends. */
    void loopPid();
    void loopAutotune();
    /** Centralised freeze-exit: restore the latched I into the live integrator
     *  (bumpless) then clear the freeze flags. Use from every unfreeze path. */
    void releasePidFreeze(const char *reason);
    float softPwm(uint32_t windowSize);
    void plot(float optimumOutput, float outputScale, uint8_t everyNth);
    float calculateDisturbanceFeedforwardGain();
    TemperatureSensor *sensor;
    uint8_t heaterPin;
    xTaskHandle taskHandle;
    SimplePID *simplePid = nullptr;
    Autotune *autotuner = nullptr;

    heater_error_callback_t error_callback;
    pid_result_callback_t pid_callback;
    heater_autotune_fail_callback_t autotune_fail_callback;

    float temperature = 0.0f;
    float output = 0.0f;
    float setpoint = 0.0f;
    float Kp = 2.4;
    float Ki = 40;
    float Kd = 10;
    int plotCount = 0;

    bool relayStatus = false;
    unsigned long windowStartTime = 0;
    unsigned long nextSwitchTime = 0;

    // Autotune variables
    bool startup = true;
    bool autotuning = false;
    // Stashed at autotune-start (BLE field 3) so loopAutotune can derive
    // combinedKff = 1000 / wattage on completion. 0 ⇒ caller didn't supply
    // wattage (older display firmware) ⇒ skip combinedKff derivation.
    int autotuneHeaterWattage = 0;

    // Thermal feedforward variables
    float *pumpFlowRate = nullptr;
    int *valveStatus = nullptr;
    float incomingWaterTemp = 23.0f;
    float heaterEfficiency = 0.95f; // 95% efficiency (immersion heater)
    float heatLossWatts = 5.0f;     // 5W heat loss (well-insulated boiler)
    float combinedKff = 0.0f;       // Combined feedforward gain (output units per watt) - disabled by default
    uint32_t pidFreezeGraceMs = 60000;
    bool pidFreezeEnabled = true;
    bool pidGraceEnabled = true;
    bool kffEnabled = true;
    unsigned long pidFreezeGraceUntil = 0;
    bool freezeLatched = false;
    bool freezeBlocked = false;
    bool wasValveOpen = false;
    float lastKffOutput = 0.0f;
    float lastKffGainPerFlow = 0.0f;

    // Thermal model constants
    static constexpr float WATER_DENSITY = 1.0f;        // g/ml
    static constexpr float WATER_SPECIFIC_HEAT = 4.18f; // J/(g·°C)

    const char *LOG_TAG = "Heater";
    static void loopTask(void *arg);
};

#endif // HEATER_H
