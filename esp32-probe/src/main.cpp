// =============================================================================
// LabWiFiMon ESP32 Probe — Main Entry Point
// =============================================================================
//
// Topology overview:
//   setup()  – initialise hardware, connect WiFi/MQTT, sync NTP
//   loop()   – every MEASUREMENT_INTERVAL_MS: collect metrics, publish JSON
//              every HEARTBEAT_INTERVAL_MS:   publish status heartbeat
//              every SCAN_INTERVAL_MS:        publish WiFi channel scan
//
// MQTT topics published:
//   labwifimon/<PROBE_ID>/metrics  – full metrics JSON
//   labwifimon/<PROBE_ID>/scan     – WiFi AP scan results
//   labwifimon/<PROBE_ID>/status   – online/offline heartbeat (retained)
//
// =============================================================================

#include <Arduino.h>
#include <WiFi.h>
#include <ArduinoJson.h>
#include <esp_task_wdt.h>
#include <esp_idf_version.h>

#include "config.h"
#include "metrics.h"
#include "network.h"

// ---------------------------------------------------------------------------
// RTC memory — survives deep sleep, reset to 0 on power-on or hard reset
// ---------------------------------------------------------------------------
RTC_DATA_ATTR uint32_t g_bootCount   = 0;   // total wake-ups from deep sleep
RTC_DATA_ATTR uint32_t g_cycleCount  = 0;   // total measurement cycles published

// ---------------------------------------------------------------------------
// Module state
// ---------------------------------------------------------------------------
static unsigned long g_lastMetricsMs   = 0;
static unsigned long g_lastHeartbeatMs = 0;
static unsigned long g_lastScanMs      = 0;
static unsigned long g_sketchStartMs   = 0;

// ---------------------------------------------------------------------------
// LED  (non-blocking blink state machine)
// ---------------------------------------------------------------------------
enum class LedMode {
    OFF,
    SOLID,
    SLOW_BLINK,    // 1 Hz — normal operation
    FAST_BLINK,    // 10 Hz — WiFi disconnected / error
    DOUBLE_PULSE,  // two quick flashes, then pause — publishing
};

static LedMode       g_ledMode       = LedMode::SOLID;
static unsigned long g_ledLastMs     = 0;
static bool          g_ledState      = false;
static uint8_t       g_ledPhase      = 0;   // for multi-pulse patterns

static inline void ledOn()  {
    digitalWrite(STATUS_LED_PIN,
        STATUS_LED_ACTIVE_HIGH ? HIGH : LOW);
    g_ledState = true;
}
static inline void ledOff() {
    digitalWrite(STATUS_LED_PIN,
        STATUS_LED_ACTIVE_HIGH ? LOW : HIGH);
    g_ledState = false;
}

static void updateLed() {
    unsigned long now = millis();
    switch (g_ledMode) {
        case LedMode::OFF:
            ledOff();
            break;

        case LedMode::SOLID:
            ledOn();
            break;

        case LedMode::SLOW_BLINK:
            // 500 ms on / 500 ms off
            if (now - g_ledLastMs >= 500) {
                g_ledLastMs = now;
                g_ledState ? ledOff() : ledOn();
            }
            break;

        case LedMode::FAST_BLINK:
            // 50 ms on / 50 ms off
            if (now - g_ledLastMs >= 50) {
                g_ledLastMs = now;
                g_ledState ? ledOff() : ledOn();
            }
            break;

        case LedMode::DOUBLE_PULSE: {
            // Pattern: ON 80 ms → OFF 80 ms → ON 80 ms → OFF 760 ms (1 Hz total)
            static const uint16_t pattern[] = {80, 80, 80, 760};
            static const uint8_t  nPhases   = 4;
            if (now - g_ledLastMs >= pattern[g_ledPhase]) {
                g_ledLastMs = now;
                g_ledPhase  = (g_ledPhase + 1) % nPhases;
                (g_ledPhase == 0 || g_ledPhase == 2) ? ledOn() : ledOff();
            }
            break;
        }
    }
}

