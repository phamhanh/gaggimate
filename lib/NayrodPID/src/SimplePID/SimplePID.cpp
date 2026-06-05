#include "SimplePID.h"
#include <Arduino.h>
#include <algorithm>
#include <cmath>
#include <cstdio>
#include <cstring>
#include <numeric>

SimplePID::SimplePID(float *controlerOutputPtr, float *sensorOutputPtr, float *setpointTargetPtr) {
    this->controlerOutput = controlerOutputPtr;
    this->sensorOutput = sensorOutputPtr;
    this->setpointTarget = setpointTargetPtr;
}

bool SimplePID::update() {
    if (mode == Control::manual) {
        return false;
    }
    uint32_t now = millis();
    uint32_t timeChange = (now - lastTime);
    if (timeChange < ctrl_freq_sampling * 1000) {
        return false;
    }
    lastTime = now;

    if (!isInitialized) {
        initSetPointFilter(*this->sensorOutput);
        // Preserve an active freeze latch across a re-init: resetFeedbackController()
        // zeros both the accumulator and the latched I sum, which would silently
        // discard the steady-state bias we are meant to hold through flow/grace.
        const bool keepFrozen = pidFrozen && frozenPidSum > 0.0f;
        const float savedFrozenPidSum = frozenPidSum;
        const float savedActiveKi = lastActiveKi;
        resetFeedbackController();
        if (keepFrozen) {
            frozenPidSum = savedFrozenPidSum;
            lastActiveKi = savedActiveKi;
            pidFrozen = true;
            restoreIntegralFromFrozenSum();
        }
        prevMeasurement = *sensorOutput; // Seed with current value to avoid derivative kick on first update
        if (gainFF != 0.0f)
            isFeedForwardActive = true; // Activate the feedforward control if gainFF is not zero
        isInitialized = true;
    }

    // Compute the filtered setpoint values
    float FFOut = 0.0f;
    float DistFFOut = 0.0f;

    if (isfilterSetpointActive) {
        setpointFiltering(setpointFilterFreq);
    } else {
        setpointFiltered = *setpointTarget;
    } // If the filter is not active, use the setpoint directly

    if (isFeedForwardActive)
        FFOut = setpointDerivative * gainFF;

    if (isDisturbanceFeedForwardActive)
        DistFFOut = currentDisturbance * gainDistFF;

    ESP_LOGV("SimplePID", "%.2f\t %.2f\t %.2f\t %.2f\n", *setpointTarget, setpointFiltered, setpointDerivative, *sensorOutput);

    lastDistFFOut = DistFFOut;

    if (pidFrozen) {
        lastPout = 0.0f;
        lastIout = frozenPidSum;
        lastDout = 0.0f;
        *controlerOutput = constrain(frozenPidSum + DistFFOut, ctrlOutputLimits[0], ctrlOutputLimits[1]);
        return true;
    }

    float deltaTime = 1.0f / ctrl_freq_sampling; // Time step in seconds

    float error = setpointFiltered - *sensorOutput;

    // --- 3-zone gain scheduling ---------------------------------------------
    // Pick the zone from the actual measurement vs the (unfiltered) target and
    // the configured bands, with hysteresis so we don't chatter at the edges.
    activeZone = selectZone(*sensorOutput, *setpointTarget, activeZone);

    float kp = gainKp;
    float ki = gainKi;
    float kd = gainKd;
    switch (activeZone) {
    case PidZone::stabilizing:
        kp = gainKpStab;
        ki = gainKiStab;
        kd = gainKdStab;
        break;
    case PidZone::cooling:
        kp = gainKpCool;
        ki = gainKiCool;
        kd = gainKdCool;
        break;
    case PidZone::heating:
    default:
        break;
    }

    // Bumpless Ki transition: rescale the shared integrator so I = ki * state is
    // continuous across a zone cross or gain edit. Never reset the integrator.
    if (ki != lastActiveKi && lastActiveKi > 0.0f && ki > 0.0f) {
        feedback_integralState *= lastActiveKi / ki;
    }
    lastActiveKi = ki;

    const bool coolingZone = (activeZone == PidZone::cooling);

    float Pout = kp * error;

    feedback_integralState += error * deltaTime;
    // Heat-only actuator (min output 0): in heating/stabilizing the integral is a
    // non-negative steady-state heating bias. In cooling we let it unwind (go
    // negative) so stored-heat overshoot can bleed off — clamp is zone-aware.
    if (!coolingZone) {
        feedback_integralState = fmaxf(0.0f, feedback_integralState);
    }
    float Iout = ki * feedback_integralState;

    // Derivative-on-measurement: avoids derivative kick on setpoint changes.
    // Low-pass filter applied via EMA to attenuate sensor noise before Kd amplifies it.
    float rawDerivative = -(*sensorOutput - prevMeasurement) / deltaTime;
    filteredDerivative = derivFilterAlpha * rawDerivative + (1.0f - derivFilterAlpha) * filteredDerivative;
    float Dout = kd * filteredDerivative;

    // Calculate the output before antiwindup clamping
    float sumPID = Pout + Iout + Dout + FFOut + DistFFOut;
    float sumPIDsat = constrain(sumPID, ctrlOutputLimits[0], ctrlOutputLimits[1]);

    // Antiwindup clamping (runs in all zones)
    bool isSaturated = (sumPID < ctrlOutputLimits[0] || sumPID > ctrlOutputLimits[1]); // Check if the output is saturated
    bool isSameSign =
        ((error > 0 && sumPID > 0) || (error < 0 && sumPID < 0)); // Check if the error and output have the same sign
    // Serial.printf("OutputPID: %.2f, Integ out: %.2f\n", sumPIDsat, Iout);
    if (isSaturated && isSameSign) {
        // Serial.printf("Antiwindup clamping: %.2f\n", feedback_integralState);
        feedback_integralState -=
            error * deltaTime; // Forbide the integration to happen when the output is saturated and the error is in the same
                               // direction as the output (i.e. the system is not able to follow the setpoint)
        if (!coolingZone) {
            feedback_integralState = fmaxf(0.0f, feedback_integralState);
        }
        Iout = ki * feedback_integralState;              // Recompute the integral term with the new state
        sumPID = Pout + Iout + Dout + FFOut + DistFFOut; // Recompute the output with the new integral state
        sumPIDsat = constrain(sumPID, ctrlOutputLimits[0], ctrlOutputLimits[1]);
    }

    // Serial.printf("Pout: %.2f, Iout: %.2f, Dout: %.2f, FFOut: %.2f, OutputPID: %.2f, SumPID: %.2f\n", Pout, Iout, Dout, FFOut,
    // sumPIDsat, sumPID); Update previous values for next iteration
    prevError = error;
    prevMeasurement = *sensorOutput;
    prevOutput = sumPIDsat;

    lastPout = Pout;
    lastIout = Iout;
    lastDout = Dout;
    *controlerOutput = sumPIDsat;

    return true;
}

