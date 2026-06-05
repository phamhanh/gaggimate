#include "Heater.h"
#include <Arduino.h>
#include <cmath>

Heater::Heater(TemperatureSensor *sensor, uint8_t heaterPin, const heater_error_callback_t &error_callback,
               const pid_result_callback_t &pid_callback,
               const heater_autotune_fail_callback_t &autotune_fail_callback)
    : sensor(sensor), heaterPin(heaterPin), taskHandle(nullptr), error_callback(error_callback), pid_callback(pid_callback),
      autotune_fail_callback(autotune_fail_callback) {

    simplePid = new SimplePID(&output, &temperature, &setpoint);
    autotuner = new Autotune();
}

void Heater::setup() {
    pinMode(heaterPin, OUTPUT);
    setupPid();
    xTaskCreate(loopTask, "Heater::loop", configMINIMAL_STACK_SIZE * 4, this, 1, &taskHandle);
}

void Heater::setupPid() {
    simplePid->setSamplingFrequency(TUNER_OUTPUT_SPAN / 1000.0f);
    simplePid->setCtrlOutputLimits(0.0f, TUNER_OUTPUT_SPAN);
    simplePid->activateSetPointFilter(false);
    simplePid->activateFeedForward(false);
    // Derivative low-pass: alpha=0.1 means 10% new / 90% old each 1 s sample.
    // Strongly attenuates thermocouple sample-to-sample noise before Kd amplifies it.
    simplePid->setDerivativeFilterAlpha(0.1f);
    simplePid->reset();
}

void Heater::setupAutotune(int testTimeSec, int windowSize, int heaterWattage) {
    // SIMC rebuild: first BLE arg = test-duration seconds (see Autotune.h),
    // not legacy phase-margin aggressiveness. Slope threshold + confirmation
    // count owned by Autotune.h defaults (0.1 °C/s, 5 confirms) — quantisation
    // resilience. Wattage stashed for loopAutotune → combinedKff = 1000 / W.
    autotuner->setWindowsize(windowSize > 0 ? windowSize : 6);
    autotuner->setTimeOut(static_cast<float>(testTimeSec > 0 ? testTimeSec : 120));
    autotuneHeaterWattage = heaterWattage > 0 ? heaterWattage : 0;
    autotuner->reset();
}

void Heater::loop() {
    if (!sensor->isErrorState() && autotuning) {
        loopAutotune();
        return;
    }

    if (sensor->isErrorState() || setpoint <= 0.0f) {
        simplePid->setMode(SimplePID::Control::manual);
        digitalWrite(heaterPin, LOW);
        relayStatus = false;
        temperature = sensor->read();
        return;
    }
    simplePid->setMode(SimplePID::Control::automatic);

    loopPid();
}

void Heater::setSetpoint(float setpoint) {
    if (this->setpoint != setpoint) {
        this->setpoint = setpoint;
        ESP_LOGV(LOG_TAG, "Set setpoint %f°C", setpoint);
    }
}

void Heater::setTunings(float Kp, float Ki, float Kd) {
    if (simplePid->getKp() != Kp || simplePid->getKi() != Ki || simplePid->getKd() != Kd) {
        simplePid->setControllerPIDGains(Kp, Ki, Kd, 0.0f);
        simplePid->reset();
        ESP_LOGV(LOG_TAG, "Set tunings to Kp: %f, Ki: %f, Kd: %f", Kp, Ki, Kd);
    }
}

void Heater::setThermalFeedforward(float *pumpFlowPtr, float incomingWaterTemp, int *valveStatusPtr) {
    pumpFlowRate = pumpFlowPtr;
    valveStatus = valveStatusPtr;
    this->incomingWaterTemp = incomingWaterTemp;

    ESP_LOGI(LOG_TAG, "Thermal feedforward setup - incoming water temp: %.1f°C, valve tracking: %s", incomingWaterTemp,
             valveStatusPtr ? "enabled" : "disabled");
    ESP_LOGI(LOG_TAG, "Feedforward will be %s based on Kff value (currently %.3f)", combinedKff > 0.0f ? "ENABLED" : "DISABLED",
             combinedKff);
}

