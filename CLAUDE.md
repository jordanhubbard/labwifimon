# LabWiFiMon — Distributed WiFi Quality Monitoring System

## Project Overview
ESP32 probes scattered around a lab measure WiFi quality metrics and publish via MQTT to a Raspberry Pi running InfluxDB + Grafana + a custom Web UI.

## Architecture
- ESP32 probes (PlatformIO/Arduino) → MQTT → Mosquitto → Telegraf → InfluxDB 2 → Grafana + Web UI
- Probes measure: RSSI, latency, jitter, packet loss, throughput, channel scan
- Pi server: Docker Compose stack (Mosquitto, Telegraf, InfluxDB 2, Grafana, Web UI)
- Custom Web UI: Flask/FastAPI app with real-time WebSocket updates, animated signal visualizations

## Code Standards
- ESP32: C++ Arduino framework, clean modular design
- Server: Docker Compose, proper health checks
- Web UI: Python backend (Flask), modern HTML5/CSS3/JS frontend with WebSocket
- Documentation: Comprehensive, beginner-friendly

## Key Design Decisions
- MQTT for lightweight pub/sub (perfect for ESP32)
- InfluxDB 2 with Flux queries
- Grafana for detailed analytics
- Custom Web UI for "sparkly" real-time signal visualization with animated gauges
- No auth required for initial setup (lab environment)
