// airfield-wx — separate wind station (ADR-0006 / topology 3).
//
// A dedicated ESP32 + anemometer mounted at the good wind spot, reporting
// ANEMOMETER ONLY: wind speed (m/s mean), gust (m/s peak), and direction in
// degrees in the RAW VANE FRAME. NO BME280, NO GPS — temperature/humidity/
// pressure and location intelligence stay anchored to the outdoor unit, and
// the vane north-alignment offset is applied SERVER-SIDE by the resolver
// (per the Cycle 6 convention). This is the opt-in topology for sites where
// the anemometer (high, clear mast) and the BME280 (shaded ventilation) can't
// co-locate.
//
// The anemometer code (pulse ISR + ADC vane + m/s conversion) is identical to
// sketches/outdoor.ino — see that file's comments. Wire keys match the outdoor
// unit's so wire_format.parse_wind_station() ingests them unchanged.
#include <freertos/FreeRTOS.h>
#include <freertos/task.h>
#include <freertos/semphr.h>
#include "esp_task_wdt.h"
#include "esp_system.h"
#include <WiFi.h>
#include <WebServer.h>
#include "time.h"

#define WDT_TIMEOUT 30 // timeout in seconds

// ── Anemometer (decision 4 / ADR-0003; topology 3 / ADR-0006) ───────────────
// Defaults are for the Davis 6410 (continuous-pot direction). To swap in a
// SparkFun Weather Meter or another anemometer, change WIND_MPH_PER_HZ and the
// vaneDegrees() mapping below — exactly as on the outdoor unit.
#define WIND_SPEED_PIN 25         // interrupt-capable GPIO; reed/hall pulse
#define WIND_DIR_PIN 34           // ADC1 input; vane wiper
#define ADC_MAX 4095.0f           // ESP32 12-bit ADC full scale
#define WIND_SAMPLE_MS 5000       // wind report cadence
#define WIND_SUBSAMPLE_MS 1000    // gust resolution within a sample window
#define WIND_DEBOUNCE_US 1000     // reject reed-switch contact bounce
// Davis 6410: V(mph) = 2.25 * pulses/second (per datasheet). SparkFun Weather
// Meter ≈ 1.492f. Swap this one constant for a different anemometer.
#define WIND_MPH_PER_HZ 2.25f
#define MPH_TO_MS 0.44704f

// Store boot count in RTC memory (survives reset)
RTC_DATA_ATTR int bootCount = 0;

// Pick a LAN address distinct from the outdoor unit (which defaults to .60).
IPAddress ip(192, 168, 1, 61);
IPAddress gateway(192, 168, 1, 1);
IPAddress subnet(255, 255, 255, 0);
IPAddress dns(8, 8, 8, 8);

const char *ssid = "NetworkName";
const char *password = "NetworkPassword";

// Shared variable protection
SemaphoreHandle_t dataMutex;

// Task handles
TaskHandle_t windTaskHandle = NULL;
TaskHandle_t webServerTaskHandle = NULL;
TaskHandle_t wifiWatchdogTaskHandle = NULL;

WebServer server(80);

// Structure to hold wind data (anemometer only).
struct SensorData
{
    float windSpeed;     // m/s, mean over the sample interval
    float windGust;      // m/s, peak over the sample interval
    float windDirection; // degrees, raw vane frame (uncorrected)
    bool validData;
    long lastUpdateTime;
} sensorData;

// ── Anemometer ───────────────────────────────────────────────────────────────
// Speed is measured by counting anemometer pulses in an ISR; direction is an
// ADC read of the vane. windTask() turns pulses into a mean + gust over each
// sample window and writes them into sensorData under dataMutex.
volatile uint32_t windPulses = 0;

void IRAM_ATTR windPulseISR()
{
    static volatile uint32_t lastPulseUs = 0;
    uint32_t now = micros();
    if (now - lastPulseUs >= WIND_DEBOUNCE_US)
    {
        windPulses++;
        lastPulseUs = now;
    }
}

