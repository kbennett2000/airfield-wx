#include <freertos/FreeRTOS.h>
#include <freertos/task.h>
#include <freertos/semphr.h>
#include "esp_task_wdt.h"
#include "esp_system.h"
#include <Wire.h>
#include <Adafruit_BME280.h>
#include <TinyGPS.h>
#include <WiFi.h>
#include <WebServer.h>
#include <Adafruit_SSD1306.h>
#include "time.h"

#define SCREEN_WIDTH 128
#define SCREEN_HEIGHT 64
#define OLED_RESET -1
#define SCREEN_ADDRESS 0x3C
#define WDT_TIMEOUT 30 // timeout in seconds

#define GPS_RX 16
#define GPS_TX 17

// ── Anemometer (decision 4 / ADR-0003) ──────────────────────────────────────
// Outdoor suite is BME280 + GPS + anemometer (the TSL2591 light sensor was
// removed — decision 15). Defaults are for the Davis 6410 (continuous-pot
// direction). To swap in a SparkFun Weather Meter or another anemometer,
// change WIND_MPH_PER_HZ and the vaneDegrees() mapping below.
#define WIND_SPEED_PIN 25         // interrupt-capable GPIO; reed/hall pulse
#define WIND_DIR_PIN 34           // ADC1 input; vane wiper
#define ADC_MAX 4095.0f           // ESP32 12-bit ADC full scale
#define WIND_SAMPLE_MS 5000       // wind report cadence (matches sensorTask)
#define WIND_SUBSAMPLE_MS 1000    // gust resolution within a sample window
#define WIND_DEBOUNCE_US 1000     // reject reed-switch contact bounce
// Davis 6410: V(mph) = 2.25 * pulses/second (per datasheet). SparkFun Weather
// Meter ≈ 1.492f. Swap this one constant for a different anemometer.
#define WIND_MPH_PER_HZ 2.25f
#define MPH_TO_MS 0.44704f

// Store boot count in RTC memory (survives reset)
RTC_DATA_ATTR int bootCount = 0;

IPAddress ip(192, 168, 1, 60);
IPAddress gateway(192, 168, 1, 1);
IPAddress subnet(255, 255, 255, 0);
IPAddress dns(8, 8, 8, 8);

const char *ssid = "NetworkName";
const char *password = "NetworkPassword";
const char *ntpServer = "pool.ntp.org";
const long gmtOffset_sec = -25200;
const int daylightOffset_sec = 3600;

// Shared variable protection
SemaphoreHandle_t i2cMutex;
SemaphoreHandle_t tempOffsetMutex;
SemaphoreHandle_t dataMutex;

// Task handles
TaskHandle_t sensorTaskHandle = NULL;
TaskHandle_t windTaskHandle = NULL;
TaskHandle_t gpsTaskHandle = NULL;
TaskHandle_t displayTaskHandle = NULL;
TaskHandle_t webServerTaskHandle = NULL;
TaskHandle_t wifiWatchdogTaskHandle = NULL;

float TEMP_OFFSET = 0;

Adafruit_SSD1306 display(SCREEN_WIDTH, SCREEN_HEIGHT, &Wire, OLED_RESET);
Adafruit_BME280 bme;
TinyGPS gps;
WebServer server(80);

// Structure to hold sensor data
struct SensorData
{
    float temperatureC;
    float temperatureF;
    float humidity;
    float pressure;
    float windSpeed;     // m/s, mean over the sample interval
    float windGust;      // m/s, peak over the sample interval
    float windDirection; // degrees, raw vane frame (uncorrected)
    float latitude;
    float longitude;
    float altitude;
    float speed;
    float course;
    unsigned int satellites;
    unsigned long age;
    bool validData;
    long lastUpdateTime;
} sensorData;

float celsiusToFahrenheit(float celsius)
{
    return celsius * 9.0 / 5.0 + 32.0;
}

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
    // The inline HTML status page was removed during Phase 5 cleanup —
    // the new dashboard at the FastAPI server is the canonical UI. This
    // endpoint stays so curl / browser pokes get something readable.
    server.send(200, "text/plain", "airfield-wx outdoor sensor — see /data for JSON\n");
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
        // Every float field goes through floatJson() so a NaN reading
        // (e.g. GPS losing lock mid-cycle, a BME280 read failure) emits
        // valid JSON "null" instead of the invalid literal "nan".
        // ints stay as String() since they can't be NaN.
        json += "\"temperatureC\":" + floatJson(sensorData.temperatureC) + ",";
        json += "\"temperatureF\":" + floatJson(sensorData.temperatureF) + ",";
        json += "\"humidity\":" + floatJson(sensorData.humidity) + ",";
        json += "\"pressure\":" + floatJson(sensorData.pressure) + ",";
        json += "\"windSpeed\":" + floatJson(sensorData.windSpeed) + ",";
        json += "\"windGust\":" + floatJson(sensorData.windGust) + ",";
        json += "\"windDirection\":" + floatJson(sensorData.windDirection, 1) + ",";
        json += "\"latitude\":" + floatJson(sensorData.latitude, 6) + ",";
        json += "\"longitude\":" + floatJson(sensorData.longitude, 6) + ",";
        json += "\"altitude\":" + floatJson(sensorData.altitude) + ",";
        json += "\"speed\":" + floatJson(sensorData.speed) + ",";
        json += "\"course\":" + floatJson(sensorData.course) + ",";
        json += "\"satellites\":" + String(sensorData.satellites) + ",";
        json += "\"tempOffset\":" + floatJson(TEMP_OFFSET) + ",";
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

