# LabWiFiMon — Open Source Distributed WiFi Quality Monitor

![Build Status](https://img.shields.io/badge/build-passing-brightgreen)
![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)
![Stars](https://img.shields.io/github/stars/jordanhubbard/labwifimon?style=social)

LabWiFiMon is an open-source, distributed WiFi quality monitoring system built for labs, makerspaces, and small offices. Low-cost ESP32 or Raspberry Pi probes scatter around your space and continuously measure RSSI, latency, jitter, packet loss, and throughput — publishing metrics via MQTT to a Raspberry Pi server running InfluxDB, Grafana, and a real-time animated Web UI. Optionally add a WiFi 6E or WiFi 7 card to a Pi 5 probe for 6 GHz band visibility and MLO link metrics.

---

## Features

- **Multi-probe distributed measurement** — deploy as many probes as you need; the server auto-discovers them
- **Five signal quality metrics** per probe: RSSI, round-trip latency, jitter, packet loss, and TCP throughput
- **Composite WiFi Health Score** (0–100) with configurable weighting
- **Channel environment scanning** — passive scan shows neighbor APs, co-channel density, and band utilization
- **Bufferbloat detection** — latency-under-load measurement reveals AQM issues invisible to RSSI-only tools
- **Real-time animated Web UI** — particle effects and animated gauges update live via WebSocket
- **Grafana dashboards** — pre-provisioned, with alerting on all metrics
- **WiFi 7 support** — Intel BE200 / MediaTek MT7925 probes report MLO link status, 6 GHz RSSI, and MCS index
- **Management frame capture** (Tier 4 hardware) — deauth reason codes, BSS transition events, client capability logging
- **Battery-operable ESP32 probes** — deep sleep mode yields months of runtime on a small LiPo
- **Docker Compose server stack** — single-command deployment, health checks included
- **Prometheus and OTLP export** — plugs into existing observability stacks

---

## Architecture

```
 ┌──────────────────────────────────────────────────────────┐
 │                   MONITORED WIFI NETWORK                  │
 │                                                           │
 │  [ESP32 Probe]  [ESP32-S3 Probe]  [Pi 5 + BE200 Probe]  │
 │       │                │                   │             │
 └───────┴────────────────┴───────────────────┴─────────────┘
                           │ MQTT (port 1883)
 ┌─────────────────────────▼─────────────────────────────────┐
 │                  RASPBERRY PI SERVER                       │
 │                                                           │
 │  Mosquitto ──► Telegraf ──► InfluxDB 2                   │
 │                                  │                        │
 │                      ┌───────────┴───────────┐           │
 │                   Grafana              Web UI (Flask)     │
 │                 (port 3000)          (port 8080, WS)      │
 └───────────────────────────────────────────────────────────┘
```

---

## Quick Start

### 1. Clone and start the server

```bash
git clone https://github.com/jordanhubbard/labwifimon.git
cd labwifimon/pi-server
cp .env.example .env          # edit: set INFLUXDB_TOKEN, ORG, BUCKET
docker compose up -d
```

Grafana: `http://<pi-ip>:3000` (admin/admin)
Web UI: `http://<pi-ip>:8080`

### 2. Configure and flash a probe

```bash
# Install PlatformIO (if you don't have it)
pip install platformio

# Edit probe credentials
nano esp32-probe/src/config.h
# Set WIFI_SSID, WIFI_PASSWORD, MQTT_SERVER, PROBE_ID

# Build and flash with PlatformIO
cd esp32-probe
pio run --target upload
```

See [ESP32 Probe Build Guide](esp32-probe/README.md) for detailed instructions, Raspberry Pi setup, and troubleshooting.

### 3. Watch it go

Within 60 seconds of powering the probe, metrics appear in the Web UI and Grafana. Add more probes by flashing additional ESP32s with unique `PROBE_ID` values — no server-side configuration needed.

---

## Hardware Support

| Tier | Hardware | Bands | WiFi Std | Key Capabilities | Cost |
|---|---|---|---|---|---|
| 1 | ESP32 / ESP32-S3 | 2.4 GHz | WiFi 4 | RSSI, ping, throughput, channel scan | $5–$15 |
| 2 | Raspberry Pi 4/5 (onboard) | 2.4 + 5 GHz | WiFi 5 | All Tier 1 + 5 GHz visibility | $35–$80 |
| 3 | Pi 5 + M.2 HAT + AX210 | 2.4 + 5 + **6 GHz** | WiFi 6E | All Tier 2 + 6 GHz, iw survey | ~$90 add-on |
| 3 | Pi 5 + M.2 HAT + BE200 | 2.4 + 5 + **6 GHz** | **WiFi 7** | All above + MLO metrics, 4096-QAM MCS | ~$97 add-on |
| 3 | Pi 5 + M.2 HAT + MT7925 | 2.4 + 5 + **6 GHz** | **WiFi 7** | All above + strong open-source driver | ~$102 add-on |
| 4 | Linux laptop + BE200/MT7925 | All | **WiFi 7** | All above + **monitor mode**, mgmt frames | Existing HW |

**M.2 HATs**: Waveshare PCIe to M.2 HAT+ (~$12), Pineberry HatDrive (~$20)

**Kernel requirement for WiFi 7 cards**: Linux 6.5+ (Raspberry Pi OS Bookworm ships 6.6 — compatible out of the box)

---

## Screenshots

> *Screenshots coming soon — contributions welcome!*

| View | Description |
|---|---|
| **Web UI Overview** | Animated probe grid with per-probe Health Score badges and particle effects |
| **Probe Detail** | Full metric history, channel scan heatmap, connection event log |
| **Grafana Dashboard** | Multi-probe time-series overlay, RSSI heatmap, alert history |
| **Channel Environment** | Band/channel utilization view showing neighbor AP density |
| **Network Map** | Floor plan overlay with signal quality heatmap (optional) |

---

## Comparison vs. Commercial Solutions

| Feature | LabWiFiMon | Ekahau Sidekick | Umber Fi-Wi | NetAlly AirCheck |
|---|---|---|---|---|
| Cost | **~$70–$200** (one-time, BOM) | $3,000–$5,000 | SaaS subscription | $1,500–$3,000 |
| Continuous monitoring | **Yes (24/7)** | Survey snapshots | Yes | Yes (AirCheck G3) |
| WiFi 7 / 6 GHz | **Yes (Tier 3+)** | Yes (Sidekick 2) | Yes | Limited |
| L4S / Bufferbloat detection | **Yes** | No | **Yes (native)** | No |
| Open source | **Yes (MIT)** | No | No | No |
| Self-hosted | **Yes** | Cloud-assisted | Cloud-required | Standalone |
| Management frame capture | **Yes (Tier 4)** | Yes | No | Yes |
| Custom metrics / extensible | **Yes (plugin MQTT)** | Limited | No | No |
| Multi-site / multi-lab | **Yes (MQTT bridge)** | Yes | Yes | Limited |
| Battery-operated probes | **Yes (ESP32)** | N/A | N/A | Yes |

**Note on Umber Fi-Wi**: Umber Networks' Fi-Wi architecture is the state of the art for L4S-based WiFi AQM. LabWiFiMon is designed to *measure* the presence or absence of proper AQM (bufferbloat detection) but does not replace a purpose-built L4S AP stack. Think of LabWiFiMon as the observability layer and Fi-Wi as the control plane — they are complementary.

---

## Documentation

- [Theory of Operation](docs/THEORY_OF_OPERATION.md) — WiFi fundamentals, WiFi 7, architecture deep-dive, metric scoring algorithm, hardware tiers, deployment guide
- [Server Setup](pi-server/README.md) — Docker Compose stack, Telegraf config, InfluxDB schema
- [ESP32 Probe Firmware](esp32-probe/README.md) — PlatformIO build, configuration reference, deep sleep mode
- [Pi Probe](pi-server/probe/README.md) — Python probe for Raspberry Pi and Linux systems
- [Web UI](web-ui/README.md) — Flask backend, WebSocket protocol, visualization customization
- [Grafana Dashboards](grafana/README.md) — Pre-provisioned dashboards, alert setup

---

## Contributing

Contributions are welcome. Please open an issue to discuss significant changes before submitting a pull request.

**Good first issues:**
- Add support for ESP32-C6 (the first ESP32 with WiFi 6 / 802.11ax)
- Implement HTTP/3 (QUIC) throughput measurement on Pi probes
- Add a Zabbix template for the SNMP proxy
- Build a floor plan editor in the Web UI for probe placement visualization
- Add i18n support to the Web UI

**Development setup:**
```bash
cd web-ui
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env  # point at a local/test InfluxDB instance
python app.py
```

Run tests: `pytest tests/`

---

## License

MIT License. See [LICENSE](LICENSE).

---

## Acknowledgments

- **[Umber Networks](https://umber.net)** — for the Fi-Wi / L4S architecture that inspired the bufferbloat detection methodology in LabWiFiMon. Their work on integrating DualPI2 AQM into the WiFi scheduler is the right answer to the bufferbloat problem.
- **[Espressif Systems](https://www.espressif.com)** — for the ESP-IDF and the remarkably capable ESP32 family, which makes $8 WiFi probes possible.
- **[InfluxData](https://www.influxdata.com)** — for InfluxDB 2 and Telegraf, the backbone of the data pipeline.
- **[Grafana Labs](https://grafana.com)** — for Grafana, the industry-standard visualization layer.
- **[Mosquitto](https://mosquitto.org)** — Eclipse Mosquitto, the lightweight MQTT broker that makes pub/sub trivial to deploy.
- **The 802.11 Working Group** — for WiFi 7 (802.11be), which finally gives us the multi-link and spectrum tools to build truly reliable wireless infrastructure.
