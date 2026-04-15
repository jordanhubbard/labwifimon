// =============================================================================
// LabWiFiMon ESP32 Probe — Metric Collection Implementation
// =============================================================================

#include "metrics.h"
#include "config.h"

#include <ESP32Ping.h>
#include <HTTPClient.h>
#include <algorithm>
#include <cmath>

// ---------------------------------------------------------------------------
// Internal helpers
// ---------------------------------------------------------------------------

/**
 * Perform PING_COUNT individual one-ping-at-a-time measurements to @target
 * and return aggregate statistics.  Individual RTTs are stored so that jitter
 * (population stddev) can be computed without the library's fixed batch API.
 */
static PingStats measurePing(IPAddress target) {
    PingStats stats;
    float times[PING_COUNT];
    int   received = 0;

    Serial.printf("[Ping] → %s  (%d pings)\n",
                  target.toString().c_str(), PING_COUNT);

    for (int i = 0; i < PING_COUNT; i++) {
        if (Ping.ping(target, 1)) {
            times[received] = static_cast<float>(Ping.averageTime());
            received++;
            Serial.printf("[Ping]   #%02d  %.1f ms\n", i + 1, times[received - 1]);
        } else {
            Serial.printf("[Ping]   #%02d  timeout\n", i + 1);
        }
        if (i < PING_COUNT - 1) {
            delay(PING_INTERVAL_MS);
        }
    }

    stats.packet_loss_pct = 100.0f * (PING_COUNT - received) / PING_COUNT;
    stats.reachable       = (received > 0);

    if (received == 0) {
        Serial.printf("[Ping] ✗ %s unreachable (100%% loss)\n",
                      target.toString().c_str());
        return stats;
    }

    // ---- min / max / avg ----
    float sum  = 0.0f;
    float vmin = times[0];
    float vmax = times[0];

    for (int i = 0; i < received; i++) {
        sum  += times[i];
        if (times[i] < vmin) vmin = times[i];
        if (times[i] > vmax) vmax = times[i];
    }
    stats.avg_ms = sum / received;
    stats.min_ms = vmin;
    stats.max_ms = vmax;

    // ---- jitter = population stddev ----
    float varSum = 0.0f;
    for (int i = 0; i < received; i++) {
        float d = times[i] - stats.avg_ms;
        varSum += d * d;
    }
    stats.jitter_ms = sqrtf(varSum / received);

    Serial.printf("[Ping] ✓ avg=%.1f min=%.1f max=%.1f jitter=%.1f loss=%.0f%%\n",
                  stats.avg_ms, stats.min_ms, stats.max_ms,
                  stats.jitter_ms, stats.packet_loss_pct);
    return stats;
}

// ---------------------------------------------------------------------------

static ThroughputMetrics measureThroughput() {
    ThroughputMetrics result;

    if (strlen(THROUGHPUT_TEST_URL) == 0) {
        Serial.println("[Throughput] Disabled (THROUGHPUT_TEST_URL is empty)");
        return result;
    }

    Serial.printf("[Throughput] GET %s\n", THROUGHPUT_TEST_URL);

    HTTPClient http;
    http.begin(THROUGHPUT_TEST_URL);
    http.setTimeout(THROUGHPUT_HTTP_TIMEOUT_MS);
    http.addHeader("Connection", "close");
    // Disable redirect following — we expect a direct 200 OK.
    http.setFollowRedirects(HTTPC_DISABLE_FOLLOW_REDIRECTS);

    uint32_t t0 = millis();
    int httpCode = http.GET();
    result.http_code = httpCode;

    if (httpCode != HTTP_CODE_OK) {
        Serial.printf("[Throughput] ✗ HTTP %d\n", httpCode);
        http.end();
        return result;
    }

    // Stream and discard the body while counting bytes.
    WiFiClient *stream    = http.getStreamPtr();
    uint32_t    received  = 0;
    uint8_t     buf[512];
    const uint32_t deadline = t0 + THROUGHPUT_HTTP_TIMEOUT_MS;

    while ((stream->available() > 0 || http.connected()) &&
           millis() < deadline) {
        int avail = stream->available();
        if (avail > 0) {
            int n = stream->read(buf, min(avail, (int)sizeof(buf)));
            if (n > 0) received += static_cast<uint32_t>(n);
        } else {
            delay(1); // yield to WiFi stack while waiting for more data
        }
    }

    result.test_duration_ms = millis() - t0;
    result.test_bytes       = received;

    // bits received / ms elapsed  =  kbps
    result.download_kbps = (result.test_duration_ms > 0)
        ? (received * 8.0f / result.test_duration_ms)
        : 0.0f;
    result.success = (received > 0);

    http.end();

    Serial.printf("[Throughput] ✓ %u bytes / %u ms = %.1f kbps\n",
                  received, result.test_duration_ms, result.download_kbps);
    return result;
}

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

