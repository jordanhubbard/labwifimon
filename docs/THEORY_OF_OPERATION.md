# LabWiFiMon — Theory of Operation

## Table of Contents

1. [WiFi Signal Quality Fundamentals](#1-wifi-signal-quality-fundamentals)
2. [WiFi 7 (802.11be) and Modern Standards](#2-wifi-7-80211be-and-modern-standards)
3. [How LabWiFiMon Works](#3-how-labwifimon-works)
4. [Supported Hardware](#4-supported-hardware)
5. [Constructing the System](#5-constructing-the-system)
6. [Operating the System](#6-operating-the-system)
7. [Advanced Topics](#7-advanced-topics)

---

## 1. WiFi Signal Quality Fundamentals

### 1.1 The Physical Layer (802.11 at a Glance)

WiFi is a half-duplex radio system: only one transmitter on a given channel can usefully transmit at a time. The physical layer (PHY) translates bits into radio waves by:

1. **Modulating** a carrier frequency using schemes like BPSK, QPSK, 16-QAM, 64-QAM, 256-QAM, or 1024-QAM (each packing more bits per symbol at the cost of needing a cleaner signal).
2. **Spreading** the signal across many orthogonal subcarriers using OFDM (Orthogonal Frequency Division Multiplexing), which makes it robust against multipath reflections.
3. **Guarding** with a cyclic prefix — a copy of the end of a symbol prepended to its beginning — so that delayed multipath copies of the previous symbol do not corrupt the current one.

The channel medium is shared using CSMA/CA (Carrier Sense Multiple Access with Collision Avoidance): a station listens before transmitting and backs off randomly if the medium is busy. This is efficient at low utilization but degrades under contention.

Modern standards layer additional techniques on top:
- **MIMO** (Multiple Input Multiple Output): multiple antennas transmit independent spatial streams simultaneously.
- **MU-MIMO** (Multi-User MIMO): an AP serves multiple clients simultaneously on the same channel.
- **OFDMA** (Orthogonal Frequency Division Multiple Access, WiFi 6+): the channel is subdivided into Resource Units (RUs) so the AP can schedule multiple clients in the same transmission window.
- **Beamforming**: the AP focuses radio energy toward a specific client by adjusting per-antenna phase shifts, improving signal strength and reducing interference to others.

### 1.2 Management Frames

The 802.11 standard defines three frame classes: data, control, and management. Management frames handle the meta-protocol of joining and maintaining a WiFi network. Understanding them is useful because LabWiFiMon's advanced mode can passively capture them (requires monitor mode hardware — see Section 4).

| Frame Type | Direction | Purpose |
|---|---|---|
| **Beacon** | AP → all | Broadcast every ~102 ms. Announces SSID, supported rates, security config, HT/VHT/HE/EHT capabilities, TIM (Traffic Indication Map). |
| **Probe Request** | Client → all | Client actively scans for networks. May contain SSID (directed) or be wildcard. Reveals client capabilities. |
| **Probe Response** | AP → client | AP responds to probe with full capability advertisement. |
| **Authentication** | Client ↔ AP | Open system (trivial) or SAE (WPA3) handshake to establish identity. |
| **Association Request** | Client → AP | Client nominates itself to join, declares its capabilities (HT/VHT/HE/EHT, streams, rates). |
| **Association Response** | AP → client | AP accepts/rejects; assigns Association ID (AID). |
| **Disassociation / Deauth** | Either direction | Tears down association. Reason codes (e.g., 3 = leaving BSS, 15 = 4-way handshake timeout) are invaluable for debugging. |
| **Action** | Either direction | Carries sub-protocols: Block ACK setup, spectrum management, BSS Transition (802.11v), neighbor reports (802.11k), Fast BSS Transition (802.11r). |

Beacons are the heartbeat of the BSS (Basic Service Set). A client that misses too many beacons will consider itself disassociated. Beacon interval is typically 100 TU (Time Units; 1 TU = 1024 µs ≈ 1.024 ms), so beacons arrive roughly every 100 ms.

### 1.3 RSSI — What It Is, How It's Measured, and What It Means

**RSSI (Received Signal Strength Indicator)** is a logarithmic measure of the power of a received radio signal, expressed in dBm (decibels relative to 1 milliwatt).

It is measured by the WiFi chipset's RF front-end circuitry. On the ESP32, the driver exposes it via `esp_wifi_sta_get_ap_info()` which fills an `wifi_ap_record_t` struct containing an `rssi` field. On Linux, it is readable from `iwconfig`, `iw dev wlan0 link`, or `/proc/net/wireless`. Most platforms sample RSSI per-packet and average over a short window.

**Important caveats:**
- RSSI is not standardized across vendors. Two different chipsets may report different values for the same actual received power.
- It reflects the signal at the *receiver*, after antenna gain and cable/connector losses.
- It fluctuates rapidly due to multipath fading — the same location can vary ±5 dBm over seconds as people walk nearby or air circulates.
- It does not capture interference from other networks on the same or adjacent channels.

#### RSSI Reference Table

| RSSI (dBm) | Signal Quality | Typical Real-World Experience |
|---|---|---|
| > -50 | Excellent | Within a few meters of AP. Maximum throughput achievable. |
| -50 to -60 | Very Good | Strong signal, high data rates (5–6 GHz) reliable. |
| -60 to -70 | Good | Usable for all tasks including video streaming. Typical in a well-covered office. |
| -70 to -75 | Fair | Most browsing works, but high-throughput or latency-sensitive tasks may degrade. |
| -75 to -80 | Poor | Connections may drop, high retransmission rates, TCP throughput collapses. |
| -80 to -90 | Very Poor | Highly unreliable. Beacon reception marginal. |
| < -90 | Unusable | At or below noise floor on most chipsets. Frequent disconnects. |

The noise floor in a typical indoor environment is around -95 to -100 dBm. What matters is the **SNR (Signal-to-Noise Ratio)** — the difference between signal and noise. An RSSI of -70 dBm with a noise floor of -95 dBm gives 25 dB SNR, which is generally workable. The same -70 dBm in a noisy environment with a -75 dBm noise floor gives only 5 dB SNR, which is catastrophic.

### 1.4 Why RSSI Alone Is Insufficient

A common misconception: "the signal looks great (RSSI -55 dBm), so WiFi must be fine." This is wrong more often than people realize. RSSI measures received power but tells you nothing about:

**Latency** — The time for a packet to travel from client to destination and back (RTT). Even with perfect RSSI, latency can be high because of:
- AP CPU contention (many clients, heavy processing)
- Upstream congestion (the router's WAN link is full)
- **Bufferbloat** — oversized buffers in the AP or router cause queuing delays of hundreds of milliseconds. This is the single most common cause of "good signal, bad WiFi" complaints. An RSSI of -50 dBm with 300 ms latency is worse for video calls than -70 dBm with 15 ms latency.

**Jitter** — The variance in latency between successive packets. Even moderate average latency is tolerable if it is consistent. High jitter destroys voice and video quality. Jitter above ~20 ms is perceptible in voice calls; above 50 ms causes severe degradation.

**Packet Loss** — A fraction of packets that never arrive. WiFi uses link-layer retransmissions (up to 7 retries by default), so some RF errors are hidden from upper layers — but at the cost of latency spikes. When loss rate is high enough to exhaust retransmits, TCP detects it via timeout or duplicate ACKs and halves its congestion window, causing throughput collapse.

**Throughput** — The actual data rate achievable end-to-end. Even a strong signal on a congested AP shared with 30 other clients will yield poor throughput. Airtime is a shared resource.

**Channel Environment** — Whether neighboring APs are transmitting on the same or overlapping channels, and how many. LabWiFiMon's channel scan metric captures this.

The WiFi Health Score in LabWiFiMon combines all of these into a single 0–100 metric. See Section 3.5 for the scoring algorithm.

### 1.5 Channel Congestion

#### Channel Basics

WiFi channels are numbered frequency slots within a band. In 2.4 GHz, channels 1–14 exist (regulatory domain determines which are legal), each 20 MHz wide but spaced only 5 MHz apart — meaning adjacent channels *overlap* heavily in spectrum. In 5 GHz and 6 GHz, channels are spaced 20 MHz apart with no spectral overlap at 20 MHz width, and can be bonded to 40/80/160/320 MHz.

#### Co-Channel Interference (CCI)

When two APs operate on the same channel within range of each other, their transmissions are visible to each other's clients. CSMA/CA will cause both APs to back off when they sense the other transmitting — reducing effective throughput for both. This is the dominant problem in dense 2.4 GHz deployments (only 3 non-overlapping 20 MHz channels: 1, 6, 11).

#### Adjacent Channel Interference (ACI)

When APs operate on spectrally overlapping channels (e.g., channels 3 and 6 in 2.4 GHz), their signals bleed into each other's spectrum. Unlike CCI, ACI is *not* managed by CSMA/CA because the adjacent-channel signal is filtered out and does not trigger carrier sense — but it still raises the noise floor for the affected channel, degrading SNR and forcing the use of lower MCS rates.

#### Hidden Node Problem

Two clients can both hear the AP but not each other. Both believe the medium is idle and transmit simultaneously, causing a collision at the AP. Request-to-Send/Clear-to-Send (RTS/CTS) mitigates this but adds overhead.

#### Beacon Pollution

In dense environments (apartment buildings, conference centers), clients may hear dozens of APs. The client must parse all beacons even for networks it is not associated with, consuming CPU and creating visible noise in spectrum scans.

### 1.6 Band Comparison: 2.4 GHz vs 5 GHz vs 6 GHz

| Attribute | 2.4 GHz | 5 GHz | 6 GHz |
|---|---|---|---|
| Frequency range | 2.400–2.4835 GHz | 5.150–5.850 GHz | 5.925–7.125 GHz |
| Non-overlapping 20 MHz channels | 3 (1, 6, 11) | ~25 (country dependent) | 59 |
| Max channel width | 40 MHz | 160 MHz | 320 MHz (WiFi 7) |
| Typical indoor range | 30–50 m | 15–30 m | 10–20 m |
| Wall penetration | Good (lower freq) | Moderate | Poor (higher freq) |
| Legacy device support | Universal | Good | WiFi 6E+ only |
| Congestion in typical office | Very high | Moderate | Very low (new band) |
| DFS channels (5 GHz) | N/A | Many — require radar detection, can cause AP channel switches | N/A |

**Key insight**: 6 GHz is currently the least congested band because only WiFi 6E and later devices can use it, and the band has nearly 20x more non-overlapping channel space than 2.4 GHz. This is the primary driver behind WiFi 6E/7 adoption in professional environments.

---

## 2. WiFi 7 (802.11be) and Modern Standards

### 2.1 What WiFi 7 Brings

WiFi 7 (IEEE 802.11be, finalized 2024) is not an incremental update — it is a fundamental redesign of how client devices interact with the radio medium. Key innovations:

#### Multi-Link Operation (MLO)

The headline feature of WiFi 7. A single logical connection between a client and AP can simultaneously use *multiple* radio links across different bands/channels. For example, a WiFi 7 laptop might simultaneously maintain an active link on 2.4 GHz channel 6, 5 GHz channel 100, and 6 GHz channel 37 — all treated as one logical interface by the OS.

MLO modes:
- **EMLSR** (Enhanced Multi-Link Single Radio): device has one radio, switches quickly between links. Saves power, provides link diversity.
- **EMLMR** (Enhanced Multi-Link Multi-Radio): device has multiple active radios, can transmit/receive on multiple links simultaneously. Maximum throughput.
- **STR** (Simultaneous Transmit and Receive): different links can simultaneously be in TX and RX state.

The AP (called a **MLD** — Multi-Link Device) presents a single BSSID to clients but coordinates traffic across all affiliated APs (one per band). This is transparent to layer 3 and above.

#### 320 MHz Channels

WiFi 7 supports 320 MHz channel bonding in the 6 GHz band (the only band with enough contiguous spectrum). This doubles the maximum channel width versus WiFi 6E's 160 MHz. A single 320 MHz channel in 6 GHz can theoretically deliver ~46 Gbps peak PHY rate (with 4x4 MIMO and 4096-QAM).

#### 4096-QAM (12-bit Symbols)

Previous maximum was 1024-QAM (WiFi 6/6E). 4096-QAM packs 12 bits per symbol versus 10, a 20% increase in spectral efficiency. This requires very high SNR (>40 dB) — only achievable in close proximity to the AP — but when conditions allow, it meaningfully increases peak throughput.

#### Preamble Puncturing

In 80/160/320 MHz channels, individual 20 MHz sub-channels can be "punctured" (excluded from a transmission) if they are occupied by a radar signal or another transmitter. Previously, a busy sub-channel would prevent use of the entire bonded channel. With preamble puncturing, the AP continues using the available sub-channels while skipping the occupied one. This dramatically improves spectrum utilization in real-world deployments.

#### Multi-RU

WiFi 7 clients can be assigned multiple non-contiguous Resource Units within an OFDMA transmission, allowing more flexible spectrum allocation.

### 2.2 How WiFi 7 Improves Latency and Reliability

The latency improvements in WiFi 7 come from several mechanisms:

**Reduced retransmission rate** via MLO: if one link is congested or experiencing interference, traffic can flow over an alternate link. This reduces queuing delay and loss events.

**Harq-lite** (Hybrid ARQ, optional): partial retransmission of corrupted packets rather than full retransmission.

**Preamble puncturing** means the AP can transmit in a wide channel more often (fewer deferred transmissions waiting for a sub-channel to clear), reducing queuing delay.

**OFDMA scheduling** (inherited from WiFi 6) allows the AP to serve latency-sensitive traffic (VoIP, gaming) in dedicated RUs with guaranteed scheduling, rather than competing with bulk transfers in contention-based access.

**Target Wake Time (TWT)** (inherited from WiFi 6, enhanced in 7): the AP and client negotiate specific wake windows, reducing contention during the client's active windows and allowing predictable latency for IoT and real-time applications.

### 2.3 L4S and Its Relationship to WiFi

**L4S (Low Latency Low Loss Scalable Throughput)** is an IETF-standardized end-to-end architecture (RFC 9330 and related RFCs) that fundamentally changes how congestion control works across the internet, including within WiFi networks.

Traditional congestion control (Reno, CUBIC) works by filling the network buffer until packets are dropped, then backing off. This creates the bufferbloat problem: large buffers cause latency spikes of hundreds of milliseconds even on uncongested links. L4S solves this with two components:

1. **Scalable congestion control** (e.g., TCP Prague, QUIC with L4S): sends at a very fine-grained rate, reacting to tiny congestion signals rather than waiting for loss.
2. **Active Queue Management with ECN** (e.g., DualPI2 AQM): the router/AP marks packets with ECN (Explicit Congestion Notification) at very low queue occupancy (target: 1 ms of standing queue), giving senders an early signal before queues build.

**Umber Networks' Fi-Wi Architecture**: Umber Networks applied L4S principles specifically to the WiFi access layer. Their Fi-Wi architecture integrates DualPI2 AQM into the WiFi driver's transmission queue, so that per-flow latency budgets are enforced at the radio scheduler level. This means a Zoom call and a large file upload can share the same WiFi AP with the Zoom call maintaining <10 ms latency while the file transfer saturates the remaining capacity — a property called *Scalable Throughput* (the "S" in L4S).

**Why this matters for LabWiFiMon**: The presence or absence of L4S-aware AQM is directly measurable — compare latency under load vs. latency idle. An AP with proper AQM shows near-idle latency even at 95% utilization. A bufferbloated AP shows 200–500 ms latency under load. LabWiFiMon's latency metric captures this, and the "under load" throughput test is specifically designed to detect bufferbloat by measuring latency *during* the throughput test.

### 2.4 Why Monitoring Is More Critical with WiFi 7

Counterintuitively, wider channels and more complex protocols make monitoring *more* important, not less:

- **320 MHz channels in 6 GHz** cover a huge swath of spectrum. If a single interferer appears in any 20 MHz sub-band, preamble puncturing may degrade throughput in a way that is invisible to a user who only checks RSSI.
- **MLO adds complexity**: a connection might look healthy on one link while degraded on another. Per-link metrics (not yet exposed by all drivers) are needed to understand what is happening.
- **6 GHz band is new**: enterprise deployments mixing 6 GHz-capable and 6 GHz-incapable clients may see unexpected band steering behavior. Probes at different tiers (ESP32 vs Pi + WiFi 7 card) can reveal band steering decisions.
- **Higher MCS rates are sensitive to SNR**: the step from 1024-QAM to 4096-QAM requires ~5 dB more SNR. Small RSSI changes that were previously benign can now cause rate adaptation to drop from MCS 13 to MCS 11, cutting throughput by 20%. Throughput monitoring catches this; RSSI alone does not.
- **Denser AP deployments** (required for 6 GHz coverage) mean more co-channel and adjacent channel interactions. A channel scan that was benign last month may now show new neighbors after a building renovation.

---

## 3. How LabWiFiMon Works

### 3.1 Architecture Overview

```
 ┌─────────────────────────────────────────────────────────────────────────┐
 │                        MONITORED WiFi NETWORK                           │
 │                                                                          │
 │   ┌──────────┐   ┌──────────┐   ┌──────────┐   ┌──────────────────┐    │
 │   │ ESP32    │   │ ESP32-S3 │   │ Pi 4 +   │   │ Pi 5 + BE200     │    │
 │   │ Probe 1  │   │ Probe 2  │   │ Onboard  │   │ WiFi 7 Probe     │    │
 │   │(WiFi 4)  │   │(WiFi 4)  │   │(WiFi 5)  │   │(WiFi 7, 6 GHz)  │    │
 │   └────┬─────┘   └────┬─────┘   └────┬─────┘   └────────┬─────────┘    │
 │        │              │              │                   │              │
 │        └──────────────┴──────────────┴───────────────────┘              │
 │                                     │                                   │
 │                              MQTT (port 1883)                           │
 └─────────────────────────────────────┼───────────────────────────────────┘
                                       │
 ┌─────────────────────────────────────┼───────────────────────────────────┐
 │                      RASPBERRY PI SERVER STACK                          │
 │                                     │                                   │
 │                            ┌────────▼────────┐                          │
 │                            │   Mosquitto     │                          │
 │                            │   MQTT Broker   │                          │
 │                            └────────┬────────┘                          │
 │                                     │                                   │
 │                            ┌────────▼────────┐                          │
 │                            │    Telegraf     │ ◄── telegraf.conf:        │
 │                            │  (MQTT input    │     mqtt_consumer,        │
 │                            │   plugin)       │     influxdb_v2 output    │
 │                            └────────┬────────┘                          │
 │                                     │                                   │
 │                            ┌────────▼────────┐                          │
 │                            │   InfluxDB 2    │                          │
 │                            │  (Time-Series   │                          │
 │                            │   Database)     │                          │
 │                            └────┬───────┬────┘                          │
 │                                 │       │                               │
 │                    ┌────────────▼─┐   ┌─▼────────────────┐             │
 │                    │   Grafana    │   │   Web UI         │             │
 │                    │  (Dashboards │   │  (Flask + WS     │             │
 │                    │   & Alerts)  │   │   Real-time)      │             │
 │                    └─────────────┘   └──────────────────┘             │
 └─────────────────────────────────────────────────────────────────────────┘
```

All server components run as Docker containers, orchestrated by Docker Compose, on the Raspberry Pi. Probes connect to the monitored WiFi network and publish metrics independently — there is no back-channel from the server to the probes during normal operation.

### 3.2 The ESP32 Probe: What It Measures and How

Each probe runs a PlatformIO/Arduino firmware that cycles through four measurement phases:

#### Phase 1: RSSI and Connection State
```
esp_wifi_sta_get_ap_info(&ap_info)
→ ap_info.rssi  (dBm, signed int8)
→ ap_info.primary  (channel number)
→ ap_info.second   (HT40 secondary channel offset)
→ ap_info.phy_11n / phy_11b / phy_11g  (PHY capabilities of AP)
```
The ESP32 WiFi driver updates this internally per received beacon/data frame. The probe samples it at measurement time — no additional RF activity required.

#### Phase 2: Latency, Jitter, and Packet Loss (ICMP Ping)
The probe sends a configurable burst of ICMP Echo Requests to a fixed target (typically the default gateway or a known-good host on the local network). For each burst:
- **Latency** = round-trip time of each echo (µs resolution via `esp_timer_get_time()`)
- **Jitter** = mean absolute deviation of successive RTT samples: `jitter = mean(|RTT[i] - RTT[i-1]|)`
- **Packet Loss** = (sent - received) / sent × 100%

Default burst: 10 packets, 100 ms interval. This is configurable in `probe_config.h`.

The target is intentionally on the *local network* (not the internet), so results reflect WiFi link quality rather than upstream internet conditions.

#### Phase 3: Throughput (HTTP GET)
The probe performs an HTTP GET request to the server's built-in speed test endpoint, fetching a fixed-size payload (default: 512 KB). Throughput is calculated as:
```
throughput_mbps = (payload_bytes * 8) / transfer_time_us
```
This is a *downstream* (AP → probe) throughput measurement. It stresses the radio link and also serves as a bufferbloat detector: if latency (measured in Phase 2) is significantly higher than idle latency, bufferbloat is present in the AP or router.

#### Phase 4: Channel Scan (Passive)
The probe temporarily disconnects from its AP and performs a passive channel scan:
```
esp_wifi_scan_start(&scan_config, true)  // blocking
esp_wifi_scan_get_ap_records(&count, ap_records)
```
For each detected AP, the probe records:
- SSID, BSSID, channel, RSSI, security type, PHY mode

This yields a snapshot of the RF environment visible from the probe's physical location. The number of APs visible on the probe's primary channel is a direct measure of co-channel interference potential. After the scan, the probe reconnects.

**Note**: the channel scan interrupts connectivity for ~2–5 seconds. It runs infrequently (default: every 10 minutes) to minimize disruption.

### 3.3 MQTT Message Flow

Each measurement phase publishes a JSON payload to a structured MQTT topic:

```
labwifimon/<probe_id>/rssi
labwifimon/<probe_id>/ping
labwifimon/<probe_id>/throughput
labwifimon/<probe_id>/scan
labwifimon/<probe_id>/status
```

Example `ping` payload:
```json
{
  "probe_id": "esp32-lab-east",
  "timestamp": 1713100800,
  "target": "192.168.1.1",
  "samples": 10,
  "rtt_min_ms": 2.1,
  "rtt_avg_ms": 3.4,
  "rtt_max_ms": 8.7,
  "jitter_ms": 1.2,
  "loss_pct": 0.0,
  "rssi_dbm": -62
}
```

The `probe_id` is set once in flash via the firmware configuration. The timestamp is obtained via SNTP (Simple Network Time Protocol) synchronized at boot.

Mosquitto is configured with no authentication for simplicity in a lab environment (see Security Note in Section 6).

### 3.4 Data Pipeline: MQTT → Telegraf → InfluxDB → Grafana/Web UI

**Telegraf** subscribes to `labwifimon/#` (all probe topics) via its `mqtt_consumer` input plugin. The plugin configuration specifies `data_format = "json"` with a `json_query` to flatten nested objects. Each JSON field becomes a field in a Telegraf measurement.

Telegraf writes to **InfluxDB 2** via the `influxdb_v2` output plugin, using a token-based authentication to a bucket named `labwifimon`. The measurement name is derived from the MQTT topic suffix (e.g., `rssi`, `ping`, `throughput`).

**InfluxDB 2** stores all data with nanosecond-precision timestamps in its TSM (Time-Structured Merge tree) storage engine. Flux queries are used for all data retrieval. A retention policy (default: 30 days) automatically purges old data.

**Grafana** connects to InfluxDB 2 as a data source and provides the analytics dashboards. Grafana is pre-provisioned via `grafana/provisioning/` configuration files in the repo, so dashboards appear automatically on first start.

**The Web UI** (Flask backend + WebSocket) queries InfluxDB 2 directly using the Python `influxdb-client` library. It pushes updates to connected browsers via WebSocket every 5 seconds (configurable).

### 3.5 Web UI Real-Time Visualization

The Web UI is the "sparkly" face of LabWiFiMon — designed for a lab monitor screen showing live WiFi health at a glance.

**Architecture:**
```
Browser (HTML5/CSS3/JS)
    │
    ├── WebSocket (ws://pi-server:8080/ws)
    │       ↕ JSON metric updates every 5s
    │
    └── Flask/FastAPI Backend
            │
            └── InfluxDB 2 (Flux queries)
```

**Visual Components:**

- **Signal Strength Gauges**: Animated arc gauges per probe, color-coded green/yellow/orange/red based on RSSI thresholds.
- **Latency Sparklines**: 60-point rolling line charts showing RTT history. The line color transitions from green to red as latency increases.
- **Particle Effects**: Floating particles drift across a probe's card visualization. Particle density, speed, and color encode the WiFi Health Score:
  - Dense, fast, blue/white particles = excellent health
  - Sparse, slow, amber particles = degraded
  - Rare, red particles with irregular movement = poor
- **Channel Heatmap**: Grid showing channel utilization across 2.4/5/6 GHz bands, updated from scan data.
- **Health Score Badge**: Large numeric 0–100 score with a pulsing glow effect. Score color: green (>80), yellow (60–80), orange (40–60), red (<40).

### 3.6 WiFi Health Score Algorithm

The Health Score is a weighted composite of five normalized sub-scores, each 0–100:

```
Score_RSSI       = clamp((rssi_dbm + 90) / 40 * 100, 0, 100)
                   # -90 dBm → 0, -50 dBm → 100

Score_Latency    = clamp(100 - (rtt_avg_ms / 50 * 100), 0, 100)
                   # 0 ms → 100, 50+ ms → 0

Score_Jitter     = clamp(100 - (jitter_ms / 20 * 100), 0, 100)
                   # 0 ms → 100, 20+ ms → 0

Score_Loss       = clamp(100 - (loss_pct * 10), 0, 100)
                   # 0% → 100, 10%+ → 0

Score_Throughput = clamp(throughput_mbps / target_mbps * 100, 0, 100)
                   # target_mbps is configurable (default: 50 Mbps for ESP32)
```

Weighted combination:
```
Health_Score = (
    Score_RSSI       * 0.20 +
    Score_Latency    * 0.35 +
    Score_Jitter     * 0.20 +
    Score_Loss       * 0.15 +
    Score_Throughput * 0.10
)
```

**Rationale for weights**: Latency is weighted most heavily (0.35) because it is the most user-perceptible quality attribute in modern lab environments (video calls, remote desktop, interactive tools). RSSI gets only 0.20 because, as discussed in Section 1.4, it is a poor proxy for actual quality. Throughput gets the lowest weight (0.10) because lab networks are rarely throughput-constrained — latency and jitter are the limiting factors for interactive use.

The weights are configurable in `web-ui/config.py`.

---

## 4. Supported Hardware

LabWiFiMon is designed to run on a spectrum of hardware, from a single $5 ESP32 to a Raspberry Pi 5 with a WiFi 7 card. More capable hardware unlocks more measurement types.

### 4.1 Probe Tiers

#### Tier 1: ESP32 / ESP32-S3 (WiFi 4 / WiFi 5 on S3)

- **WiFi standard**: 802.11n (WiFi 4) on ESP32; 802.11n + partial 802.11ac on ESP32-S3
- **Bands**: 2.4 GHz only
- **Cost**: $3–$8 (bare module); $10–$20 (dev board with USB)
- **Power**: 5V USB, ~250 mA peak (WiFi TX), <20 µA deep sleep
- **Strengths**: Cheapest, battery-operable with deep sleep, widely available, LabWiFiMon firmware is battle-tested on this platform
- **Limitations**: 2.4 GHz only; cannot see 5/6 GHz; no monitor mode; limited CPU for complex analysis
- **Recommended board**: ESP32-DevKitC, WEMOS D32 Pro, or Seeed Studio XIAO ESP32-S3

#### Tier 2: Raspberry Pi with Onboard WiFi

- **WiFi standard**: 802.11ac (WiFi 5) on Pi 4/5
- **Bands**: 2.4 GHz + 5 GHz (dual-band)
- **Cost**: $35–$80 (Pi board) — likely already available in most labs
- **Strengths**: Can run the full LabWiFiMon server stack *and* be a probe simultaneously; Python-based probe firmware; full Linux OS for advanced diagnostics
- **Limitations**: No 6 GHz; no WiFi 7; no monitor mode without patched drivers; onboard antenna is mediocre
- **Notes**: Run the probe as a systemd service on the same Pi as the server stack, pointing metrics at localhost. Use a separate network namespace if measuring isolation is needed.

#### Tier 3: Raspberry Pi + PCIe M.2 HAT + WiFi 6E/7 Card

This is the most capable purpose-built probe configuration. A Pi 5 (the only Pi with PCIe) with an M.2 E-Key HAT provides a PCIe slot that accepts standard laptop WiFi cards.

**Recommended HATs:**

| HAT | PCIe Gen | M.2 Key | Price |
|---|---|---|---|
| Waveshare PCIe to M.2 HAT+ | Gen 2 x1 | E-Key (2230) | $8–$15 |
| Pineberry HatDrive | Gen 2/3 x1 | E-Key (2230) | $15–$25 |

**Recommended WiFi Cards (M.2 2230 E-Key):**

| Card | Standard | Bands | Notable Features | Price |
|---|---|---|---|---|
| Intel AX210 | WiFi 6E | 2.4 + 5 + 6 GHz | Good Linux support, Bluetooth 5.2 | ~$15 |
| Intel BE200 | WiFi 7 | 2.4 + 5 + 6 GHz | Full WiFi 7 incl. MLO, Bluetooth 5.4 | ~$20 |
| MediaTek MT7925 | WiFi 7 | 2.4 + 5 + 6 GHz | Strong open-source driver (mt7921/mt7925) | ~$25 |

**Driver requirements:**
- Intel AX210: `iwlwifi` driver, requires Linux 5.10+ firmware
- Intel BE200: `iwlwifi` driver, requires Linux 6.5+ and `linux-firmware` ≥ 2023-12-01
- MediaTek MT7925: `mt7921` / `mt7925` driver, mainlined in Linux 6.5+
- Raspberry Pi OS (Bookworm, 64-bit) ships Linux 6.6, which satisfies all of the above

**Assembly notes:**
1. Pi 5 must be powered with a 27W USB-C PD supply — the WiFi card draws significant power.
2. The HAT connects via the Pi 5's PCIe FFC connector (40-pin flat flex, underneath the board).
3. Set PCIe Gen 2 explicitly in `config.txt`: `dtparam=pciex1_gen=2` (Gen 3 is electrically marginal with some HATs).
4. An external antenna (U.FL/IPEX connector to SMA) significantly improves 6 GHz performance.

#### Tier 4: Linux Laptop or Mini-PC with M.2 WiFi 7 Card

The most capable probe tier. A laptop or mini-PC (Intel NUC, Beelink SER, etc.) with an M.2 2230 slot can accept the same BE200/MT7925 cards as Tier 3. Advantages:

- **Monitor mode**: With the right driver build, the AX210 and MT7925 support monitor mode, enabling passive capture of management frames (beacons, probe requests, association frames, deauth reason codes).
- **802.11be sniffer**: Monitor mode on a WiFi 7 card can capture 802.11be frames including MLO fields.
- **Wired uplink**: Connect the laptop via Ethernet to the server network while the WiFi card monitors, completely isolating monitoring traffic from the monitored network.
- **`iw` tooling**: Full access to `iw dev wlan0 scan`, `iw dev wlan0 station dump`, `iw dev wlan0 survey dump`, `iw phy phy0 info` for detailed per-band, per-channel statistics.

### 4.2 Capabilities by Tier

| Capability | Tier 1 (ESP32) | Tier 2 (Pi Onboard) | Tier 3 (Pi + M.2) | Tier 4 (Laptop) |
|---|---|---|---|---|
| RSSI measurement | Yes (2.4 GHz) | Yes (2.4 + 5 GHz) | Yes (2.4 + 5 + 6 GHz) | Yes (all bands) |
| Latency / Jitter / Loss | Yes | Yes | Yes | Yes |
| TCP Throughput | Yes (~50 Mbps) | Yes (~300 Mbps) | Yes (~1+ Gbps) | Yes (~multi-Gbps) |
| Channel scan (passive) | Yes (2.4 GHz) | Yes (2.4 + 5 GHz) | Yes (all bands) | Yes (all bands) |
| 5 GHz monitoring | No | Yes | Yes | Yes |
| 6 GHz monitoring | No | No | Yes (6E card) | Yes |
| WiFi 7 / MLO metrics | No | No | Yes (BE200/MT7925) | Yes |
| Monitor mode | No | No | Partial | Yes (AX210/MT7925) |
| Management frame capture | No | No | No | Yes |
| Deep sleep / battery | Yes | No | No | No |
| Embedded (no OS) | Yes | No | No | No |

---

## 5. Constructing the System

### 5.1 Bill of Materials

#### Minimum Configuration (1× ESP32 probe + Pi server)

| Item | Approx. Price | Notes |
|---|---|---|
| Raspberry Pi 4 or 5 (2GB+ RAM) | $35–$80 | Server + optional Tier 2 probe |
| Pi power supply (USB-C, 5V/3A for Pi 4; 27W PD for Pi 5) | $10–$15 | |
| MicroSD card, 32GB+ (Class 10 / A2) | $8–$15 | |
| ESP32-DevKitC or similar | $8–$15 | Probe node |
| USB cable (Micro-B or USB-C per board) | $5 | Probe power |
| Short Ethernet cable | $5 | Pi server uplink |
| **Total** | **~$70–$130** | |

#### Enhanced Configuration (add WiFi 7 probe)

| Item | Approx. Price | Notes |
|---|---|---|
| Raspberry Pi 5 (4GB) | $60 | Required for PCIe |
| Waveshare PCIe M.2 HAT+ | $12 | |
| Intel BE200 WiFi 7 card | $20 | |
| 27W USB-C PD supply | $15 | |
| U.FL to SMA pigtail (optional) | $5 | Better 6 GHz antenna |
| External 2.4/5/6 GHz antenna (optional) | $10 | |
| **Subtotal (probe only)** | **~$120** | |

### 5.2 Physical Assembly

**ESP32 Probe**: No assembly required for dev boards. Mount in a plastic enclosure if desired. Route the USB cable to a wall adapter or USB power bank.

**Pi + M.2 HAT (Tier 3 Probe):**
1. Install Pi OS Bookworm 64-bit on the microSD card.
2. Power off. Connect the M.2 HAT to the Pi 5's PCIe FFC connector — handle the flat flex cable gently, it is fragile.
3. Insert the WiFi card into the M.2 slot and secure with the retention screw.
4. If using an external antenna, connect the U.FL pigtail to the card's antenna port before securing the card.
5. Edit `/boot/firmware/config.txt`, add: `dtparam=pciex1_gen=2`
6. Boot and verify: `lspci` should show the WiFi card; `ip link` should show a new wireless interface.

### 5.3 Probe Placement Strategy

The goal is to characterize WiFi quality at the *locations that matter* — where people work — not just near the APs.

**Recommendations:**
- Place probes at workstation height (desk level), not ceiling or floor.
- At least one probe in each distinct room or zone.
- If the lab has known problem areas (far corner, glass wall, elevator lobby), place a probe there specifically.
- Place one probe *near* each AP as a baseline reference (this should always show excellent metrics; if it doesn't, the AP itself may be the problem).
- Avoid placing probes directly behind large metal objects (server racks, whiteboards) — these create measurement shadows, not representative of user experience.
- For multi-floor labs, at least one probe per floor.

**Coverage assessment**: After deploying, compare Health Scores across probes during a known-good period. Probes showing consistently lower scores identify coverage gaps that warrant either AP repositioning, added APs, or beam-steering investigation.

### 5.4 Network Diagram

```
  INTERNET
      │
  ┌───┴────────────────────────────────────────┐
  │  ROUTER / FIREWALL                          │
  │  (e.g., pfSense, UniFi, consumer router)   │
  └───┬────────────────────────────────────────┘
      │  LAN (192.168.1.0/24)
      │
  ┌───┴──────────────────────────────────────────────┐
  │ MANAGED SWITCH                                    │
  │                                                   │
  │  ┌───────┐  ┌────────────┐  ┌──────────────────┐ │
  │  │ AP 1  │  │  AP 2      │  │ Pi Server        │ │
  │  │(5 GHz)│  │(5+6 GHz)   │  │192.168.1.10      │ │
  │  └───────┘  └────────────┘  │Docker: Mosquitto │ │
  │      │           │          │       Telegraf   │ │
  │  WiFi│       WiFi│          │       InfluxDB   │ │
  │      └─────┬─────┘          │       Grafana    │ │
  │            │                │       Web UI     │ │
  │   ┌────────┴──────┐         └──────────────────┘ │
  │   │  ESP32 Probes │                               │
  │   │  (via WiFi)   │                               │
  │   └───────────────┘                               │
  └──────────────────────────────────────────────────┘
```

### 5.5 Power Options

| Option | Suitable For | Notes |
|---|---|---|
| USB wall adapter | Fixed probe location | Simplest. Requires outlet proximity. |
| USB power bank | Temporary deployments | 10,000 mAh bank → ~48h for ESP32. |
| PoE via HAT (Pi) | Pi probes near PoE switch | Requires PoE HAT and PoE-capable switch port. |
| Deep sleep + LiPo (ESP32) | Remote / battery-only locations | ESP32 wakes, measures, publishes, sleeps. Average current ~500 µA → months on 2000 mAh LiPo. |

---

## 6. Operating the System

### 6.1 Starting the Server Stack

```bash
git clone https://github.com/your-org/labwifimon.git
cd labwifimon/pi-server

# Configure environment
cp .env.example .env
nano .env  # Set INFLUXDB_TOKEN, ORG, BUCKET

# Start all services
docker compose up -d

# Verify
docker compose ps
docker compose logs -f telegraf
```

Services and their ports:
- Mosquitto MQTT: `1883` (unencrypted), `8883` (TLS, optional)
- InfluxDB 2: `8086` (UI + API)
- Grafana: `3000` (admin/admin by default — change immediately)
- Web UI: `8080`

### 6.2 Flashing and Configuring ESP32 Probes

1. Install PlatformIO (VS Code extension or CLI).
2. Edit `esp32-probe/include/probe_config.h`:
   ```cpp
   #define WIFI_SSID        "YourLabNetwork"
   #define WIFI_PASSWORD    "YourPassword"
   #define MQTT_SERVER      "192.168.1.10"  // Pi server IP
   #define PROBE_ID         "esp32-lab-east"
   #define PING_TARGET      "192.168.1.1"   // Gateway or known-good host
   #define THROUGHPUT_URL   "http://192.168.1.10:8080/speedtest"
   ```
3. Connect ESP32 via USB. `pio run --target upload`. Monitor: `pio device monitor`.
4. Within 30 seconds, you should see metrics appearing in the Web UI and MQTT topics (use `mosquitto_sub -h 192.168.1.10 -t 'labwifimon/#' -v` to verify).

### 6.3 Using the Web UI

Navigate to `http://<pi-ip>:8080`. The main page shows:

- **Overview grid**: One card per probe. Each card shows the Health Score, RSSI gauge, latency sparkline, and status.
- **Probe detail** (click a card): Full metric history, channel scan results, connection history.
- **Network map** (optional): If probe locations are configured, shows a floor plan overlay with signal quality heatmap.
- **Alerts panel**: Active threshold breaches (configurable in `web-ui/config.py`).

The **particle animation** on each probe card responds to the Health Score in real time — it is not merely decorative, it is a rapid visual indicator of relative probe health across the room.

### 6.4 Using Grafana

Navigate to `http://<pi-ip>:3000`. Default credentials: admin/admin (change on first login).

The pre-provisioned **LabWiFiMon Overview** dashboard provides:
- Time-series charts for RSSI, latency, jitter, loss, throughput per probe
- Channel utilization heatmap (from scan data)
- Health Score trend per probe
- Comparison overlays (select multiple probes)

**Creating alerts in Grafana:**
1. Open the RSSI panel → Edit → Alert tab
2. Set condition: `rssi_avg < -75` for 5 minutes
3. Configure notification channel (email, Slack, PagerDuty, etc.)
4. Save. Grafana evaluates the alert rule on every query interval.

### 6.5 Interpreting Results

#### "RSSI is good but latency is high"
**Diagnosis: Bufferbloat** is the most likely cause. The AP or router has an oversized transmit buffer. When the throughput test runs (or another device saturates the link), packets queue for hundreds of milliseconds.

Check: compare latency during throughput test vs. idle. If latency is >5× higher during load than idle, bufferbloat is confirmed.

Fix: enable AQM (FQ-CoDel, CAKE, or DualPI2) on the router/AP firmware. OpenWrt supports CAKE natively via SQM package. Some enterprise APs support AQM in their firmware.

#### "RSSI fluctuates rapidly"
**Diagnosis: Multipath fading or moving objects.** Human bodies are excellent reflectors at 2.4 and 5 GHz. People walking near a probe cause fast fading (±5–10 dBm over seconds). This is expected and benign if RSSI stays above -70 dBm. If it drops into the -75 to -80 range during fluctuations, consider repositioning the probe or AP.

Also check: is the probe physically stable? Vibration from HVAC or equipment can cause antenna orientation changes.

#### "Packet loss spikes at certain times"
**Diagnosis: Scheduled interference.** Common culprits:
- Microwave ovens (2.4 GHz, 30-second burst when used)
- Cordless phones (2.4 GHz DECT)
- Nearby WiFi networks enabling time-of-day schedules (neighbor network that turns on at 9 AM)
- Building HVAC systems with variable-frequency drives (VFDs emit RF noise)

Check the timestamp of loss spikes. If they correlate with lunch hour (microwave) or shift changes (VFDs spooling up), the cause is clear.

Fix: move to 5 GHz or 6 GHz if possible. Or identify and eliminate the interference source.

#### "Throughput is low despite good signal"
**Diagnosis: Channel congestion (CCI).** Check the channel scan data. If 5+ other APs are visible on the same channel, all are competing for airtime.

Fix: change the AP channel to the least-congested option shown in the channel scan. In 2.4 GHz, use only channels 1, 6, or 11. In 5 GHz or 6 GHz, pick a channel with no neighbors, or the fewest neighbors.

Also check: is the AP firmware up to date? Old firmware often has sub-optimal OFDMA scheduling and rate adaptation.

### 6.6 Setting Up Alerts

In `web-ui/config.py`, define threshold policies:
```python
ALERT_THRESHOLDS = {
    "rssi_dbm":        {"warn": -70, "crit": -80},
    "rtt_avg_ms":      {"warn": 30,  "crit": 75},
    "jitter_ms":       {"warn": 15,  "crit": 30},
    "loss_pct":        {"warn": 1.0, "crit": 5.0},
    "health_score":    {"warn": 60,  "crit": 40},
}
```

Alerts are surfaced in the Web UI and optionally pushed via webhook (configure `ALERT_WEBHOOK_URL` in `.env`).

### 6.7 Adding New Probes

1. Flash the firmware to a new ESP32 with a unique `PROBE_ID`.
2. Power it on. It will connect to MQTT and begin publishing automatically.
3. The Web UI auto-discovers new probes within one reporting cycle (~60 seconds).
4. Optionally add the probe's location metadata in `web-ui/probe_locations.json` to place it on the network map.

---

## 7. Advanced Topics

### 7.1 Monitor Mode for Management Frame Capture

Monitor mode places the WiFi interface into a passive receive-all state — it captures every 802.11 frame on the channel, regardless of BSSID. This enables visibility into management frame traffic, which is normally invisible to associated clients.

**Requirements**: A WiFi card and driver that support monitor mode. On Linux:
- AX210: Supported with `iwlwifi` (monitor mode, no TX in monitor mode)
- MT7925: Supported with `mt7921` driver

**Setup on Tier 4 (Linux laptop):**
```bash
# Bring down the interface
ip link set wlan0 down
# Set monitor mode
iw dev wlan0 set type monitor
ip link set wlan0 up
# Set channel to monitor
iw dev wlan0 set channel 6

# Capture with tcpdump
tcpdump -i wlan0 -w capture.pcap 'type mgt'
# or with Wireshark: filter 'wlan.fc.type == 0'
```

**Useful management frame filters (Wireshark):**
- `wlan.fc.type_subtype == 0x0c` — Deauthentication (look for reason codes)
- `wlan.fc.type_subtype == 0x0a` — Disassociation
- `wlan.fc.type_subtype == 0x00` — Association Request (client capabilities)
- `wlan.fc.type_subtype == 0x08` — Beacon
- `wlan.tag.number == 67` — BSS Transition Management Request (AP-initiated roaming)

**LabWiFiMon integration**: A Tier 4 probe running in monitor mode feeds a custom Python parser (`pi-server/mgmt_frame_parser.py`) that counts deauths per BSSID/reason, beacon intervals, and association rates, publishing these as MQTT metrics alongside the active probe metrics.

### 7.2 Using Raspberry Pi Probes Alongside ESP32 Probes

The Pi probe firmware (`pi-server/probe/pi_probe.py`) is a Python script equivalent to the ESP32 firmware. It adds:
- `iw dev wlan0 survey dump` for per-channel noise and busy-time statistics
- `iw dev wlan0 station dump` for per-AP connection quality (TX/RX rates, signal, retries)
- 5 GHz scan capability
- HTTP/3 throughput testing (via `curl` with QUIC support)

Run as a systemd service:
```bash
sudo cp pi-server/probe/labwifimon-probe.service /etc/systemd/system/
sudo systemctl enable --now labwifimon-probe
```

The Pi probe publishes to the same MQTT topics as the ESP32 probe; the server stack treats them identically. The probe tier is communicated via the `hw_type` field in the `status` topic.

### 7.3 WiFi 7 Specific Monitoring

With an Intel BE200 or MT7925 card and Linux 6.5+ (standard on Pi OS Bookworm):

**Check MLO link status:**
```bash
iw dev wlan0 link
# Shows: connected BSS, frequency, MLO links if active
```

**6 GHz band scan:**
```bash
iw dev wlan0 scan freq 5925-7125
```

**Per-MCS TX rate breakdown (MT7925):**
```bash
iw dev wlan0 station dump | grep -A 20 "connected time"
```

**LabWiFiMon WiFi 7 metrics** (when `hw_type: be200` or `hw_type: mt7925`):
- `mlo_links_active`: number of simultaneously active MLO links
- `mlo_link_0_freq`, `mlo_link_1_freq`, ...: frequency of each link
- `mlo_link_0_rssi`, ...: per-link RSSI
- `tx_mcs`, `rx_mcs`: current MCS index (0–13 for WiFi 7)
- `channel_width_mhz`: negotiated channel width (20/40/80/160/320)

### 7.4 Integration with Existing Monitoring

**Prometheus**: The Web UI exposes a `/metrics` endpoint in Prometheus text format. Scrape it from Prometheus and use Grafana's Prometheus data source alongside (or instead of) InfluxDB.

**SNMP (for legacy NMS / Zabbix / Nagios)**: A thin SNMP agent (`tools/snmp_proxy.py`) translates LabWiFiMon metrics to SNMP OIDs. Configure Zabbix to poll it, or use the provided Zabbix template.

**OpenTelemetry**: For organizations running an OTEL collector, the Web UI supports the OTLP HTTP exporter. Set `OTEL_EXPORTER_OTLP_ENDPOINT` in `.env`.

### 7.5 Scaling to Multiple Labs or Buildings

A single MQTT broker and InfluxDB instance can handle hundreds of probes — the bottleneck is the Pi's disk I/O for InfluxDB writes. At >20 probes or high measurement frequency, consider:

- Moving the server stack to a more powerful machine (x86 mini-PC or a cloud VM).
- Using InfluxDB Cloud (hosted) as the storage backend — change only the `influxdb_v2` output plugin URL and token in Telegraf.
- Deploying a regional MQTT broker per building, bridging to a central broker at headquarters using Mosquitto's bridge feature.
- Using InfluxDB's built-in downsampling tasks to aggregate data older than 7 days at lower resolution, reducing long-term storage requirements.

**Multi-site Grafana**: Use Grafana's variable-based filtering (`$probe_id`, `$location`) to create dashboards that span all sites, with the ability to drill into a specific building or room.