void Heater::setFeedforwardScale(float combinedKff) {
    this->combinedKff = combinedKff;
    ESP_LOGI(LOG_TAG, "Combined feedforward gain (Kff) set to: %.3f output units per watt", combinedKff);
}

void Heater::setPidFreezeGraceMs(uint32_t graceMs) { pidFreezeGraceMs = graceMs; }

void Heater::setPidFreezeEnabled(bool enabled) { pidFreezeEnabled = enabled; }

void Heater::setPidGraceEnabled(bool enabled) { pidGraceEnabled = enabled; }

void Heater::setKffEnabled(bool enabled) { kffEnabled = enabled; }

void Heater::setIncomingWaterTemp(float tempC) { incomingWaterTemp = tempC; }

void Heater::setZoneBands(const float belowC, const float aboveC) {
    if (simplePid) {
        simplePid->setZoneBands(belowC, aboveC);
    }
}

void Heater::setStabGains(const float Kp, const float Ki, const float Kd) {
    if (simplePid) {
        simplePid->setStabGains(Kp, Ki, Kd);
    }
}

void Heater::setCoolGains(const float Kp, const float Ki, const float Kd) {
    if (simplePid) {
        simplePid->setCoolGains(Kp, Ki, Kd);
    }
}

int Heater::getActiveZone() const { return simplePid ? simplePid->getActiveZone() : 0; }

float Heater::getActiveKi() const { return simplePid ? simplePid->getActiveKi() : 0.0f; }

void Heater::autotune(int testTimeSec, int windowSize, int heaterWattage) {
    setupAutotune(testTimeSec, windowSize, heaterWattage);
    autotuning = true;
}

void Heater::loopPid() {
    softPwm(TUNER_OUTPUT_SPAN);
    temperature = sensor->read();

    const bool valveOpen = pumpFlowRate && valveStatus && *valveStatus != 0;
    const bool pumpRunning = pumpFlowRate && *pumpFlowRate > 0.01f;
    const bool waterFlowing = valveOpen || pumpRunning; // covers steam/hotwater where brew valve stays closed

    const bool kffWouldApply = kffEnabled && combinedKff > 0.0f && waterFlowing && pumpRunning;

    if (!pidFreezeEnabled) {
        if (freezeLatched || pidFreezeGraceUntil != 0) {
            freezeLatched = false;
            pidFreezeGraceUntil = 0;
            freezeBlocked = false;
        }
        wasValveOpen = waterFlowing;
        simplePid->setPidFrozen(false);
    } else {
        if (waterFlowing && !freezeLatched && !freezeBlocked) {
            simplePid->captureFrozenFeedback();
            freezeLatched = true;
            const float flow = pumpFlowRate ? *pumpFlowRate : 0.0f;
            ESP_LOGI(LOG_TAG,
                     "PID freeze latched: T=%.2f SP=%.2f flow=%.2f ml/s frozenPid=%.2f kffEn=%d combinedKff=%.3f",
                     temperature, setpoint, flow, simplePid->getFrozenPidSum(), kffEnabled ? 1 : 0, combinedKff);
        } else if (!waterFlowing && wasValveOpen) {
            if (pidGraceEnabled && pidFreezeGraceMs > 0) {
                pidFreezeGraceUntil = millis() + pidFreezeGraceMs;
                ESP_LOGI(LOG_TAG,
                         "PID freeze grace started: duration=%lu ms T=%.2f output=%.2f frozenPid=%.2f",
                         static_cast<unsigned long>(pidFreezeGraceMs), temperature, output,
                         simplePid->getFrozenPidSum());
            } else {
                freezeLatched = false;
                simplePid->setPidFrozen(false);
                pidFreezeGraceUntil = 0;
                freezeBlocked = false;
                ESP_LOGI(LOG_TAG, "PID freeze ended (no grace): T=%.2f output=%.2f", temperature, output);
            }
        }
        wasValveOpen = waterFlowing;

        if (freezeLatched && pidFreezeGraceUntil != 0 && millis() >= pidFreezeGraceUntil) {
            freezeLatched = false;
            simplePid->setPidFrozen(false);
            pidFreezeGraceUntil = 0;
            freezeBlocked = false;
            ESP_LOGI(LOG_TAG, "PID freeze grace ended (timer): T=%.2f output=%.2f", temperature, output);
        }

        simplePid->setPidFrozen(freezeLatched);
    }

    if (kffWouldApply) {
        lastKffGainPerFlow = calculateDisturbanceFeedforwardGain();
        lastKffOutput = lastKffGainPerFlow * *pumpFlowRate;
        simplePid->setDisturbanceFeedforward(*pumpFlowRate, lastKffGainPerFlow);
    } else {
        lastKffGainPerFlow = 0.0f;
        lastKffOutput = 0.0f;
        simplePid->setDisturbanceFeedforward(0.0f, 0.0f);
    }

    bool pidUpdated = simplePid->update();

    if (pidUpdated) {
        plot(output, 1.0f, 1);
    }
}