void sensorTask(void *parameter)
{
    TickType_t xLastWakeTime = xTaskGetTickCount();

    while (1)
    {
        // Log debug info
        Serial.printf("Sensor Task - Free heap: %d bytes\n", ESP.getFreeHeap());
        Serial.printf("WiFi RSSI: %d dBm\n", WiFi.RSSI());

        // Always acquire mutexes in the same order to prevent deadlocks
        if (xSemaphoreTake(dataMutex, pdMS_TO_TICKS(5000)) != pdTRUE)
        {
            Serial.println("Failed to acquire dataMutex");
            continue;
        }

        if (xSemaphoreTake(i2cMutex, pdMS_TO_TICKS(5000)) != pdTRUE)
        {
            xSemaphoreGive(dataMutex);
            Serial.println("Failed to acquire i2cMutex");
            continue;
        }

        if (xSemaphoreTake(tempOffsetMutex, pdMS_TO_TICKS(5000)) != pdTRUE)
        {
            xSemaphoreGive(i2cMutex);
            xSemaphoreGive(dataMutex);
            Serial.println("Failed to acquire tempOffsetMutex");
            continue;
        }

        bool success = true;

        // Read BME280
        float temp = bme.readTemperature();
        float humidity = bme.readHumidity();
        float pressure = bme.readPressure();

        if (isnan(temp) || temp < -40 || temp > 85)
        {
            Serial.println("Invalid temperature reading");
            success = false;
        }

        if (isnan(humidity) || humidity < 0 || humidity > 100)
        {
            Serial.println("Invalid humidity reading");
            success = false;
        }

        if (isnan(pressure) || pressure < 30000 || pressure > 110000)
        {
            Serial.println("Invalid pressure reading");
            success = false;
        }

        if (success)
        {
            sensorData.temperatureC = temp + TEMP_OFFSET;
            sensorData.temperatureF = celsiusToFahrenheit(sensorData.temperatureC);
            sensorData.humidity = humidity;
            sensorData.pressure = pressure / 100.0F;
        }

        // Wind is sampled continuously in windTask (interrupt pulse counter +
        // vane ADC); nothing to read here.

        sensorData.validData = success;
        sensorData.lastUpdateTime = millis();

        if (!success)
        {
            Serial.println("Attempting to reinitialize sensors...");
            if (!bme.begin(0x76))
            {
                Serial.println("Failed to reinitialize BME280");
            }
        }

        xSemaphoreGive(tempOffsetMutex);
        xSemaphoreGive(i2cMutex);
        xSemaphoreGive(dataMutex);

        vTaskDelayUntil(&xLastWakeTime, pdMS_TO_TICKS(5000));
    }
}

void gpsTask(void *parameter)
{
    while (1)
    {
        while (Serial2.available())
        {
            char c = Serial2.read();
            gps.encode(c);
        }

        if (xSemaphoreTake(dataMutex, pdMS_TO_TICKS(1000)) == pdTRUE)
        {
            gps.f_get_position(&sensorData.latitude, &sensorData.longitude, &sensorData.age);
            sensorData.altitude = gps.altitude() / 100.0;
            sensorData.speed = gps.speed() * 0.0185;
            sensorData.course = gps.course() / 100.0;
            sensorData.satellites = gps.satellites();
            xSemaphoreGive(dataMutex);
        }

        vTaskDelay(pdMS_TO_TICKS(100));
    }
}

