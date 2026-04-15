#pragma once

// =============================================================================
// LabWiFiMon ESP32 Probe — Metric Collection Declarations
// =============================================================================

#include <Arduino.h>
#include <WiFi.h>
#include <vector>

// ---------------------------------------------------------------------------
// Data Structures
// ---------------------------------------------------------------------------

/** Info about the currently associated access point. */
struct WiFiInfo {
    String ssid;
    String bssid;        // "AA:BB:CC:DD:EE:FF"
    int    channel;
    int    rssi_dbm;
    bool   valid = false;
};

/**
 * Statistics derived from N individual pings to a single target.
 * jitter_ms is the population standard deviation of individual RTTs.
 */
struct PingStats {
    float avg_ms         = 0.0f;
    float min_ms         = 0.0f;
    float max_ms         = 0.0f;
    float jitter_ms      = 0.0f;   // stddev of RTTs
    float packet_loss_pct= 0.0f;
    bool  reachable      = false;
};

/** Latency results for both the local gateway and an external host. */
struct LatencyMetrics {
    PingStats  gateway;
    PingStats  external;
    IPAddress  gateway_ip;
    bool       gateway_ip_valid = false;
};

/** TCP download throughput measured via HTTP GET. */
struct ThroughputMetrics {
    float    download_kbps    = 0.0f;
    uint32_t test_bytes       = 0;
    uint32_t test_duration_ms = 0;
    int      http_code        = 0;
    bool     success          = false;
};

/** A single entry from a WiFi channel scan. */
struct ScanEntry {
    String ssid;         // "<hidden>" for hidden SSIDs
    String bssid;
    int    channel;
    int    rssi_dbm;
    String encryption;   // "Open", "WEP", "WPA", "WPA2", etc.
};

/** Results of a full 2.4 GHz channel scan. */
struct ScanResults {
    std::vector<ScanEntry> aps;               // sorted strongest→weakest
    uint32_t               scan_duration_ms;
    bool                   valid = false;
};

/** All metrics collected in one measurement cycle. */
struct ProbeMetrics {
    WiFiInfo          wifi;
    LatencyMetrics    latency;
    ThroughputMetrics throughput;
    uint32_t          uptime_s         = 0;
    uint32_t          free_heap_bytes  = 0;
    uint32_t          min_free_heap_bytes = 0;
};

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

/**
 * Collect a full set of probe metrics: WiFi info, ping latency to gateway
 * and external target, and TCP download throughput.
 *
 * Duration: ~10–20 s (dominated by PING_COUNT × 2 individual pings plus
 *           the HTTP throughput test).
 *
 * WiFi must be connected before calling; call wdtReset() externally around
 * long sub-operations if needed.
 */
ProbeMetrics collectMetrics();

/**
 * Perform a passive WiFi channel scan and return visible APs sorted by
 * signal strength (strongest first).
 *
 * Duration: ~2–4 s.  Causes a brief connectivity interruption (normal).
 *
 * @param maxResults  Cap on returned entries; excess (weakest) are discarded.
 */
ScanResults performChannelScan(uint8_t maxResults = 20);

/** Return info about the currently associated AP without scanning. */
WiFiInfo getCurrentWiFiInfo();

/** Human-readable string for a wifi_auth_mode_t value. */
const char* encryptionTypeStr(wifi_auth_mode_t enc);