// ---------------------------------------------------------------------------
// Watchdog initialisation
// ---------------------------------------------------------------------------
static void setupWatchdog() {
#if ESP_IDF_VERSION >= ESP_IDF_VERSION_VAL(5, 0, 0)
    esp_task_wdt_config_t wdt_cfg = {
        .timeout_ms     = (uint32_t)WDT_TIMEOUT_S * 1000,
        .idle_core_mask = 0,
        .trigger_panic  = true,
    };
    // init may return ESP_ERR_INVALID_STATE if already initialised by the
    // Arduino bootloader; in that case reconfigure it instead.
    esp_err_t err = esp_task_wdt_init(&wdt_cfg);
    if (err == ESP_ERR_INVALID_STATE) {
        esp_task_wdt_reconfigure(&wdt_cfg);
    }
#else
    esp_task_wdt_init(WDT_TIMEOUT_S, /*panic=*/true);
#endif
    esp_task_wdt_add(NULL);  // subscribe the loop task
    Serial.printf("[WDT] Hardware watchdog armed (%d s timeout)\n",
                  WDT_TIMEOUT_S);
}

// ---------------------------------------------------------------------------
// JSON builders
// ---------------------------------------------------------------------------

/**
 * Serialise ProbeMetrics into the metrics JSON described in the project spec.
 * Writes into @buf (size @bufLen).  Returns number of bytes written (0 on
 * error).
 */
static size_t buildMetricsJSON(const ProbeMetrics &m, char *buf, size_t bufLen) {
    JsonDocument doc;

    doc["probe_id"]   = PROBE_ID;
    doc["location"]   = PROBE_LOCATION;
    doc["timestamp"]  = getISO8601Timestamp();
    doc["uptime_s"]   = m.uptime_s;
    doc["cycle"]      = g_cycleCount;
    doc["free_heap"]  = m.free_heap_bytes;
    doc["min_free_heap"] = m.min_free_heap_bytes;

    // WiFi
    JsonObject wifi = doc["wifi"].to<JsonObject>();
    wifi["ssid"]     = m.wifi.ssid;
    wifi["bssid"]    = m.wifi.bssid;
    wifi["channel"]  = m.wifi.channel;
    wifi["rssi_dbm"] = m.wifi.rssi_dbm;

    // Helper: round a float to N decimal places for tidy JSON numbers.
    // ArduinoJson emits floats as numbers; rounding avoids 2.2999999 artifacts.
    auto r2 = [](float v) { return roundf(v * 100.0f) / 100.0f; };
    auto r1 = [](float v) { return roundf(v *  10.0f) /  10.0f; };

    // Latency — gateway
    JsonObject latency = doc["latency"].to<JsonObject>();
    if (m.latency.gateway.reachable) {
        latency["gateway_avg_ms"] = r2(m.latency.gateway.avg_ms);
        latency["gateway_min_ms"] = r2(m.latency.gateway.min_ms);
        latency["gateway_max_ms"] = r2(m.latency.gateway.max_ms);
    } else {
        latency["gateway_avg_ms"] = nullptr;
        latency["gateway_min_ms"] = nullptr;
        latency["gateway_max_ms"] = nullptr;
    }
    // Latency — external
    if (m.latency.external.reachable) {
        latency["external_avg_ms"] = r2(m.latency.external.avg_ms);
        latency["external_min_ms"] = r2(m.latency.external.min_ms);
        latency["external_max_ms"] = r2(m.latency.external.max_ms);
    } else {
        latency["external_avg_ms"] = nullptr;
        latency["external_min_ms"] = nullptr;
        latency["external_max_ms"] = nullptr;
    }

    // Jitter
    JsonObject jitter = doc["jitter"].to<JsonObject>();
    if (m.latency.gateway.reachable)
        jitter["gateway_ms"]  = r2(m.latency.gateway.jitter_ms);
    else
        jitter["gateway_ms"]  = nullptr;
    if (m.latency.external.reachable)
        jitter["external_ms"] = r2(m.latency.external.jitter_ms);
    else
        jitter["external_ms"] = nullptr;

    // Packet loss
    JsonObject pktloss = doc["packet_loss"].to<JsonObject>();
    pktloss["gateway_pct"]  = r1(m.latency.gateway.packet_loss_pct);
    pktloss["external_pct"] = r1(m.latency.external.packet_loss_pct);

    // Throughput
    JsonObject tput = doc["throughput"].to<JsonObject>();
    if (m.throughput.success) {
        tput["download_kbps"]    = r1(m.throughput.download_kbps);
        tput["test_bytes"]       = m.throughput.test_bytes;
        tput["test_duration_ms"] = m.throughput.test_duration_ms;
    } else {
        tput["download_kbps"]    = nullptr;
        tput["test_bytes"]       = 0;
        tput["test_duration_ms"] = 0;
    }
    tput["success"]   = m.throughput.success;
    tput["http_code"] = m.throughput.http_code;

    size_t n = serializeJson(doc, buf, bufLen);
    return n;
}