void displayTask(void *parameter)
{
    uint8_t displayPage = 0;
    TickType_t xLastWakeTime = xTaskGetTickCount();

    while (1)
    {
        if (xSemaphoreTake(dataMutex, pdMS_TO_TICKS(1000)) != pdTRUE)
        {
            continue;
        }

        if (xSemaphoreTake(i2cMutex, pdMS_TO_TICKS(1000)) != pdTRUE)
        {
            xSemaphoreGive(dataMutex);
            continue;
        }

        display.clearDisplay();
        display.setTextSize(1);
        display.setCursor(0, 0);

        if (sensorData.validData)
        {
            switch (displayPage)
            {
            case 0:
                display.println("Weather:");
                display.printf("Temp: %.1fC\n", sensorData.temperatureC);
                display.printf("Hum:  %.1f%%\n", sensorData.humidity);
                display.printf("Press:%.1fhPa\n", sensorData.pressure);
                break;
            case 1:
                display.println("Wind:");
                display.printf("Spd: %.1f m/s\n", sensorData.windSpeed);
                display.printf("Gst: %.1f m/s\n", sensorData.windGust);
                display.printf("Dir: %.0f deg\n", sensorData.windDirection);
                break;
            case 2:
                display.println("Location:");
                if (sensorData.age < 5000)
                {
                    display.printf("Lat: %.6f\n", sensorData.latitude);
                    display.printf("Lon: %.6f\n", sensorData.longitude);
                    display.printf("Alt: %.1fm\n", sensorData.altitude);
                }
                else
                {
                    display.println("No GPS Fix");
                }
                break;
            case 3:
                display.println("System:");
                display.printf("RSSI: %d dBm\n", WiFi.RSSI());
                display.printf("Heap: %d KB\n", ESP.getFreeHeap() / 1024);
                display.printf("Up: %d min\n", millis() / 60000);
                break;
            }
        }
        else
        {
            display.println("Sensor Error");
            display.println("Check Serial");
            display.println("Monitor");
            display.printf("Heap: %d KB\n", ESP.getFreeHeap() / 1024);
        }

        display.display();
        displayPage = (displayPage + 1) % 4; // Now 4 pages instead of 3

        xSemaphoreGive(i2cMutex);
        xSemaphoreGive(dataMutex);

        vTaskDelayUntil(&xLastWakeTime, pdMS_TO_TICKS(10000));
    }
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
            Serial.printf("Last sensor update: %lu ms ago\n", millis() - sensorData.lastUpdateTime);
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
    Serial2.begin(9600, SERIAL_8N1, GPS_RX, GPS_TX);
    delay(1000);

    bootCount++;
    Serial.printf("\n\nStarting Weather Station... (Boot Count: %d)\n", bootCount);
    Serial.printf("Reset Reason: %d\n", esp_reset_reason());

    // Create mutexes
    i2cMutex = xSemaphoreCreateMutex();
    tempOffsetMutex = xSemaphoreCreateMutex();
    dataMutex = xSemaphoreCreateMutex();

    if (!i2cMutex || !tempOffsetMutex || !dataMutex)
    {
        Serial.println("Failed to create mutexes!");
        while (1)
            delay(1000);
    }

    Wire.begin(21, 22);
    Serial.println("I2C Initialized");

    if (!display.begin(SSD1306_SWITCHCAPVCC, SCREEN_ADDRESS))
    {
        Serial.println("SSD1306 allocation failed");
        while (1)
            delay(10);
    }
    Serial.println("Display Initialized");

    display.clearDisplay();
    display.setTextColor(WHITE);
    display.setTextSize(1);

    // Initialize BME280
    int bmeRetries = 3;
    while (!bme.begin(0x76) && bmeRetries > 0)
    {
        Serial.println("Retrying BME280 initialization...");
        delay(1000);
        bmeRetries--;
    }
    if (bmeRetries == 0)
    {
        Serial.println("Could not find BME280 sensor!");
        display.clearDisplay();
        display.println("BME280\nError!");
        display.display();
        while (1)
            delay(10);
    }
    Serial.println("BME280 Initialized");

    // Initialize anemometer: pulse input (interrupt) + vane ADC.
    pinMode(WIND_SPEED_PIN, INPUT_PULLUP);
    attachInterrupt(digitalPinToInterrupt(WIND_SPEED_PIN), windPulseISR, FALLING);
    analogReadResolution(12); // 0..ADC_MAX
    Serial.println("Anemometer Initialized");

    display.clearDisplay();
    display.println("Connecting\nto WiFi...");
    display.display();

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

    // Initialize sensorData
    sensorData.validData = false;
    sensorData.lastUpdateTime = millis();

    // Create tasks with adjusted priorities and stack sizes
    xTaskCreate(sensorTask, "SensorTask", 4096, NULL, 3, &sensorTaskHandle);
    xTaskCreate(windTask, "WindTask", 2048, NULL, 3, &windTaskHandle);
    xTaskCreate(webServerTask, "WebServerTask", 4096, NULL, 2, &webServerTaskHandle);
    xTaskCreate(gpsTask, "GPSTask", 4096, NULL, 2, &gpsTaskHandle);
    xTaskCreate(displayTask, "DisplayTask", 4096, NULL, 1, &displayTaskHandle);
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
