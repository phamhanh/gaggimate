#ifndef PUMPPROCESS_H
#define PUMPPROCESS_H

#include <display/core/constants.h>
#include <display/core/process/Process.h>

class PumpProcess : public Process {
  public:
    int duration;
    unsigned long started;
    bool valveOpen = false;

    explicit PumpProcess(int duration = HOT_WATER_SAFETY_DURATION_MS, bool valveOpen = false)
        : duration(duration), valveOpen(valveOpen) {
        started = millis();
    }

    bool isRelayActive() override { return valveOpen; };

    bool isAltRelayActive() override { return false; };

    float getPumpValue() override { return isActive() ? 100.f : 0.f; };

    void progress() override {
        // Stateless implementation
    };

    bool isActive() override {
        unsigned long now = millis();
        return now - started < duration;
    };

    bool isComplete() override { return !isActive(); };

    int getType() override { return MODE_WATER; }

    void updateVolume(double volume) override {};
};

#endif // PUMPPROCESS_H
