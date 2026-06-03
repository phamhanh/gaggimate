#ifndef SIMPLE_PID_H
#define SIMPLE_PID_H
#include <cmath>
#include <deque>
#include <vector>
// #define PI 3.14159265358979323846

class SimplePID {
  public:
    SimplePID(float *controlerOutput = nullptr, float *sensorOutput = nullptr, float *setpointTargetPtr = nullptr);
    bool update();
    void setControllerPIDGains(float Kp, float Ki, float Kd, float FF);
    void resetFeedbackController();
    void setSamplingFrequency(float freq);
    void setCtrlOutputLimits(float minOutput, float maxOutput);

    void initSetPointFilter(float initialValue);
    void setSetpointRateLimits(float lowerLimit, float upperLimit);
    void setSetpointDelaySamples(int delaySamples);
    void setSetpointFilterFrequency(float freq);

    void activateSetPointFilter(bool flag);

    void reset();

    void setManualOutput(float output = 0.0f);
    void computeSetpointDelay(float systemDelay);
    void activateFeedForward(bool flag);

    enum class Control : uint8_t { manual, automatic }; // controller mode
    void setMode(Control mode);

    float getCtrlSamplingFrequency() { return ctrl_freq_sampling; };
    float getKp() { return gainKp; };
    float getKi() { return gainKi; };
    float getKd() { return gainKd; };
    float getKFF() { return gainFF; };
    float getSetpointFiltered() const { return setpointFiltered; };
    float getSetpointValue() const { return *setpointTarget; };
    float getInputValue() const { return *sensorOutput; };

    void setKp(float val) { gainKp = val; };
    void setKi(float val) { gainKi = val; };
    void setKd(float val) { gainKd = val; };
    void setKFF(float val) { gainFF = val; };

    // Disturbance feedforward methods
    void setDisturbanceFeedforward(float disturbance, float gainDFF);
    void setDisturbanceGain(float gainDFF) { gainDistFF = gainDFF; };
    float getDisturbanceGain() { return gainDistFF; };

    /** Set EMA alpha for the derivative low-pass filter.
     *  1.0 = no filtering (default, backward-compatible).
     *  Lower values = more smoothing, e.g. 0.1 strongly attenuates sample-to-sample noise. */
    void setDerivativeFilterAlpha(float alpha) { derivFilterAlpha = alpha; }

    /** Near-target P/D softening (°C). Scales P and D by min(1, |error|/threshold); 0 = off. */
    void setErrorAttenuationThreshold(float c) { errorAttenuationThresholdC = c; }
    float getErrorAttenuationThreshold() const { return errorAttenuationThresholdC; }

    /** Snapshot P+I+D at latch time; caller enables freeze via setPidFrozen(). */
    void captureFrozenFeedback();
    void setPidFrozen(bool frozen) { pidFrozen = frozen; }
    bool isPidFrozen() const { return pidFrozen; }
    float getFrozenPidSum() const { return frozenPidSum; }

    /** Last computed feedback terms (controller units); updated at PID sample rate. */
    float getLastP() const { return lastPout; }
    float getLastI() const { return lastIout; }
    float getLastD() const { return lastDout; }
    /** Disturbance feedforward output from the last PID tick. */
    float getLastKffOut() const { return lastDistFFOut; }

    /** When CT > TT + X, zero P/D and use pidKiAbove for I (optional). */
    void setPdMuteEnabled(bool enabled) { pdMuteEnabled = enabled; }
    void setPdMuteAboveC(float c) { pdMuteAboveC = c; }
    void setPidKiAbove(float ki) { pidKiAbove = ki; }
    bool isPdMuted() const { return pdMuted; }
    float getActiveKi() const { return pdMuted ? pidKiAbove : gainKi; }

  private:
    static constexpr float PD_MUTE_HYSTERESIS_C = 0.3f;
    // setpoint filtering
    void setpointFiltering(float freq);
    bool isfilterSetpointActive = false;          // Flag to activate/deactivate the setpoint filter
    std::deque<float> setpointFilteredValues;     // Setpoint synchronized state
    float setpointDerivative = 0.0f;              // Setpoint derivative
    float setpointFiltstate1 = 0.0f;              // Setpoint State1
    float setpointFiltXi = 1.2f;                  // Setpoint filter damping
    float setpointFiltered = 0.0f;                // Filtered setpoint value
    uint32_t setpointDelaySamples = 5;            // Number of samples to delay the setpoint
    float setpointFilterFreq = 0.005f;            // Setpoint filter frequency
    float setpointRatelimits[2] = {-INFINITY, 2}; // Setpoint rate limits {lower, upper}
    bool isFeedForwardActive = false;             // Flag to activate/deactivate the feedforward control

    // feedback controler
    float ctrlOutputLimits[2] = {-INFINITY, INFINITY}; // Control output limits {lower, upper}
    float ctrl_freq_sampling = 1.0f;                   // Control frequency (Hz)
    bool isInitialized = false;                        // Flag to check if the controller is initialized
    float gainKp = 0.0f;                               // Proportional gain
    float gainKi = 0.0f; // Integral gain (multiplies by Kp if Kp,Ki,Kd are strictly parallèle (no factoring by Kp))
    float gainKd = 0.0f; // Derivative gain (by default no derivative term)
    float gainFF = 0.5 * 1000.0f / 2.5f; // Feedforward gain

    // Disturbance feedforward variables
    float gainDistFF = 0.0f;                     // Disturbance feedforward gain
    float currentDisturbance = 0.0f;             // Current disturbance value
    bool isDisturbanceFeedForwardActive = false; // Flag to activate disturbance feedforward
    bool pidFrozen = false;
    float frozenPidSum = 0.0f; // Latched P+I+D; held until caller clears freeze

    float feedback_integralState = 0.0f;   // Integral state
    float prevError = 0.0f;               // Previous error for derivative calculation
    float prevMeasurement = 0.0f;         // Previous measurement for derivative-on-measurement
    float filteredDerivative = 0.0f;      // Low-pass filtered derivative term
    float derivFilterAlpha = 1.0f;        // EMA alpha for derivative filter: 1.0 = no filter, lower = smoother
    float errorAttenuationThresholdC = 0.0f; // Near-target P/D scale; 0 = disabled
    bool pdMuteEnabled = false;
    float pdMuteAboveC = 0.5f;
    float pidKiAbove = 0.27f;
    bool pdMuted = false;
    float prevOutput = 0.0f;             // Previous output for derivative calculation
    float lastPout = 0.0f;
    float lastIout = 0.0f;
    float lastDout = 0.0f;
    float lastDistFFOut = 0.0f;
    Control mode = Control::manual;
    float manualOutput = 0.0f;
    unsigned long lastTime = 0;

    float *controlerOutput = nullptr; // Pointer to the control output variable
    float *sensorOutput = nullptr;    // Pointer to the sensor output variable
    float *setpointTarget = nullptr;  // System current target setpoint;
};

#endif
//