void SimplePID::setpointFiltering(float freq) {

    float wn = (2.0f * PI * freq);
    float dderiv = wn * wn * (*setpointTarget - setpointFilteredValues.back());
    setpointFiltstate1 += dderiv / ctrl_freq_sampling;
    setpointDerivative = setpointFiltstate1 - wn * 2 * setpointFiltXi * setpointFilteredValues.back();
    // Output the filtered setpoint values
    setpointDerivative = constrain(setpointDerivative, setpointRatelimits[0], setpointRatelimits[1]);
    // Integrate (forward euler) the setpoint derivative to get the filtered setpoint value
    float integ = setpointFilteredValues.back() + setpointDerivative / ctrl_freq_sampling;
    // Add the new setpoint to the history to introduce a delay between the setpoint derivative and the filtered setpoint
    setpointFilteredValues.push_back(integ);
    if (setpointFilteredValues.size() > setpointDelaySamples + 1) {
        setpointFilteredValues.pop_front();
    }
    setpointFiltered = setpointFilteredValues.front(); // Get the filtered setpoint value
}

void SimplePID::initSetPointFilter(float initialValue) {
    setpointFilteredValues.clear();
    for (int i = 0; i < setpointDelaySamples + 1; ++i) {
        setpointFilteredValues.push_back(initialValue);
    }
    setpointFiltstate1 = 2 * setpointFiltXi * 2 * PI * setpointFilterFreq * initialValue;
}

