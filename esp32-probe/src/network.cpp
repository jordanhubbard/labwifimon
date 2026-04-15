// =============================================================================
// LabWiFiMon ESP32 Probe — Network Management Implementation
// =============================================================================

#include "network.h"
#include "config.h"

#include <WiFi.h>
#include <time.h>
#include <ArduinoJson.h>

// ---------------------------------------------------------------------------
// Module-private state
// ---------------------------------------------------------------------------

static WiFiClient    wifiClient;
       PubSubClient  mqttClient(wifiClient);   // extern in network.h

// MQTT topic roots pre-built to avoid repeated String allocation in hot paths.
static char topicMetrics[64];
static char topicScan[64];
static char topicStatus[64];

// Last MQTT reconnect attempt time (for simple back-off)
static unsigned long lastMqttReconnectAttempt = 0;
static const uint32_t MQTT_RECONNECT_INTERVAL_MS = 5000;

// ---------------------------------------------------------------------------
// MQTT callback  (inbound messages — not needed for a publish-only probe but
//                 required by PubSubClient)
// ---------------------------------------------------------------------------
static void onMqttMessage(char* /*topic*/, byte* /*payload*/, unsigned int /*len*/) {
    // No subscriptions at present; placeholder for future config-over-MQTT.
}

// ---------------------------------------------------------------------------
// Public: MQTT setup
// ---------------------------------------------------------------------------

void setupMQTT() {
    // Pre-build topic strings once
    snprintf(topicMetrics, sizeof(topicMetrics), "labwifimon/%s/metrics", PROBE_ID);
    snprintf(topicScan,    sizeof(topicScan),    "labwifimon/%s/scan",    PROBE_ID);
    snprintf(topicStatus,  sizeof(topicStatus),  "labwifimon/%s/status",  PROBE_ID);

    mqttClient.setServer(MQTT_SERVER, MQTT_PORT);
    mqttClient.setKeepAlive(MQTT_KEEPALIVE_S);
    mqttClient.setSocketTimeout(10);                 // TCP connect timeout (s)
    mqttClient.setBufferSize(MQTT_BUFFER_SIZE);
    mqttClient.setCallback(onMqttMessage);

    Serial.printf("[MQTT] Broker: %s:%d  topics: %s | %s | %s\n",
                  MQTT_SERVER, MQTT_PORT,
                  topicMetrics, topicScan, topicStatus);
}

// ---------------------------------------------------------------------------
// Public: WiFi connection management
// ---------------------------------------------------------------------------

void ensureWiFi() {
    if (WiFi.status() == WL_CONNECTED) return;

    Serial.printf("[WiFi] Connecting to \"%s\"", WIFI_SSID);
    WiFi.mode(WIFI_STA);
    WiFi.begin(WIFI_SSID, WIFI_PASSWORD);

    unsigned long start = millis();
    while (WiFi.status() != WL_CONNECTED) {
        if (millis() - start >= WIFI_CONNECT_TIMEOUT_MS) {
            Serial.println("\n[WiFi] ✗ Timeout — will retry next cycle");
            WiFi.disconnect(true);
            return;
        }
        delay(500);
        Serial.print(".");
    }

    Serial.printf("\n[WiFi] ✓ Connected\n"
                  "         IP      : %s\n"
                  "         Gateway : %s\n"
                  "         RSSI    : %d dBm\n"
                  "         Channel : %d\n",
                  WiFi.localIP().toString().c_str(),
                  WiFi.gatewayIP().toString().c_str(),
                  WiFi.RSSI(),
                  WiFi.channel());

    // Re-sync NTP whenever we (re-)associate — the clock may have drifted.
    syncNTP();
}

// ---------------------------------------------------------------------------
// Public: MQTT connection management
// ---------------------------------------------------------------------------