// Vane reading → degrees, raw vane frame. Davis 6410 is a continuous pot, so
// the mapping is linear over the ADC range. A SparkFun Weather Meter is a
// resistor network with 8/16 discrete positions — replace this with a
// nearest-voltage lookup table for that hardware.
float vaneDegrees(int adc)
{
    float deg = (adc / ADC_MAX) * 360.0f;
    if (deg < 0.0f) deg += 360.0f;
    if (deg >= 360.0f) deg -= 360.0f;
    return deg;
}

void windTask(void *parameter)
{
    TickType_t xLastWakeTime = xTaskGetTickCount();
    float speedSum = 0.0f;
    float peak = 0.0f;
    int subsamples = 0;
    const int subsamplesPerWindow = WIND_SAMPLE_MS / WIND_SUBSAMPLE_MS;

    while (1)
    {
        // Atomically read-and-clear the pulse count accumulated this subsample.
        portDISABLE_INTERRUPTS();
        uint32_t pulses = windPulses;
        windPulses = 0;
        portENABLE_INTERRUPTS();

        float hz = pulses / (WIND_SUBSAMPLE_MS / 1000.0f);
        float instant = hz * WIND_MPH_PER_HZ * MPH_TO_MS; // m/s
        speedSum += instant;
        if (instant > peak) peak = instant;
        subsamples++;

        if (subsamples >= subsamplesPerWindow)
        {
            float mean = speedSum / subsamples;
            // Direction is always read (even in calm) so the vane angle is
            // reported regardless of speed.
            float dir = vaneDegrees(analogRead(WIND_DIR_PIN));
            if (xSemaphoreTake(dataMutex, pdMS_TO_TICKS(1000)) == pdTRUE)
            {
                sensorData.windSpeed = mean;
                sensorData.windGust = peak;
                sensorData.windDirection = dir;
                sensorData.validData = true;
                sensorData.lastUpdateTime = millis();
                xSemaphoreGive(dataMutex);
            }
            speedSum = 0.0f;
            peak = 0.0f;
            subsamples = 0;
        }

        vTaskDelayUntil(&xLastWakeTime, pdMS_TO_TICKS(WIND_SUBSAMPLE_MS));
    }
}

void checkWiFiConnection()
{
    if (WiFi.status() != WL_CONNECTED)
    {
        Serial.println("WiFi disconnected. Reconnecting...");
        WiFi.disconnect();
        WiFi.begin(ssid, password);
        WiFi.config(ip, gateway, subnet, dns);

        int attempts = 0;
        while (WiFi.status() != WL_CONNECTED && attempts < 20)
        {
            delay(500);
            Serial.print(".");
            attempts++;
        }

        if (WiFi.status() == WL_CONNECTED)
        {
            Serial.println("\nWiFi reconnected");
            Serial.printf("RSSI: %d dBm\n", WiFi.RSSI());
        }
        else
        {
            Serial.println("\nWiFi reconnection failed");
        }
    }
}

// Emit a float as JSON, substituting "null" for NaN. Without this, ESP32's
// String(NaN) prints the literal text "nan", which is not valid JSON and
// breaks parsers downstream. This is BUG-08 fixed at the source — the
// server's wire_format.py keeps a defense-in-depth regex for old firmware
// that still emits "nan", but new sketches must emit clean JSON.
static String floatJson(float v, unsigned int decimals = 2)
{
    if (isnan(v))
    {
        return String("null");
    }
    return String(v, decimals);
}

void handleRoot()
{
    server.send(200, "text/plain", "airfield-wx wind station — see /data for JSON\n");
}

void handleData()
{
    if (xSemaphoreTake(dataMutex, pdMS_TO_TICKS(5000)) != pdTRUE)
    {
        server.send(503, "text/plain", "Server busy");
        return;
    }

    String json = "{";
    if (sensorData.validData)
    {
        // Anemometer-only payload: wind (raw vane frame) + device telemetry.
        // No temperature/pressure/GPS — see the file header.
        json += "\"windSpeed\":" + floatJson(sensorData.windSpeed) + ",";
        json += "\"windGust\":" + floatJson(sensorData.windGust) + ",";
        json += "\"windDirection\":" + floatJson(sensorData.windDirection, 1) + ",";
        json += "\"rssi\":" + String(WiFi.RSSI()) + ",";
        json += "\"uptime\":" + String(millis()) + ",";
        json += "\"freeHeap\":" + String(ESP.getFreeHeap());
    }
    else
    {
        json += "\"error\":\"No valid sensor data\"";
    }
    json += "}";
    xSemaphoreGive(dataMutex);

    server.send(200, "application/json", json);
}