/**
 * Serialise ScanResults into a JSON document.
 * Writes into @buf.  Returns bytes written (0 on error).
 */
static size_t buildScanJSON(const ScanResults &scan, char *buf, size_t bufLen) {
    JsonDocument doc;

    doc["probe_id"]        = PROBE_ID;
    doc["timestamp"]       = getISO8601Timestamp();
    doc["ap_count"]        = (int)scan.aps.size();
    doc["scan_duration_ms"]= scan.scan_duration_ms;

    JsonArray arr = doc["access_points"].to<JsonArray>();
    for (const ScanEntry &e : scan.aps) {
        JsonObject ap  = arr.add<JsonObject>();
        ap["ssid"]       = e.ssid;
        ap["bssid"]      = e.bssid;
        ap["channel"]    = e.channel;
        ap["rssi_dbm"]   = e.rssi_dbm;
        ap["encryption"] = e.encryption;
    }

    size_t n = serializeJson(doc, buf, bufLen);
    return n;
}

// ---------------------------------------------------------------------------
// Publish helpers
// ---------------------------------------------------------------------------

static void publishMetrics(const ProbeMetrics &m) {
    // 1 KB is more than enough for the metrics document.
    static char buf[1280];

    size_t n = buildMetricsJSON(m, buf, sizeof(buf));
    if (n == 0 || n >= sizeof(buf)) {
        Serial.printf("[Main] ✗ Metrics JSON overflow (n=%zu)\n", n);
        return;
    }

    char topic[64];
    snprintf(topic, sizeof(topic), "labwifimon/%s/metrics", PROBE_ID);

    g_ledMode = LedMode::DOUBLE_PULSE;
    bool ok = publishJSON(topic, buf);
    g_ledMode = LedMode::SLOW_BLINK;

    Serial.printf("[Main] %s metrics → %s  (%zu bytes)\n",
                  ok ? "✓" : "✗", topic, n);
}

static void publishScan(const ScanResults &scan) {
    // Up to 20 APs × ~110 chars + overhead = ~2.5 KB; use a heap buffer.
    const size_t BUF_SIZE = MQTT_BUFFER_SIZE - 64;  // leave room for header
    char *buf = static_cast<char*>(malloc(BUF_SIZE));
    if (!buf) {
        Serial.println("[Main] ✗ Out of heap for scan JSON");
        return;
    }

    size_t n = buildScanJSON(scan, buf, BUF_SIZE);
    if (n == 0 || n >= BUF_SIZE) {
        Serial.printf("[Main] ✗ Scan JSON overflow (n=%zu)\n", n);
        free(buf);
        return;
    }

    char topic[64];
    snprintf(topic, sizeof(topic), "labwifimon/%s/scan", PROBE_ID);

    bool ok = publishJSON(topic, buf);
    Serial.printf("[Main] %s scan → %s  (%zu bytes, %zu APs)\n",
                  ok ? "✓" : "✗", topic, n, scan.aps.size());

    free(buf);
}

// ---------------------------------------------------------------------------
// Deep sleep helper
// ---------------------------------------------------------------------------
#if ENABLE_DEEP_SLEEP
static void enterDeepSleep(uint32_t elapsedMs) {
    // We already spent elapsedMs awake; sleep for the remainder of the cycle.
    uint32_t remainMs = (elapsedMs < MEASUREMENT_INTERVAL_MS)
        ? (MEASUREMENT_INTERVAL_MS - elapsedMs)
        : 1000;   // minimum 1 s sleep to avoid tight boot loop

    Serial.printf("[Main] Deep sleeping for %u ms (%.1f s)\n",
                  remainMs, remainMs / 1000.0f);
    Serial.flush();

    // Gracefully disconnect so the broker gets the LWT
    publishStatus(false);
    mqttClient.disconnect();
    WiFi.disconnect(true);
    delay(100);

    esp_sleep_enable_timer_wakeup((uint64_t)remainMs * 1000ULL);
    esp_deep_sleep_start();
    // Does not return.
}
#endif

