#ifndef MAX31855THERMOCOUPLE_H
#define MAX31855THERMOCOUPLE_H

#include "TemperatureSensor.h"
#include <MAX31855.h>
#include <freertos/FreeRTOS.h>
#include <freertos/task.h>

constexpr int MAX31855_UPDATE_INTERVAL = 250;
constexpr int MAX31855_ERROR_WINDOW = 20;
constexpr float MAX31855_MAX_ERROR_RATE = 0.5f;
constexpr int MAX31855_MAX_ERRORS = static_cast<int>(static_cast<float>(MAX31855_ERROR_WINDOW) * MAX31855_MAX_ERROR_RATE);
constexpr double MAX_SAFE_TEMP = 170.0;
constexpr bool DEFAULT_TEMP_PROBE_FILTER_ENABLED = true;
constexpr float DEFAULT_TEMP_PROBE_FILTER_ALPHA = 0.05f;
constexpr float TEMP_PROBE_FILTER_ALPHA_MIN = 0.01f;
constexpr float TEMP_PROBE_FILTER_ALPHA_MAX = 1.0f;

using temperature_callback_t = std::function<void(float)>;
using temperature_error_callback_t = std::function<void()>;

class Max31855Thermocouple : public TemperatureSensor {
  public:
    Max31855Thermocouple(int csPin, int misoPin, int sckPin, const temperature_callback_t &callback,
                         const temperature_error_callback_t &error_callback);
    float read() override;
    bool isErrorState() override;

    void setup();
    void loop();
    void setFilterEnabled(bool enabled);
    void setFilterAlpha(float alpha);
    void setFilter(bool enabled, float alpha);

  private:
    static float clampFilterAlpha(float alpha);
    void seedFilterFromRaw();

    MAX31855 *max31855;
    xTaskHandle taskHandle;

    int errorCount = 0;
    std::array<int, MAX31855_ERROR_WINDOW> resultBuffer{};
    size_t resultCount = 0;
    size_t bufferIndex = 0;

    float temperature = .0f;
    float lastRawTemp = 0.0f;
    bool filterEnabled = DEFAULT_TEMP_PROBE_FILTER_ENABLED;
    float filterAlpha = DEFAULT_TEMP_PROBE_FILTER_ALPHA;

    int csPin = 0;
    int misoPin = 0;
    int sckPin = 0;

    temperature_callback_t callback;
    temperature_error_callback_t error_callback;

    const char *LOG_TAG = "Max31855Thermocouple";
    static void monitorTask(void *arg);
};

#endif // MAX31855THERMOCOUPLE_H
