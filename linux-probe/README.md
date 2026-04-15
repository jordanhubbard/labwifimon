# LabWiFiMon Linux Probe

A Python-based WiFi quality probe that runs on any Linux system (Raspberry Pi, Intel NUC, laptop, etc.) and publishes metrics to the same MQTT pipeline used by the ESP32 probes.

Enables monitoring of **WiFi 6E and WiFi 7** via M.2 PCIe cards (Intel BE200, AX210; MediaTek MT7925) that are not available to ESP32.

---

## Supported Hardware

| Hardware | Notes |
|---|---|
| Raspberry Pi 5 | Best choice. PCIe M.2 HAT + Intel BE200 = WiFi 7 monitoring |
| Raspberry Pi 4 | USB WiFi dongle or built-in `wlan0`. No PCIe. |
| Intel NUC / mini-PC | Any built-in or M.2 WiFi card |
| Any Debian/Ubuntu/Fedora/Arch Linux box | With any `iw`-compatible wireless NIC |

---

## Quick Start

```bash
# 1. Clone / copy the linux-probe directory to the target machine
scp -r linux-probe/ pi@raspberrypi.local:~/

# 2. Run the installer (handles packages, venv, systemd service)
ssh pi@raspberrypi.local
cd linux-probe
sudo bash install.sh

# 3. Edit config if needed
sudo nano /etc/labwifimon/config.yaml
sudo systemctl restart labwifimon-probe

# 4. Watch live logs
journalctl -u labwifimon-probe -f
```

### Manual test (no installation)

```bash
pip install paho-mqtt pyyaml requests
python3 probe.py --once --dry-run     # print one JSON sample to stdout
python3 probe.py -v                    # run with debug logging
```

---

## M.2 HAT Installation — Raspberry Pi 5

The Pi 5 exposes a PCIe FPC connector that accepts M.2 HAT boards, allowing
full-speed PCIe WiFi 7 cards.

### Hardware needed

- Raspberry Pi 5 (any RAM variant)
- M.2 HAT for Pi 5 (e.g., Pimoroni NVMe Base, Waveshare PCIe to M.2)
- **Intel BE200** (WiFi 7, 6 GHz, MLO) or **AX210** (WiFi 6E, 6 GHz)
- Short M.2 2230 or 2242 card usually fits best

### Physical installation

1. Power off the Pi completely.
2. Connect the M.2 HAT FPC ribbon cable to the Pi 5's PCIe connector (white latch, gold contacts face down).
3. Insert the WiFi card into the M.2 key-E or key-M slot on the HAT.
4. Secure the card with the retention screw.
5. Attach any antenna cables (use U.FL to RP-SMA pigtails + external antennas for best range).

### Firmware configuration

Add these lines to `/boot/firmware/config.txt`:

```ini
# Enable the PCIe x1 connector
dtparam=pciex1

# Optional: request PCIe Gen 3 (Intel BE200 supports it; improves throughput ceiling)
# Some HATs may not support Gen 3 — remove this line if the card doesn't appear
dtparam=pciex1_gen=3
```

Reboot, then verify the card is detected:

```bash
lspci | grep -i wireless
# Should show something like:
# 0001:01:00.0 Network controller: Intel Corporation BE200 802.11be Wireless Network Adapter
```

---

## Driver and Firmware Setup — Intel BE200

The BE200 uses the `iwlwifi` driver, which is included in the mainline kernel (6.1+).  
The firmware files are distributed separately.

### Check current state

```bash
lsmod | grep iwlwifi       # should show iwlwifi, iwlmvm
dmesg | grep iwlwifi       # look for "loaded firmware version" or error messages
ls /lib/firmware/iwlwifi-be*   # firmware files for BE200
```

### Install firmware (Debian / Ubuntu)

```bash
# Enable non-free repository first (Debian)
sudo apt edit-sources   # add "non-free" to the line with your release

sudo apt update
sudo apt install firmware-iwlwifi

# Or download directly from linux-firmware:
# https://git.kernel.org/pub/scm/linux/kernel/git/firmware/linux-firmware.git/tree/
# Files needed: iwlwifi-be-a0-ge-b0-89.ucode  iwlwifi-be-a0-ge-b0-89.pnvm
sudo cp iwlwifi-be-*.ucode iwlwifi-be-*.pnvm /lib/firmware/
sudo modprobe -r iwlwifi && sudo modprobe iwlwifi   # reload driver
```

### Kernel version requirements

| Feature | Minimum kernel |
|---|---|
| BE200 basic operation | 6.1 |
| WiFi 7 EHT (802.11be) | 6.5 |
| MLO (Multi-Link Operation) | 6.7 |
| 6 GHz regulatory unlock | depends on region firmware |

Check your kernel: `uname -r`

