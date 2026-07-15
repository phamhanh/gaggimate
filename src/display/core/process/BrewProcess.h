#ifndef BREWPROCESS_H
#define BREWPROCESS_H

#include <algorithm>
#include <display/core/constants.h>
#include <display/core/predictive.h>
#include <display/core/process/Process.h>
#include <display/models/profile.h>

class BrewProcess : public Process {
  public:
    Profile profile;
    ProcessTarget target;
    double brewDelay;
    unsigned int phaseIndex = 0;
    Phase currentPhase;
    ProcessPhase processPhase = ProcessPhase::RUNNING;
    unsigned long processStarted = 0;
    unsigned long currentPhaseStarted = 0;
    unsigned long previousPhaseFinished = 0;
    unsigned long finished = 0;
    double currentVolume = 0; // most recent volume pushed
    float currentFlow = 0.0f;
    float currentPuckFlow = 0.0f;
    float currentCupFlow = 0.0f;
    bool cupFlowLive = false;
    float currentPressure = 0.0f;
    float waterPumped = 0.0f;
    VolumetricRateCalculator volumetricRateCalculator{PREDICTIVE_TIME};

    explicit BrewProcess(Profile profile, ProcessTarget target, double brewDelay = 0.0)
        : profile(profile), target(target), brewDelay(brewDelay) {
        currentPhase = profile.phases.at(phaseIndex);
        processStarted = millis();
        currentPhaseStarted = millis();
        phaseStartPressure = currentPhase.transition.adaptive ? currentPressure : 0;
        phaseStartFlow = currentPhase.transition.adaptive ? currentFlow : 0;
        computeEffectiveTargetsForCurrentPhase();
    }

    void updateVolume(double volume) override { // called even after the Process is no longer active
        currentVolume = volume;
        if (processPhase != ProcessPhase::FINISHED) { // only store measurements while active
            volumetricRateCalculator.addMeasurement(volume);
        }
    }

    void updatePressure(float pressure) { currentPressure = pressure; }

    void updateFlow(float flow) { currentFlow = flow; }

    void updatePuckFlow(float flow) { currentPuckFlow = flow; }

    // Feed the scale-derived cup flow and run the outer trim loop: when the
    // current phase has a cup-flow setpoint and the scale is live, integrate
    // the error into a (non-positive) trim on the pump-flow command. The
    // pump-flow field stays the ceiling; losing the scale mid-phase freezes
    // control back to the plain pump-flow target (trim decays to 0).
    void updateCupFlow(float flow, bool live) {
        currentCupFlow = flow;
        cupFlowLive = live;
        const unsigned long now = millis();
        if (lastCupTrimUpdate == 0) {
            lastCupTrimUpdate = now;
            return;
        }
        const float dt = static_cast<float>(now - lastCupTrimUpdate) / 1000.0f;
        lastCupTrimUpdate = now;
        if (processPhase != ProcessPhase::RUNNING || currentPhase.pumpIsSimple) {
            cupFlowTrim = 0.0f;
            return;
        }
        const float setpoint = currentPhase.pumpAdvanced.cupFlow;
        if (setpoint <= 0.0f || !live) {
            // Decay any leftover trim so a scale drop degrades smoothly to the
            // plain pump-flow target instead of stepping.
            cupFlowTrim *= std::max(0.0f, 1.0f - 2.0f * dt);
            return;
        }
        const float error = setpoint - flow; // g/s; negative = cup running too fast
        cupFlowTrim += CUP_FLOW_TRIM_GAIN * error * dt;
        // Trim may only reduce the command below the ceiling, never above it,
        // and must leave a minimum command so the pump keeps metering.
        const float ceiling = getPumpFlowCeiling();
        const float maxReduction = std::max(0.0f, ceiling - CUP_FLOW_MIN_COMMAND);
        cupFlowTrim = std::clamp(cupFlowTrim, -maxReduction, 0.0f);
    }

    unsigned long getTotalDuration() const { return profile.getTotalDuration() * 1000L; }

    unsigned long getPhaseDuration() const { return static_cast<long>(currentPhase.duration) * 1000L; }