void Heater::loopAutotune() {
    simplePid->setMode(SimplePID::Control::manual);
    autotuner->reset();
    long microseconds;
    long loopInterval = (static_cast<long>(TUNER_OUTPUT_SPAN) - 1L) * 1000L;
    while (!autotuner->isFinished()) {
        microseconds = micros();
        // Re-check sensor every iteration. Heater::loop entry gate sampled
        // once — mid-test fault would leave relay stuck at full power for
        // rest of window. Max31855Thermocouple::read() returns 0 on fault;
        // neither overtemp guard nor autotuner state machine detects it.
        if (sensor->isErrorState()) {
            output = 0.0f;
            autotuning = false;
            softPwm(TUNER_OUTPUT_SPAN);
            ESP_LOGE(LOG_TAG, "Autotune aborted: sensor fault mid-test");
            if (error_callback) {
                error_callback();
            }
            return;
        }
        temperature = sensor->read();
        output = 0.0f;
        if (autotuner->maxPowerOn) {
            output = TUNER_OUTPUT_SPAN;
        }
        ESP_LOGI(LOG_TAG, "Autotuner Cycle: Temperature=%.2f", temperature);
        autotuner->update(temperature, millis() / 1000.0f);
        while (micros() - microseconds < loopInterval) {
            softPwm(TUNER_OUTPUT_SPAN);
            vTaskDelay(1 / portTICK_PERIOD_MS);
        }
        if (temperature > MAX_AUTOTUNE_TEMP) {
            output = 0.0f;
            autotuning = false;
            softPwm(TUNER_OUTPUT_SPAN);
            // Overtemp abort. Preserve NVS gains — skip pid_callback. Surface
            // as runaway so display drops machine into standby.
            ESP_LOGE(LOG_TAG, "Autotune aborted: temperature %.1f°C exceeds %.1f°C", temperature, MAX_AUTOTUNE_TEMP);
            if (error_callback) {
                error_callback();
            }
            return;
        }
    }
    output = 0.0f;
    autotuning = false;
    softPwm(TUNER_OUTPUT_SPAN);

    if (autotuner->isTimedOut()) {
        // Reaction/inflection never detected in window. Keep NVS PID — never
        // call pid_callback with zeros (#672 crash). Use dedicated
        // autotune-fail callback (→ ERROR_CODE_AUTOTUNE_TIMEOUT) so display
        // doesn't mistake it for runaway + force pump/valve off.
        // getTimeOut() = configured window. getSystemDelay() only set on
        // success path — reads 0 here.
        ESP_LOGW(LOG_TAG, "Autotune timed out (no inflection within %.1f s) — gains preserved",
                 autotuner->getTimeOut());
        if (autotune_fail_callback) {
            autotune_fail_callback();
        }
        return;
    }

    // Disturbance feedforward (output units per watt of heat loss). With
    // TUNER_OUTPUT_SPAN=1000 and a known heater wattage, "1 watt of
    // compensation maps to (1000 / wattage) PWM duty units". 0 when wattage
    // wasn't supplied — preserves prior no-disturbance-FF behaviour.
    // Variable name avoids shadowing the Heater member combinedKff (Sonar
    // cpp:S1117).
    const float kffFromWattage = autotuneHeaterWattage > 0
                                     ? TUNER_OUTPUT_SPAN / static_cast<float>(autotuneHeaterWattage)
                                     : 0.0f;

    pid_callback(autotuner->getKp() * 1000.0f, autotuner->getKi() * 1000.0f, autotuner->getKd() * 1000.0f, kffFromWattage);

    setTunings(autotuner->getKp() * 1000.0f, autotuner->getKi() * 1000.0f, autotuner->getKd() * 1000.0f);
    setFeedforwardScale(kffFromWattage);

    ESP_LOGI(LOG_TAG, "Autotuning finished: Kp=%.4f, Ki=%.4f, Kd=%.4f, Kff=%.4f", autotuner->getKp() * 1000.0f,
             autotuner->getKi() * 1000.0f, autotuner->getKd() * 1000.0f, kffFromWattage);
    // Setpoint-FF (SIMC's 1/k') logged for diagnostics — currently unrouted
    // (SimplePID gainFF stays 0); useful for sanity-checking the identifier
    // without affecting the runtime FF path that uses kffFromWattage above.
    ESP_LOGI(LOG_TAG, "SIMC params: L=%.2fs k'=%.4f°C/s tau2=%.2fs fc=%.4fHz wattage=%dW setpoint_FF=%.2f",
             autotuner->getSystemDelay(), autotuner->getSystemGain(), autotuner->getSystemTau2(),
             autotuner->getCrossoverFreq(), autotuneHeaterWattage, autotuner->getKff() * 1000.0f);
}

