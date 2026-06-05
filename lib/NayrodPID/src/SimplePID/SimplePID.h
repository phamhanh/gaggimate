#ifndef SIMPLE_PID_H
#define SIMPLE_PID_H
#include <cmath>
#include <deque>
#include <vector>
// #define PI 3.14159265358979323846

class SimplePID {
  public:
    // 3-zone gain scheduler. Zone is chosen from CT relative to TT and the
    // configurable bands; one shared integrator is carried across zones with a
    // bumpless Ki transition (see update()).
    enum class PidZone : uint8_t { heating = 0, stabilizing = 1, cooling = 2 };

    SimplePID(float *controlerOutput = nullptr, float *sensorOutput = nullptr, float *setpointTargetPtr = nullptr);
    bool update();
    /** Heating-zone PID gains (full aggressive set) + feedforward gain. */
    void setControllerPIDGains(float Kp, float Ki, float Kd, float FF);
    /** Zone band thresholds (°C). Heating below TT-below, cooling above TT+above. */
    void setZoneBands(float belowC, float aboveC);
    /** Stabilizing-zone PID gains (near setpoint). */
    void setStabGains(float Kp, float Ki, float Kd);
    /** Cooling-zone PID gains (above setpoint; integral allowed to unwind). */
    void setCoolGains(float Kp, float Ki, float Kd);
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

    /** Snapshot P+I+D at latch time; caller enables freeze via setPidFrozen(). */
    void captureFrozenFeedback();
    /** Bumpless unfreeze: re-seed the live integrator from the latched I sum so
     *  pidLive.i stays continuous when freeze ends, and refresh prevMeasurement
     *  to suppress the stale-derivative kick on the first unfrozen tick. */
    void restoreIntegralFromFrozenSum();
    void setPidFrozen(bool frozen) { pidFrozen = frozen; }
    bool isPidFrozen() const { return pidFrozen; }
    float getFrozenPidSum() const { return frozenPidSum; }

    /** Last computed feedback terms (controller units); updated at PID sample rate. */
    float getLastP() const { return lastPout; }
    float getLastI() const { return lastIout; }
    float getLastD() const { return lastDout; }
    /** Disturbance feedforward output from the last PID tick. */
    float getLastKffOut() const { return lastDistFFOut; }

    /** Active zone selected on the last tick (0=heating, 1=stabilizing, 2=cooling). */
    int getActiveZone() const { return static_cast<int>(activeZone); }
    /** Ki of the active zone on the last tick (telemetry / freeze latch). */
    float getActiveKi() const { return lastActiveKi; }

  private:
    static constexpr float ZONE_HYSTERESIS_C = 0.3f;
    PidZone selectZone(float ct, float tt, PidZone current) const;
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
    // Heating-zone gains (full aggressive set; also the autotune target).
    float gainKp = 0.0f;                               // Proportional gain
    float gainKi = 0.0f; // Integral gain (multiplies by Kp if Kp,Ki,Kd are strictly parallèle (no factoring by Kp))
    float gainKd = 0.0f; // Derivative gain (by default no derivative term)
    float gainFF = 0.5 * 1000.0f / 2.5f; // Feedforward gain

    // Stabilizing-zone gains (near setpoint).
    float gainKpStab = 0.0f;
    float gainKiStab = 0.0f;
    float gainKdStab = 0.0f;
    // Cooling-zone gains (above setpoint).
    float gainKpCool = 0.0f;
    float gainKiCool = 0.0f;
    float gainKdCool = 0.0f;

    // Zone scheduling state.
    float bandBelowC = 0.3f;                  // Heating when CT < TT - bandBelowC
    float bandAboveC = 0.5f;                  // Cooling when CT > TT + bandAboveC
    PidZone activeZone = PidZone::heating;    // Zone selected on the last tick
    float lastActiveKi = 0.0f;                // Ki applied on the last tick (bumpless transitions)

    // Disturbance feedforward variables
    float gainDistFF = 0.0f;                     // Disturbance feedforward gain
    float currentDisturbance = 0.0f;             // Current disturbance value
    bool isDisturbanceFeedForwardActive = false; // Flag to activate disturbance feedforward
    bool pidFrozen = false;
    float frozenPidSum = 0.0f; // Latched P+I+D; held until caller clears freeze

    float feedback_integralState = 0.0f;   // Integral state (non-negative; heat-only actuator)
    float prevError = 0.0f;               // Previous error for derivative calculation
    float prevMeasurement = 0.0f;         // Previous measurement for derivative-on-measurement
    float filteredDerivative = 0.0f;      // Low-pass filtered derivative term
    float derivFilterAlpha = 1.0f;        // EMA alpha for derivative filter: 1.0 = no filter, lower = smoother
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
