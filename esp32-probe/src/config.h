#pragma once

// =============================================================================
// LabWiFiMon ESP32 Probe — User Configuration
// =============================================================================
// Edit this file before flashing.  Every probe on the network must have a
// unique PROBE_ID so their MQTT topics don't collide.
// =============================================================================

// -----------------------------------------------------------------------------
// WiFi
// -----------------------------------------------------------------------------

#define WIFI_SSID               "YourWiFiSSID"
#define WIFI_PASSWORD           "YourWiFiPassword"

// How long to wait for a WiFi association before giving up and retrying (ms).
#define WIFI_CONNECT_TIMEOUT_MS 20000

// -----------------------------------------------------------------------------
// MQTT Broker  (typically the Raspberry Pi running Mosquitto)
// -----------------------------------------------------------------------------

#define MQTT_SERVER             "192.168.1.100"   // Pi IP or hostname
#define MQTT_PORT               1883
// Leave both empty if the broker has no authentication.
#define MQTT_USERNAME           ""
#define MQTT_PASSWORD           ""
// MQTT keep-alive sent to the broker (seconds).
#define MQTT_KEEPALIVE_S        60
// MQTT socket buffer — must hold the largest single publish (scan JSON).
// 4096 handles up to ~35 visible APs.
#define MQTT_BUFFER_SIZE        4096

// -----------------------------------------------------------------------------
// Probe Identity
// -----------------------------------------------------------------------------
// Used as the third segment of every MQTT topic this probe publishes on:
//   labwifimon/<PROBE_ID>/metrics
//   labwifimon/<PROBE_ID>/scan
//   labwifimon/<PROBE_ID>/status
//
// Allowed characters: a-z A-Z 0-9 hyphen underscore.  No spaces.
#define PROBE_ID                "probe-bench-1"

// Human-readable label (included in JSON payloads for dashboards).
#define PROBE_LOCATION          "Bench area"

// -----------------------------------------------------------------------------
// Ping / Latency Settings
// -----------------------------------------------------------------------------

// Gateway IP for local-network latency.
// Set to "" to auto-detect from DHCP (recommended), or hard-code, e.g.
// "192.168.1.1".
#define PING_TARGET_GATEWAY     ""

// Public host for internet latency / reachability.
#define PING_TARGET_EXTERNAL    "8.8.8.8"

// Pings sent per measurement cycle (average, min, max, jitter derived from
// these).  More pings → better statistics but slower cycle.
#define PING_COUNT              10

// Gap between individual pings (ms).  Keeps traffic polite.
#define PING_INTERVAL_MS        200

// Per-ping timeout (ms).
#define PING_TIMEOUT_MS         2000

// -----------------------------------------------------------------------------
// Measurement Intervals
// -----------------------------------------------------------------------------

// Full metrics collection + publish period (ms).
#define MEASUREMENT_INTERVAL_MS     30000   // 30 s

// Heartbeat / status publish period (ms).
#define HEARTBEAT_INTERVAL_MS       60000   // 60 s

// WiFi channel scan period (ms).  Scans are slower and briefly interrupt
// connectivity, so run them less often than core metrics.
#define SCAN_INTERVAL_MS            300000  // 5 min

// -----------------------------------------------------------------------------
// Throughput Test
// -----------------------------------------------------------------------------
// URL served by the Pi (or any reachable server) that delivers ~50 KB of
// arbitrary payload.  The labwifimon web server exposes this endpoint.
// Set to "" to skip throughput testing entirely.
#define THROUGHPUT_TEST_URL         "http://192.168.1.100:8080/test-payload"

// Expected response body size (bytes).  Used only for logging; the probe
// measures whatever bytes actually arrive.
#define THROUGHPUT_TEST_BYTES       51200   // 50 KB

// HTTP GET timeout (ms).
#define THROUGHPUT_HTTP_TIMEOUT_MS  10000

// -----------------------------------------------------------------------------
// Deep Sleep Mode
// -----------------------------------------------------------------------------
// When true the probe deep-sleeps between measurements — good for battery use.
// When false the probe stays awake and monitors continuously.
// Note: on each wake from deep sleep WiFi reconnects and NTP re-syncs, so the
//       effective cycle time is MEASUREMENT_INTERVAL_MS (not faster).
#define ENABLE_DEEP_SLEEP           false

// -----------------------------------------------------------------------------
// Hardware
// -----------------------------------------------------------------------------

// STATUS_LED_PIN is injected by platformio.ini via -D.  Define a fallback in
// case the project is opened outside PlatformIO.
#ifndef STATUS_LED_PIN
  #define STATUS_LED_PIN            2
#endif

// LED on = what logic level?  Most ESP32 dev boards: HIGH.
#define STATUS_LED_ACTIVE_HIGH      true

// -----------------------------------------------------------------------------
// Watchdog Timer
// -----------------------------------------------------------------------------
// The hardware task watchdog resets the probe if loop() stalls for this many
// seconds.  Must be > the worst-case loop duration (ping + scan + HTTP).
// A full cycle with 10 pings each target + 5-min scan + HTTP ≈ 30–40 s.
#define WDT_TIMEOUT_S               90

// -----------------------------------------------------------------------------
// NTP
// -----------------------------------------------------------------------------
#define NTP_SERVER_1                "pool.ntp.org"
#define NTP_SERVER_2                "time.nist.gov"
// Seconds to wait for first NTP sync before continuing anyway.
#define NTP_SYNC_TIMEOUT_S          30

// -----------------------------------------------------------------------------
// Serial Debug
// -----------------------------------------------------------------------------
#define SERIAL_BAUD                 115200