float Heater::softPwm(uint32_t windowSize) {
    // software PWM timer
    unsigned long msNow = millis();
    if (msNow - windowStartTime >= windowSize) {
        windowStartTime = msNow;
    }
    float optimumOutput = output;

    // PWM relay output
    if (!relayStatus && static_cast<unsigned long>(optimumOutput) > (msNow - windowStartTime)) {
        if (msNow > nextSwitchTime) {
            nextSwitchTime = msNow;
            relayStatus = true;
            digitalWrite(heaterPin, HIGH);
        }
    } else if (relayStatus && static_cast<unsigned long>(optimumOutput) < (msNow - windowStartTime)) {
        if (msNow > nextSwitchTime) {
            nextSwitchTime = msNow;
            relayStatus = false;
            digitalWrite(heaterPin, LOW);
        }
    }
    return optimumOutput;
}

void Heater::plot(float optimumOutput, float outputScale, uint8_t everyNth) {
    if (plotCount >= everyNth) {
        plotCount = 1;
        const float flow = pumpFlowRate ? *pumpFlowRate : 0.0f;
        unsigned long graceLeftMs = 0;
        if (pidFreezeGraceUntil != 0 && millis() < pidFreezeGraceUntil) {
            graceLeftMs = pidFreezeGraceUntil - millis();
        }
        ESP_LOGI(LOG_TAG,
                 "PID Plot: output=%.2f input=%.2f setpoint=%.2f frozen=%d frozenPid=%.2f kffOut=%.2f "
                 "flow=%.2f gainPerFlow=%.3f graceLeftMs=%lu",
                 optimumOutput * outputScale, temperature, setpoint, freezeLatched ? 1 : 0,
                 simplePid->getFrozenPidSum(), lastKffOutput, flow, lastKffGainPerFlow,
                 graceLeftMs);
    } else
        plotCount++;
}

float Heater::calculateDisturbanceFeedforwardGain() {
    if (combinedKff <= 0.0f || !pumpFlowRate || *pumpFlowRate <= 0.01f)
        return 0.0f;

    // Calculate temperature difference (target - incoming water temperature)
    float tempDelta = setpoint - incomingWaterTemp;
    if (tempDelta <= 0.0f)
        return 0.0f;

    // Calculate thermal power needed per ml/s of flow (Watts per ml/s)
    float powerPerFlowRate = WATER_DENSITY * WATER_SPECIFIC_HEAT * tempDelta + (heatLossWatts / *pumpFlowRate);
    powerPerFlowRate /= heaterEfficiency;

    // Apply combined Kff directly (output units per watt)
    float gainPerFlowRate = powerPerFlowRate * combinedKff;

    return gainPerFlowRate;
}

void Heater::loopTask(void *arg) {
    TickType_t lastWake = xTaskGetTickCount();
    auto *heater = static_cast<Heater *>(arg);
    while (true) {
        heater->loop();
        xTaskDelayUntil(&lastWake, pdMS_TO_TICKS(10));
    }
}