// ---------------------------------------------------------------------------
// setup()
// ---------------------------------------------------------------------------
void setup() {
    g_sketchStartMs = millis();

    Serial.begin(SERIAL_BAUD);
    // Give the host a moment to open its serial monitor before the banner.
    delay(200);

    Serial.println();
    Serial.println("╔══════════════════════════════════════╗");
    Serial.println("║  LabWiFiMon ESP32 Probe  v1.0        ║");
    Serial.println("╚══════════════════════════════════════╝");
    Serial.printf("  Probe ID : %s\n",  PROBE_ID);
    Serial.printf("  Location : %s\n",  PROBE_LOCATION);
    Serial.printf("  Boot#    : %u\n",  ++g_bootCount);
    Serial.printf("  Free heap: %u bytes\n", esp_get_free_heap_size());
    Serial.printf("  IDF      : %s\n",  esp_get_idf_version());
    Serial.println();

    // ---- Status LED --------------------------------------------------------
    pinMode(STATUS_LED_PIN, OUTPUT);
    ledOn();   // solid during initialisation

    // ---- Watchdog ----------------------------------------------------------
    setupWatchdog();

    // ---- WiFi --------------------------------------------------------------
    g_ledMode = LedMode::FAST_BLINK;
    ensureWiFi();
    if (WiFi.status() != WL_CONNECTED) {
        Serial.println("[Setup] WiFi failed — rebooting in 10 s");
        delay(10000);
        ESP.restart();
    }

    // ---- NTP ---------------------------------------------------------------
    // (also called by ensureWiFi on success, so this is mostly a no-op)
    if (!isTimeSynced()) syncNTP();

    // ---- MQTT --------------------------------------------------------------
    setupMQTT();
    ensureMQTT();

    // ---- Schedule first cycle immediately -----------------------------------
    // Setting g_last* = 0 makes the first `millis() - g_last*` comparison
    // in loop() exceed the interval, triggering an immediate measurement.
    g_lastMetricsMs   = millis() - MEASUREMENT_INTERVAL_MS;
    g_lastHeartbeatMs = millis() - HEARTBEAT_INTERVAL_MS;
    g_lastScanMs      = millis() - SCAN_INTERVAL_MS;

    g_ledMode = LedMode::SLOW_BLINK;
    Serial.println("[Setup] Initialisation complete — entering measurement loop");
    Serial.println();
}

// ---------------------------------------------------------------------------
// loop()
// ---------------------------------------------------------------------------
void loop() {
    esp_task_wdt_reset();

    unsigned long now = millis();

    // ---- Maintain connections -----------------------------------------------
    if (WiFi.status() != WL_CONNECTED) {
        g_ledMode = LedMode::FAST_BLINK;
        Serial.println("[Loop] WiFi lost — reconnecting");
        ensureWiFi();
        if (WiFi.status() == WL_CONNECTED) {
            g_ledMode = LedMode::SLOW_BLINK;
        }
        updateLed();
        delay(100);
        return;  // skip publishing until connected
    }

    if (!mqttClient.connected()) {
        ensureMQTT();
    }
    mqttClient.loop();  // process keep-alive / broker pings

    // ---- Heartbeat ----------------------------------------------------------
    if (now - g_lastHeartbeatMs >= HEARTBEAT_INTERVAL_MS) {
        g_lastHeartbeatMs = now;
        esp_task_wdt_reset();
        publishStatus(true);
    }

    // ---- Channel scan -------------------------------------------------------
    if (now - g_lastScanMs >= SCAN_INTERVAL_MS) {
        g_lastScanMs = now;
        esp_task_wdt_reset();
        Serial.println("[Loop] Starting channel scan...");

        ScanResults scan = performChannelScan(/*maxResults=*/20);
        esp_task_wdt_reset();

        if (scan.valid && mqttClient.connected()) {
            publishScan(scan);
        }
    }

    // ---- Full metrics collection --------------------------------------------
    if (now - g_lastMetricsMs >= MEASUREMENT_INTERVAL_MS) {
        g_lastMetricsMs = now;
        g_cycleCount++;

        Serial.printf("\n[Loop] ─── Cycle %u at %s ───\n",
                      g_cycleCount, getISO8601Timestamp().c_str());

        esp_task_wdt_reset();
        ProbeMetrics m = collectMetrics();
        esp_task_wdt_reset();

        if (mqttClient.connected()) {
            publishMetrics(m);
        } else {
            Serial.println("[Loop] MQTT disconnected — metrics not published");
        }

#if ENABLE_DEEP_SLEEP
        // In deep sleep mode we sleep for the remainder of the interval.
        enterDeepSleep(millis() - now);
        // Does not return.
#endif
    }

    updateLed();
    delay(10);   // small yield — keeps the WiFi stack happy between tasks
}