void wifiWatchdogTask(void *parameter)
{
    while (1)
    {
        checkWiFiConnection();

        // Log system status every minute
        static unsigned long lastLog = 0;
        if (millis() - lastLog >= 60000)
        {
            Serial.printf("System Status:\n");
            Serial.printf("Uptime: %lu minutes\n", millis() / 60000);
            Serial.printf("Free Heap: %u bytes\n", ESP.getFreeHeap());
            Serial.printf("WiFi RSSI: %d dBm\n", WiFi.RSSI());
            Serial.printf("Last wind update: %lu ms ago\n", millis() - sensorData.lastUpdateTime);
            Serial.printf("Boot count: %d\n", bootCount);
            lastLog = millis();
        }

        vTaskDelay(pdMS_TO_TICKS(10000)); // Check every 10 seconds
    }
}

void webServerTask(void *parameter)
{
    while (1)
    {
        server.handleClient();
        vTaskDelay(pdMS_TO_TICKS(10));
    }
}

void setup()
{
    Serial.begin(115200);
    delay(1000);

    bootCount++;
    Serial.printf("\n\nStarting Wind Station... (Boot Count: %d)\n", bootCount);
    Serial.printf("Reset Reason: %d\n", esp_reset_reason());

    dataMutex = xSemaphoreCreateMutex();
    if (!dataMutex)
    {
        Serial.println("Failed to create mutex!");
        while (1)
            delay(1000);
    }

    // Initialize anemometer: pulse input (interrupt) + vane ADC.
    pinMode(WIND_SPEED_PIN, INPUT_PULLUP);
    attachInterrupt(digitalPinToInterrupt(WIND_SPEED_PIN), windPulseISR, FALLING);
    analogReadResolution(12); // 0..ADC_MAX
    Serial.println("Anemometer Initialized");

    WiFi.begin(ssid, password);
    WiFi.config(ip, gateway, subnet, dns);

    int wifiRetries = 30;
    while (WiFi.status() != WL_CONNECTED && wifiRetries > 0)
    {
        delay(500);
        Serial.print(".");
        wifiRetries--;
    }

    if (WiFi.status() != WL_CONNECTED)
    {
        Serial.println("\nWiFi connection failed!");
        ESP.restart();
    }

    Serial.println("\nWiFi Connected!");
    Serial.printf("IP Address: %s\n", WiFi.localIP().toString().c_str());
    Serial.printf("RSSI: %d dBm\n", WiFi.RSSI());

    server.on("/", handleRoot);
    server.on("/data", handleData);
    server.begin();

    Serial.println("HTTP server started");

    // Initialize sensorData (no valid wind until windTask reports a window).
    sensorData.validData = false;
    sensorData.lastUpdateTime = millis();

    // Anemometer + web server + WiFi watchdog only — no sensor/GPS/display tasks.
    xTaskCreate(windTask, "WindTask", 2048, NULL, 3, &windTaskHandle);
    xTaskCreate(webServerTask, "WebServerTask", 4096, NULL, 2, &webServerTaskHandle);
    xTaskCreate(wifiWatchdogTask, "WiFiWatchdog", 4096, NULL, 1, &wifiWatchdogTaskHandle);

    // Configure watchdog
    esp_task_wdt_config_t wdtConfig = {
        .timeout_ms = WDT_TIMEOUT * 1000,
        .idle_core_mask = (1 << portNUM_PROCESSORS) - 1,
        .trigger_panic = true};
    esp_task_wdt_init(&wdtConfig);
}

void loop()
{
    vTaskDelete(NULL); // Delete setup and loop task
}