    bool isCurrentPhaseFinished() {
        if (millis() - currentPhaseStarted > BREW_SAFETY_DURATION_MS) {
            return true;
        }
        double predicted_volume = currentVolume;
        if (currentVolume > 0.0) {
            double currentRate = volumetricRateCalculator.getRate();
            double predictedAddedVolume = currentRate * brewDelay;
            predictedAddedVolume = std::clamp(predictedAddedVolume, 0.0, 8.0);
            predicted_volume = currentVolume + predictedAddedVolume;
        }
        float timeInPhase = static_cast<float>(millis() - currentPhaseStarted) / 1000.0f;
        return currentPhase.isFinished(target == ProcessTarget::VOLUMETRIC, currentVolume, predicted_volume, timeInPhase,
                                       currentFlow, currentPressure, waterPumped, profile.type, currentPuckFlow,
                                       currentCupFlow, cupFlowLive);
    }

    bool isUtility() const { return profile.utility; }

    double getBrewVolume() const {
        double brewVolume = 0;
        for (const auto &phase : profile.phases) {
            if (phase.hasPredictedWeightTarget()) {
                Target target = phase.getPredictedWeightTarget();
                brewVolume = target.value;
            }
        }
        return brewVolume;
    }

    double getNewDelayTime() {
        double newDelay = brewDelay + volumetricRateCalculator.getOvershootAdjustMillis(getBrewVolume(), currentVolume);
        if (newDelay <= 0.0 || newDelay >= PREDICTIVE_TIME) {
            return -1;
        }
        return newDelay;
    }

    bool isRelayActive() override {
        if (processPhase == ProcessPhase::FINISHED) {
            return false;
        }
        return currentPhase.valve;
    }

    bool isAltRelayActive() override { return false; }

    float getPumpValue() override {
        if (processPhase == ProcessPhase::FINISHED) {
            return 0.0f;
        }
        return currentPhase.pumpIsSimple ? currentPhase.pumpSimple : 100.0f;
    }

    bool isAdvancedPump() const { return processPhase != ProcessPhase::FINISHED && !currentPhase.pumpIsSimple; }

    [[nodiscard]] PumpTarget getPumpTarget() const { return currentPhase.pumpAdvanced.target; }

    float getPumpPressure() const {
        if (!isAdvancedPump())
            return 0.0f;
        const float startVal = phaseStartPressure;
        const float endVal = effectivePressure;
        const float a = transitionAlpha();
        return startVal + (endVal - startVal) * a;
    }

    // Transitioned pump-flow command before cup-flow trim — acts as the
    // ceiling while cup-flow control is active.
    float getPumpFlowCeiling() const {
        if (!isAdvancedPump())
            return 0.0f;
        const float startVal = phaseStartFlow;
        const float endVal = effectiveFlow;
        const float a = transitionAlpha();
        return startVal + (endVal - startVal) * a;
    }

    float getPumpFlow() const {
        const float base = getPumpFlowCeiling();
        if (!isAdvancedPump())
            return base;
        const float setpoint = currentPhase.pumpAdvanced.cupFlow;
        if (setpoint > 0.0f) {
            if (cupFlowLive) {
                return std::clamp(base + cupFlowTrim, CUP_FLOW_MIN_COMMAND, base);
            }
            if (currentPhase.pumpAdvanced.flow <= 0.0f) {
                // No scale and no pump-flow field: fall back to the cup value
                // as a plain pump-flow target (conservative, shot completes).
                return std::min(base, setpoint);
            }
        }
        return base;
    }

    float getTemperature() const {
        if (currentPhase.temperature > 0.0f) {
            return currentPhase.temperature;
        }
        return profile.temperature;
    }

    void progress() override {
        // Progress should be called around every 100ms, as defined in PROGRESS_INTERVAL, while the Process is active
        waterPumped += currentFlow / 10.0f; // Add current flow divided to 100ms to water pumped counter
        while (isCurrentPhaseFinished() && processPhase == ProcessPhase::RUNNING) {
            previousPhaseFinished = millis();
            if (phaseIndex + 1 < profile.phases.size()) {
                waterPumped = 0.0f;
                phaseIndex++;
                Phase nextPhase = profile.phases.at(phaseIndex);
                phaseStartPressure = nextPhase.transition.adaptive ? currentPressure : getPumpPressure();
                phaseStartFlow = nextPhase.transition.adaptive ? currentFlow : getPumpFlow();
                currentPhase = nextPhase;
                currentPhaseStarted = millis();
                computeEffectiveTargetsForCurrentPhase();
            } else {
                processPhase = ProcessPhase::FINISHED;
                finished = millis();
            }
        }
    }