void ensureMQTT() {
    if (mqttClient.connected()) return;

    unsigned long now = millis();
    if (now - lastMqttReconnectAttempt < MQTT_RECONNECT_INTERVAL_MS) return;
    lastMqttReconnectAttempt = now;

    // Build a unique client ID: "labwifimon-<PROBE_ID>"
    char clientId[64];
    snprintf(clientId, sizeof(clientId), "labwifimon-%s", PROBE_ID);

    // Last-Will-and-Testament: broker marks the probe offline if it drops.
    const char *willPayload = "{\"online\":false}";

    Serial.printf("[MQTT] Connecting as \"%s\"... ", clientId);

    bool ok;
    if (strlen(MQTT_USERNAME) > 0) {
        ok = mqttClient.connect(clientId,
                                MQTT_USERNAME, MQTT_PASSWORD,
                                topicStatus, /*qos=*/0, /*retain=*/true,
                                willPayload);
    } else {
        ok = mqttClient.connect(clientId,
                                nullptr, nullptr,
                                topicStatus, /*qos=*/0, /*retain=*/true,
                                willPayload);
    }

    if (ok) {
        Serial.println("✓");
        // Immediately advertise online status
        publishStatus(true);
    } else {
        Serial.printf("✗ (rc=%d)\n", mqttClient.state());
        // rc codes: -4=timeout -3=denied -2=unavail -1=badproto 0=ok 1=badver
        // 2=badclient 3=unavail 4=badcredentials 5=unauthorized
    }
}

// ---------------------------------------------------------------------------
// Public: NTP
// ---------------------------------------------------------------------------

void syncNTP() {
    Serial.printf("[NTP] Syncing from %s / %s", NTP_SERVER_1, NTP_SERVER_2);
    // UTC offset = 0, DST offset = 0 — we store everything as UTC.
    configTime(0, 0, NTP_SERVER_1, NTP_SERVER_2);

    const unsigned long deadline = millis() + (NTP_SYNC_TIMEOUT_S * 1000UL);
    struct tm ti = {};
    while (!getLocalTime(&ti, 200) && millis() < deadline) {
        Serial.print(".");
    }

    if (isTimeSynced()) {
        char buf[32];
        strftime(buf, sizeof(buf), "%Y-%m-%dT%H:%M:%SZ", &ti);
        Serial.printf("\n[NTP] ✓ Synced: %s\n", buf);
    } else {
        Serial.println("\n[NTP] ✗ Sync failed — timestamps will show epoch");
    }
}

bool isTimeSynced() {
    time_t now = time(nullptr);
    // If time is before 2024-01-01, NTP has not synchronised yet.
    return (now > 1704067200UL);
}

String getISO8601Timestamp() {
    struct tm ti = {};
    if (!getLocalTime(&ti)) {
        return "1970-01-01T00:00:00Z";
    }
    char buf[25];
    strftime(buf, sizeof(buf), "%Y-%m-%dT%H:%M:%SZ", &ti);
    return String(buf);
}

// ---------------------------------------------------------------------------
// Public: MQTT publish helpers
// ---------------------------------------------------------------------------

bool publishJSON(const char *topic, const char *payload, bool retain) {
    size_t len = strlen(payload);

    // For payloads that fit inside the client buffer use the simple path.
    // PubSubClient reserves ~5 bytes for the fixed header, so the usable
    // payload window is MQTT_BUFFER_SIZE - 5 - topic_len.
    bool ok = false;
    if (mqttClient.beginPublish(topic, len, retain)) {
        size_t written = mqttClient.write(
            reinterpret_cast<const uint8_t *>(payload), len);
        ok = mqttClient.endPublish();
        if (written != len) ok = false;
    }

    if (!ok) {
        Serial.printf("[MQTT] ✗ Publish failed on topic: %s  len=%zu\n",
                      topic, len);
    }
    return ok;
}

void publishStatus(bool online) {
    // Build a compact status document.
    JsonDocument doc;
    doc["probe_id"]  = PROBE_ID;
    doc["online"]    = online;
    doc["timestamp"] = getISO8601Timestamp();
    if (online) {
        doc["ip"]       = WiFi.localIP().toString();
        doc["uptime_s"] = millis() / 1000;
        doc["rssi_dbm"] = WiFi.RSSI();
    }

    char buf[256];
    size_t n = serializeJson(doc, buf, sizeof(buf));
    if (n == 0) {
        Serial.println("[MQTT] ✗ Status JSON serialisation failed");
        return;
    }

    publishJSON(topicStatus, buf, /*retain=*/true);
    Serial.printf("[MQTT] Status → %s: online=%s\n",
                  topicStatus, online ? "true" : "false");
}