void SimplePID::captureFrozenFeedback() {
    if (isfilterSetpointActive) {
        setpointFiltering(setpointFilterFreq);
    } else {
        setpointFiltered = *setpointTarget;
    }

    // Freeze only the I term: it represents true steady-state power at this setpoint/environment.
    // P and D are transient corrections that depend on the exact moment flow starts — locking them
    // in produces arbitrary shot-to-shot variation. Kff handles the dynamic flow disturbance.
    // Use the active zone's Ki (latched on the last tick); fall back to heating Ki if never run.
    const float kiForFreeze = lastActiveKi > 0.0f ? lastActiveKi : gainKi;
    frozenPidSum = kiForFreeze * feedback_integralState;
}

void SimplePID::restoreIntegralFromFrozenSum() {
    // Bumpless handoff out of freeze: the latch (frozenPidSum) held the I output
    // during flow/grace while feedback_integralState was not advanced (and may
    // have been zeroed by a re-init). Re-seed the accumulator so the first
    // unfrozen tick reports the same I instead of dropping to 0.
    //
    // Mirror the Ki rule used by captureFrozenFeedback() so state * ki == the
    // latched sum: prefer the last active zone's Ki, fall back to heating Ki.
    const float kiForRestore = lastActiveKi > 0.0f ? lastActiveKi : gainKi;
    if (frozenPidSum > 0.0f && kiForRestore > 0.0f) {
        feedback_integralState = frozenPidSum / kiForRestore;
        // Pin lastActiveKi so the bumpless-transition rescale in update() does
        // not immediately re-scale the accumulator we just restored.
        lastActiveKi = kiForRestore;
    }
    // Refresh derivative state against the current measurement so the long
    // freeze gap doesn't produce a one-tick derivative spike on resume.
    if (sensorOutput != nullptr) {
        prevMeasurement = *sensorOutput;
    }
    filteredDerivative = 0.0f;
}

void SimplePID::resetFeedbackController() {
    feedback_integralState = 0.0f; // Reset the integral state
    prevError = 0.0f;              // Reset the previous error for derivative calculation
    prevMeasurement = 0.0f;        // Reset the previous measurement for derivative-on-measurement
    filteredDerivative = 0.0f;     // Reset the derivative filter state
    prevOutput = 0.0f;             // Reset the previous output for derivative calculation
    pidFrozen = false;
    frozenPidSum = 0.0f;
    activeZone = PidZone::heating; // Re-arm zone scheduler
    lastActiveKi = 0.0f;           // No prior Ki ⇒ first tick won't rescale integrator
}

// Zone selection with symmetric hysteresis on both band edges so we don't
// chatter when CT sits right at a boundary. Bands are relative to the (unfiltered)
// target TT: heating below TT-bandBelow, cooling above TT+bandAbove, stabilizing
// in between.
SimplePID::PidZone SimplePID::selectZone(float ct, float tt, PidZone current) const {
    const float lowEdge = tt - bandBelowC;
    const float highEdge = tt + bandAboveC;
    const float H = ZONE_HYSTERESIS_C;
    switch (current) {
    case PidZone::heating:
        if (ct >= lowEdge + H) {
            return (ct > highEdge + H) ? PidZone::cooling : PidZone::stabilizing;
        }
        return PidZone::heating;
    case PidZone::cooling:
        if (ct <= highEdge - H) {
            return (ct < lowEdge - H) ? PidZone::heating : PidZone::stabilizing;
        }
        return PidZone::cooling;
    case PidZone::stabilizing:
    default:
        if (ct < lowEdge - H) {
            return PidZone::heating;
        }
        if (ct > highEdge + H) {
            return PidZone::cooling;
        }
        return PidZone::stabilizing;
    }
}