On Raspberry Pi OS, install a newer kernel with:
```bash
sudo rpi-update          # testing channel — use with care
# or
sudo apt install linux-image-rpi-v8   # if available in repo
```

### Verify WiFi 7 capabilities

```bash
python3 wifi7_info.py wlan0
# Output:
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#   WiFi Capability Report — wlan0  (phy0)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#   Standard            WiFi 7  (802.11be / EHT)
#   Bands               2.4GHz, 5GHz, 6GHz
#   6 GHz supported     Yes
#   Max channel width   320 MHz
#   MLO capable         Yes
#   4096-QAM (MCS12/13) Yes
#   ...

python3 wifi7_info.py --json wlan0   # machine-readable JSON
```

---

## Configuration Reference

Edit `/etc/labwifimon/config.yaml` (created by `install.sh`).  
All keys can be overridden with environment variables (`LABWIFIMON_<KEY>`).

| Key | Default | Description |
|---|---|---|
| `interface` | auto | WiFi interface (e.g. `wlan0`, `wlp3s0`) |
| `probe_id` | hostname | Unique probe name; appears in MQTT topics |
| `location` | `""` | Optional human label stored in metrics |
| `mqtt_host` | `localhost` | Mosquitto broker address |
| `mqtt_port` | `1883` | Mosquitto broker port |
| `ping_gateway` | `auto` | Gateway IP or `auto` (read from routing table) |
| `ping_dns` | `1.1.1.1` | Host for DNS latency measurement |
| `ping_external` | `8.8.8.8` | Host for internet latency / jitter / loss |
| `ping_count` | `10` | ICMP packets per measurement |
| `throughput_url` | `http://localhost:8080/test_payload.bin` | Throughput server URL |
| `throughput_enabled` | `true` | Enable HTTP download throughput test |
| `interval_seconds` | `30` | Metric collection interval |
| `scan_interval_seconds` | `300` | BSS scan interval (requires root) |
| `heartbeat_interval_seconds` | `60` | Online/offline status publish interval |
| `wifi7_monitoring` | `true` | Enable EHT/MLO/6 GHz capability detection |
| `log_level` | `INFO` | `DEBUG` \| `INFO` \| `WARNING` \| `ERROR` |

---

## Running as a Systemd Service

`install.sh` creates and enables the service automatically.  Manual steps:

```bash
# Install (if not done by install.sh)
sudo cp labwifimon-probe.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now labwifimon-probe

# Manage
sudo systemctl start   labwifimon-probe
sudo systemctl stop    labwifimon-probe
sudo systemctl restart labwifimon-probe
sudo systemctl status  labwifimon-probe

# Logs
journalctl -u labwifimon-probe -f          # follow live
journalctl -u labwifimon-probe --since "1h ago"
```

### Running as non-root (advanced)

The probe needs `CAP_NET_ADMIN` for BSS scanning.  To avoid running as root:

```bash
# Create a system user
sudo useradd -r -s /sbin/nologin labwifimon

# Grant capabilities to the venv Python binary
sudo setcap 'cap_net_admin,cap_net_raw+eip' /opt/labwifimon/venv/bin/python3

# Edit the service file — change User=root to User=labwifimon
sudo systemctl edit labwifimon-probe
```

---

## MQTT Topics and JSON Format

The probe publishes to the same topics as ESP32 probes, fully compatible with
the Telegraf pipeline in `pi-server/telegraf/telegraf.conf`.

### `labwifimon/<probe_id>/metrics` (every `interval_seconds`)

```json
{
  "probe_id": "pi-probe-1",
  "timestamp_ms": 1700000000000,
  "uptime_s": 3600,
  "platform": "linux",
  "firmware": "1.0.0",
  "wifi": {
    "ssid": "LabNet-6GHz",
    "bssid": "aa:bb:cc:dd:ee:ff",
    "channel": 37,
    "rssi_dbm": -52,
    "tx_power_dbm": 20.0,
    "bandwidth_mhz": 320,
    "frequency_mhz": 6135,
    "band": "6GHz",
    "standard": "802.11be",
    "tx_rate_mbps": 5765.0,
    "rx_rate_mbps": 5765.0
  },
  "latency": {
    "gateway_avg_ms": 1.4,
    "gateway_min_ms": 0.9,
    "gateway_max_ms": 2.3,
    "dns_avg_ms": 4.2,
    "internet_avg_ms": 12.1,
    "internet_min_ms": 10.8,
    "internet_max_ms": 14.6,
    "jitter_ms": 0.9,
    "samples": 10
  },
  "packet_loss": {
    "percent": 0.0,
    "sent": 10,
    "received": 10
  },
  "throughput": {
    "download_kbps": 412000,
    "test_bytes": 10485760,
    "test_duration_ms": 204,
    "success": true
  },
  "wifi7": {
    "eht_capable": true,
    "mlo_capable": true,
    "mlo_active": true,
    "mlo_links": [
      {"id": 0, "frequency_mhz": 6135, "rssi_dbm": -52},
      {"id": 1, "frequency_mhz": 5500, "rssi_dbm": -61}
    ],
    "band_6ghz_supported": true,
    "on_6ghz": true,
    "max_bandwidth_mhz": 320,
    "preamble_puncturing": true,
    "qam4096": true,
    "regulatory_country": "US"
  }
}
```