WiFiInfo getCurrentWiFiInfo() {
    WiFiInfo info;
    if (WiFi.status() != WL_CONNECTED) return info;

    info.ssid    = WiFi.SSID();
    info.channel = WiFi.channel();
    info.rssi_dbm = WiFi.RSSI();
    info.valid   = true;

    // Format BSSID as "AA:BB:CC:DD:EE:FF"
    uint8_t bssid[6];
    memcpy(bssid, WiFi.BSSID(), 6);
    char buf[18];
    snprintf(buf, sizeof(buf), "%02X:%02X:%02X:%02X:%02X:%02X",
             bssid[0], bssid[1], bssid[2], bssid[3], bssid[4], bssid[5]);
    info.bssid = String(buf);

    return info;
}

// ---------------------------------------------------------------------------

ProbeMetrics collectMetrics() {
    ProbeMetrics m;

    // ---- WiFi info ----------------------------------------------------------
    m.wifi = getCurrentWiFiInfo();
    Serial.printf("[Metrics] WiFi: %s  ch%d  %d dBm\n",
                  m.wifi.ssid.c_str(), m.wifi.channel, m.wifi.rssi_dbm);

    // ---- Resolve gateway IP -------------------------------------------------
    IPAddress gwIP;
    if (strlen(PING_TARGET_GATEWAY) > 0) {
        gwIP.fromString(PING_TARGET_GATEWAY);
        m.latency.gateway_ip_valid = true;
    } else {
        gwIP = WiFi.gatewayIP();
        m.latency.gateway_ip_valid = (gwIP != INADDR_NONE);
    }
    m.latency.gateway_ip = gwIP;

    // ---- Ping: gateway ------------------------------------------------------
    if (m.latency.gateway_ip_valid) {
        m.latency.gateway = measurePing(gwIP);
    } else {
        Serial.println("[Metrics] No gateway IP — skipping gateway ping");
    }

    // ---- Ping: external -----------------------------------------------------
    IPAddress extIP;
    extIP.fromString(PING_TARGET_EXTERNAL);
    m.latency.external = measurePing(extIP);

    // ---- TCP throughput -----------------------------------------------------
    m.throughput = measureThroughput();

    // ---- System info --------------------------------------------------------
    m.uptime_s            = millis() / 1000;
    m.free_heap_bytes     = esp_get_free_heap_size();
    m.min_free_heap_bytes = esp_get_minimum_free_heap_size();

    return m;
}

// ---------------------------------------------------------------------------

const char* encryptionTypeStr(wifi_auth_mode_t enc) {
    switch (enc) {
        case WIFI_AUTH_OPEN:            return "Open";
        case WIFI_AUTH_WEP:             return "WEP";
        case WIFI_AUTH_WPA_PSK:         return "WPA";
        case WIFI_AUTH_WPA2_PSK:        return "WPA2";
        case WIFI_AUTH_WPA_WPA2_PSK:    return "WPA/WPA2";
        case WIFI_AUTH_WPA2_ENTERPRISE: return "WPA2-Enterprise";
        case WIFI_AUTH_WPA3_PSK:        return "WPA3";
        case WIFI_AUTH_WPA2_WPA3_PSK:   return "WPA2/WPA3";
        default:                        return "Unknown";
    }
}

// ---------------------------------------------------------------------------

ScanResults performChannelScan(uint8_t maxResults) {
    ScanResults results;
    Serial.println("[Scan] Starting WiFi channel scan...");

    uint32_t t0 = millis();

    // Synchronous scan.  Passing (false, true) shows hidden networks.
    int found = WiFi.scanNetworks(/*async=*/false, /*show_hidden=*/true);
    results.scan_duration_ms = millis() - t0;

    if (found == WIFI_SCAN_FAILED || found < 0) {
        Serial.printf("[Scan] ✗ Failed (code %d)\n", found);
        return results;
    }

    Serial.printf("[Scan] ✓ Found %d APs in %u ms\n", found, results.scan_duration_ms);

    results.aps.reserve(min(found, (int)maxResults));

    for (int i = 0; i < found; i++) {
        ScanEntry e;
        e.ssid       = (WiFi.SSID(i).length() > 0) ? WiFi.SSID(i) : "<hidden>";
        e.channel    = WiFi.channel(i);
        e.rssi_dbm   = WiFi.RSSI(i);
        e.encryption = encryptionTypeStr(WiFi.encryptionType(i));

        // Format BSSID
        uint8_t *bssid = WiFi.BSSID(i);
        char buf[18];
        snprintf(buf, sizeof(buf), "%02X:%02X:%02X:%02X:%02X:%02X",
                 bssid[0], bssid[1], bssid[2], bssid[3], bssid[4], bssid[5]);
        e.bssid = String(buf);

        results.aps.push_back(e);
    }

    // Sort strongest → weakest RSSI
    std::sort(results.aps.begin(), results.aps.end(),
              [](const ScanEntry &a, const ScanEntry &b) {
                  return a.rssi_dbm > b.rssi_dbm;
              });

    // Trim to maxResults
    if (results.aps.size() > maxResults) {
        results.aps.resize(maxResults);
    }

    // Free scan memory
    WiFi.scanDelete();

    results.valid = true;
    return results;
}
