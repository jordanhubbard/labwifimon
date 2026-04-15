#pragma once

// =============================================================================
// LabWiFiMon ESP32 Probe — Network Management Declarations
// (WiFi • MQTT • NTP)
// =============================================================================

#include <Arduino.h>
#include <PubSubClient.h>

// ---------------------------------------------------------------------------
// MQTT client — defined in network.cpp, declared here so main.cpp can call
// mqttClient.loop() and publish helpers.
// ---------------------------------------------------------------------------
extern PubSubClient mqttClient;

// ---------------------------------------------------------------------------
// Initialisation  (call once from setup())
// ---------------------------------------------------------------------------

/** Configure the PubSubClient (server, port, buffer size, keep-alive). */
void setupMQTT();

// ---------------------------------------------------------------------------
// Connection management  (call from loop() before each publish cycle)
// ---------------------------------------------------------------------------

/**
 * Ensure WiFi is associated.  Blocks until connected or
 * WIFI_CONNECT_TIMEOUT_MS elapses, then returns so the caller can retry.
 * Triggers NTP re-sync on reconnection.
 */
void ensureWiFi();

/**
 * Ensure MQTT is connected.  Attempts a single reconnect with LWT attached
 * if the socket is down.  Returns immediately on failure so loop() can
 * retry next cycle.
 */
void ensureMQTT();

// ---------------------------------------------------------------------------
// NTP / Timestamps
// ---------------------------------------------------------------------------

/**
 * Block until the system clock is synchronised from NTP (or NTP_SYNC_TIMEOUT_S
 * seconds elapse).  Call once after WiFi is up.
 */
void syncNTP();

/** Return true if the system time has been synchronised (year ≥ 2024). */
bool isTimeSynced();

/**
 * Return the current UTC time as an ISO 8601 string:
 *   "2025-04-14T17:30:00Z"
 * Returns "1970-01-01T00:00:00Z" if NTP has not yet synchronised.
 */
String getISO8601Timestamp();

// ---------------------------------------------------------------------------
// MQTT Publish helpers
// ---------------------------------------------------------------------------

/**
 * Publish a null-terminated JSON string on @topic.
 * Automatically falls back to chunked publish (beginPublish / write /
 * endPublish) for payloads > 256 bytes so PubSubClient's internal buffer is
 * never the limiting factor.
 *
 * @param topic    Full MQTT topic string.
 * @param payload  Null-terminated JSON (or any text).
 * @param retain   Whether the broker should retain the last value.
 * @return true on success.
 */
bool publishJSON(const char *topic, const char *payload, bool retain = false);

/**
 * Publish the probe's online/offline status on
 *   labwifimon/<PROBE_ID>/status
 *
 * @param online  true → publish {"online":true,...}, false → {"online":false}
 */
void publishStatus(bool online);