### `labwifimon/<probe_id>/scan` (every `scan_interval_seconds`)

```json
{
  "probe_id": "pi-probe-1",
  "timestamp_ms": 1700000000000,
  "scan_duration_ms": 1840,
  "count": 12,
  "networks": [
    {"ssid": "LabNet-6GHz", "bssid": "aa:bb:cc:dd:ee:ff",
     "channel": 37, "rssi": -52, "band": "6GHz",
     "bandwidth_mhz": 320, "encrypted": true, "eht_capable": true},
    {"ssid": "LabNet-5GHz", "bssid": "aa:bb:cc:dd:ee:fe",
     "channel": 100, "rssi": -61, "band": "5GHz",
     "bandwidth_mhz": 80, "encrypted": true}
  ]
}
```

### `labwifimon/<probe_id>/status` (every `heartbeat_interval_seconds`, retained)

```json
{
  "probe_id": "pi-probe-1",
  "timestamp_ms": 1700000000000,
  "status": "online",
  "firmware": "1.0.0",
  "platform": "linux",
  "ip_address": "192.168.1.42",
  "mac_address": "dc:a6:32:ab:cd:ef",
  "uptime_s": 3600,
  "wifi_rssi_dbm": -52
}
```

---

## Monitor Mode — Advanced Frame Capture

`monitor_mode.py` sets a **dedicated** interface to monitor mode and captures
raw 802.11 management frames.  This gives beacon intervals, supported rates,
and HT/VHT/HE/EHT capability IEs directly from over-the-air frames.

**Use a second adapter** — monitor mode takes the interface offline for all
normal WiFi traffic.

### Prerequisites

```bash
pip install scapy
sudo apt install libpcap-dev    # if scapy can't capture without it
```

### Usage

```bash
# Identify your second adapter
iw dev

# Start the scanner (will hop 2.4 + 5 GHz channels)
sudo python3 monitor_mode.py -i wlan1

# Include 6 GHz channels (requires WiFi 6E/7 adapter + regulatory unlock)
sudo python3 monitor_mode.py -i wlan1 --6ghz

# Use a different config
sudo python3 monitor_mode.py -i wlan1 -c /etc/labwifimon/config.yaml
```

Publishes to `labwifimon/<probe_id>/frames` every `scan_interval_seconds`.

### Stopping

Press `Ctrl+C` or send `SIGTERM`.  The script automatically restores managed
mode before exiting.

---

## WiFi 7 Feature Reference

| Feature | Description | iw keyword |
|---|---|---|
| EHT (Extremely High Throughput) | 802.11be PHY | `EHT Capabilities` |
| MLO (Multi-Link Operation) | Simultaneous multi-band links | `link[N]` in iw link |
| 320 MHz channels | 6 GHz only | `width: 320 MHz` |
| 4096-QAM | MCS index 12 and 13 | EHT MCS 12-13 entries |
| Preamble puncturing | Skip bad sub-channels | `preamble puncturing` |
| 6 GHz band | UNII-5/6/7/8 (5925–7125 MHz) | Band 3 frequencies |

---

## Troubleshooting

**`iw dev scan` says "Operation not permitted"**  
The probe needs `CAP_NET_ADMIN`.  Either run as root or use `setcap` as shown above.

**Interface not found / probe_id is wrong**  
Edit `/etc/labwifimon/config.yaml` and set `interface` and `probe_id` explicitly.  
Find your interface: `iw dev`

**BE200 not detected after Pi 5 reboot**  
Check `/boot/firmware/config.txt` has `dtparam=pciex1`.  
Check `dmesg | grep -iE "pcie|iwlwifi|be200"` for errors.

**6 GHz channels show "disabled" in `iw phy`**  
6 GHz requires the correct regulatory domain.  Set country code:
```bash
iw reg set US      # temporary
# Permanent: configure in /etc/wpa_supplicant/ or NetworkManager
```

**MQTT metrics not appearing in Grafana**  
1. Check Telegraf is running: `docker compose -f pi-server/docker-compose.yml ps`
2. Subscribe manually: `mosquitto_sub -h localhost -t 'labwifimon/#' -v`
3. Check probe logs: `journalctl -u labwifimon-probe -n 50`

**Throughput test always shows null**  
The throughput server (`pi-server/throughput-server`) must be running.  
Set `throughput_enabled: false` in config to skip it.