void SimplePID::reset() {
    resetFeedbackController();
    isInitialized = false;
    setpointFilteredValues.clear();
    setpointFiltstate1 = 0.0f;
}

// GETTER-SETTER FUNCTIONS
// Setpoint
void SimplePID::setSetpointRateLimits(float minRate, float maxRate) {
    setpointRatelimits[0] = minRate;
    setpointRatelimits[1] = maxRate;
}

void SimplePID::setSetpointDelaySamples(int delaySamples) { setpointDelaySamples = delaySamples; }
void SimplePID::activateSetPointFilter(bool flag) { isfilterSetpointActive = flag; }
void SimplePID::setSetpointFilterFrequency(float freq) { setpointFilterFreq = freq; }

// Feedback controller
void SimplePID::setControllerPIDGains(float Kp, float Ki, float Kd, float FF) {
    this->gainKp = Kp;
    this->gainKi = Ki;
    this->gainFF = FF;
    this->gainKd = Kd;
}

void SimplePID::setZoneBands(float belowC, float aboveC) {
    this->bandBelowC = belowC;
    this->bandAboveC = aboveC;
}

void SimplePID::setStabGains(float Kp, float Ki, float Kd) {
    this->gainKpStab = Kp;
    this->gainKiStab = Ki;
    this->gainKdStab = Kd;
}

void SimplePID::setCoolGains(float Kp, float Ki, float Kd) {
    this->gainKpCool = Kp;
    this->gainKiCool = Ki;
    this->gainKdCool = Kd;
}

void SimplePID::setSamplingFrequency(float freq) { ctrl_freq_sampling = freq; }
void SimplePID::setCtrlOutputLimits(float minOutput, float maxOutput) {
    ctrlOutputLimits[0] = minOutput;
    ctrlOutputLimits[1] = maxOutput;
}

void SimplePID::setMode(Control modeCMD) {
    if (modeCMD == Control::automatic && this->mode == Control::manual) {
        isInitialized = false; // Reset the controller when switching to automatic mode
    }
    this->mode = modeCMD;
}

void SimplePID::setManualOutput(float output) {
    if (this->mode == Control::automatic)
        setMode(Control::manual);
    manualOutput = output;
}

void SimplePID::computeSetpointDelay(float systemDelay) {
    // systemDelay : (s) system pure delay
    float setpointFilterDelay = 1 / (2 * PI * setpointFilterFreq); // Setpoint filter delay in seconds
    float totalDelay =
        systemDelay -
        setpointFilterDelay; // Delay to apply to synchronise the setpoint with the stepoint derivative for the feedforward term
    if (totalDelay < 0.0f) {
        totalDelay = 0.0f; // Set the delay to 0 if it is negative
    }
    setpointDelaySamples = static_cast<uint32_t>(totalDelay * ctrl_freq_sampling); // Convert to number of samples
}

void SimplePID::activateFeedForward(bool flag) {
    if (gainFF == 0.0f) {
        // ERROR : feedforward gain is not activated
        isFeedForwardActive = false;
        // throw std::invalid_argument("Feedforward gain is 0.0, must be set to a non zero value.");
    } else {
        isFeedForwardActive = flag;
    }
}

void SimplePID::setDisturbanceFeedforward(float disturbance, float gainDFF) {
    currentDisturbance = disturbance;
    gainDistFF = gainDFF;
    isDisturbanceFeedForwardActive = (gainDFF != 0.0f);
}