    bool isActive() override { return processPhase == ProcessPhase::RUNNING; }

    bool isComplete() override {
        if (target == ProcessTarget::TIME) {
            return !isActive();
        }
        return processPhase == ProcessPhase::FINISHED && millis() - finished > PREDICTIVE_TIME;
    }

    int getType() override { return MODE_BREW; }

  private:
    // Cup-flow outer loop tuning. The cup responds to pump changes with
    // multi-second lag (puck transit), so the integrator is deliberately slow;
    // the measurement-side EMA (Settings → cup flow smoothing) adds stability.
    static constexpr float CUP_FLOW_TRIM_GAIN = 0.4f;   // (g/s command) per (g/s error) per second
    static constexpr float CUP_FLOW_MIN_COMMAND = 0.2f; // never trim the pump command below this
    static constexpr float CUP_FLOW_DEFAULT_CEILING = 6.0f; // ceiling when no pump-flow field is set

    float cupFlowTrim = 0.0f; // additive, clamped to [-(ceiling-min), 0]
    unsigned long lastCupTrimUpdate = 0;

    float phaseStartPressure = 0.0f;
    float phaseStartFlow = 0.0f;

    float effectivePressure = 0.0f;
    float effectiveFlow = 0.0f;

    static float easeLinear(float t) { return t; }
    static float easeIn(float t) { return t * t; }
    static float easeOut(float t) { return 1.0f - (1.0f - t) * (1.0f - t); }
    static float easeInOut(float t) { return (t < 0.5f) ? 2.0f * t * t : 1.0f - 2.0f * (1.0f - t) * (1.0f - t); }

    float applyEasing(float t, TransitionType type) const {
        if (t <= 0.0f)
            return 0.0f;
        if (t >= 1.0f)
            return 1.0f;
        switch (type) {
        case TransitionType::LINEAR:
            return easeLinear(t);
        case TransitionType::EASE_IN:
            return easeIn(t);
        case TransitionType::EASE_OUT:
            return easeOut(t);
        case TransitionType::EASE_IN_OUT:
            return easeInOut(t);
        case TransitionType::INSTANT:
        default:
            return 1.0f;
        }
    }

    void computeEffectiveTargetsForCurrentPhase() {
        if (currentPhase.pumpIsSimple) {
            effectivePressure = 0.0f;
            effectiveFlow = 0.0f;
            return;
        }

        // If the profile requests -1, use the *measured* value at the moment the phase starts.
        effectivePressure =
            (currentPhase.pumpAdvanced.pressure == -1.0f) ? phaseStartPressure : currentPhase.pumpAdvanced.pressure;
        effectiveFlow = (currentPhase.pumpAdvanced.flow == -1.0f) ? phaseStartFlow : currentPhase.pumpAdvanced.flow;
        if (currentPhase.pumpAdvanced.cupFlow > 0.0f && effectiveFlow <= 0.0f) {
            // Cup-flow phase without a pump-flow field: give the trim loop a
            // sane ceiling to work under.
            effectiveFlow = CUP_FLOW_DEFAULT_CEILING;
        }
        if (currentPhase.pumpAdvanced.target == PumpTarget::PUMP_TARGET_FLOW) {
            phaseStartPressure = effectivePressure;
        } else {
            phaseStartFlow = effectiveFlow;
        }
        // Each phase starts with a fresh trim — stale corrections from the
        // previous puck state would mis-command the new phase.
        cupFlowTrim = 0.0f;
        lastCupTrimUpdate = 0;
    }

    float transitionAlpha() const {
        float dur_s = currentPhase.transition.duration;
        if (dur_s <= 0.0f) {
            dur_s = currentPhase.duration; // If the transition has no duration, use the phase duration
        }
        if (currentPhase.transition.type == TransitionType::INSTANT || dur_s <= 0.0f) {
            return 1.0f;
        }
        const unsigned long elapsedMs = millis() - currentPhaseStarted;
        float t = float(elapsedMs) / (dur_s * 1000.0f);
        return applyEasing(t, currentPhase.transition.type);
    }
};

#endif // BREWPROCESS_H
