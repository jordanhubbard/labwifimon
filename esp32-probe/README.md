# ESP32 Probe — Build & Flash Guide

Firmware for the LabWiFiMon WiFi quality probe. Runs on ESP32 and ESP32-S3 boards.

---

## Prerequisites

### Hardware
- ESP32 or ESP32-S3 development board (e.g., ESP32 DevKit v1, ESP32-S3-DevKitC-1)
- USB cable for flashing and serial monitor

### Software — PlatformIO

This project uses **PlatformIO** (not ESP-IDF directly). Install it on your build machine:

**Option A: PlatformIO CLI only (recommended for headless / Raspberry Pi)**
```bash
pip install platformio
```

**Option B: VS Code + PlatformIO IDE**
Install the [PlatformIO IDE extension](https://platformio.org/install/ide?install=vscode) from the VS Code marketplace.

> **Raspberry Pi note:** PlatformIO works on arm64 Pi OS. If `pip install platformio` fails, try:
> ```bash
> python3 -m venv ~/pio-env
> source ~/pio-env/bin/activate
> pip install platformio
> ```
> Then always activate that venv before building.

---

## Configure

Edit `src/config.h` before flashing. At minimum, set:

```c
#define WIFI_SSID               "YourWiFiSSID"
#define WIFI_PASSWORD           "YourWiFiPassword"
#define MQTT_SERVER             "192.168.1.100"   // IP of your Pi server
#define PROBE_ID                "probe-bench-1"   // Unique per probe
#define PROBE_LOCATION          "Bench area"      // Human-readable label
```

If your Pi server runs the throughput test endpoint, also set:
```c
#define THROUGHPUT_TEST_URL     "http://192.168.1.100:8080/test-payload"
```

See `src/config.h` for the full list of options (ping count, intervals, deep sleep, etc.).

---

## Build

```bash
cd esp32-probe

# Build for ESP32 (default)
pio run

# Build for ESP32-S3
pio run -e esp32-s3

# Build for ESP32-C5 (WiFi 6, dual-band 2.4 + 5 GHz)
pio run -e esp32-c5
```

First build downloads the toolchain automatically (~300 MB; the C5 downloads a separate pioarduino fork). Subsequent builds are fast.

> **ESP32-C5 note:** The C5 is not yet supported by the official PlatformIO espressif32 platform. The `esp32-c5` environment uses the [pioarduino community fork](https://github.com/pioarduino/platform-espressif32), which is downloaded automatically on first build.

---

## Flash

Connect the ESP32 via USB, then:

```bash
# Flash ESP32 (default)
pio run --target upload

# Flash ESP32-S3
pio run -e esp32-s3 --target upload

# Flash ESP32-C5
pio run -e esp32-c5 --target upload

# If auto-detection fails, specify the port
pio run --target upload --upload-port /dev/ttyUSB0      # Linux (ESP32)
pio run --target upload --upload-port /dev/ttyACM0      # ESP32-S3/C5 USB-CDC
pio run --target upload --upload-port /dev/cu.usbserial-*  # macOS
```

> **Raspberry Pi USB port:** The ESP32 typically shows up as `/dev/ttyUSB0`. If you get a permission error:
> ```bash
> sudo usermod -aG dialout $USER
> # Log out and back in, or: newgrp dialout
> ```

---

## Serial Monitor

Watch probe output in real time:

```bash
pio device monitor
```

You'll see WiFi connection status, MQTT publishes, RSSI readings, and ping results. Press `Ctrl+C` to exit.

---

## Build + Flash + Monitor (one command)

```bash
pio run --target upload && pio device monitor
```

---

## Board Environments

| Environment | Board | LED Pin | Notes |
|---|---|---|---|
| `esp32dev` (default) | ESP32 DevKit | GPIO 2 (blue) | Most common dev board |
| `esp32-s3` | ESP32-S3-DevKitC-1 | GPIO 2 | USB-CDC serial, RGB LED on GPIO 48 not used |
| `esp32-c5` | ESP32-C5-DevKitC-1 | GPIO 2 | WiFi 6 dual-band (2.4+5 GHz), RISC-V, USB-CDC, uses pioarduino fork |

---

## Troubleshooting

**`pio: command not found`**
PlatformIO isn't in your PATH. If you installed in a venv, activate it first (`source ~/pio-env/bin/activate`).

**`No such board: esp32dev`**
First build needs internet to download the platform. Make sure you're online.

**Upload fails / device not found**
- Check `ls /dev/ttyUSB* /dev/ttyACM*` — is the board visible?
- Try a different USB cable (some are charge-only)
- On the Pi, ensure you're in the `dialout` group

**ESP32-S3 serial not working**
The S3 uses USB-CDC by default. The `platformio.ini` already sets `-DARDUINO_USB_CDC_ON_BOOT=1`. Use `/dev/ttyACM0` instead of `/dev/ttyUSB0`.

**Stack overflow / crash loop**
The build flags set a 16 KB loop stack. If you've added heavy local buffers, increase `CONFIG_ARDUINO_LOOP_STACK_SIZE` in `platformio.ini`.

---

## Project Structure

```
esp32-probe/
├── platformio.ini      # Build config (board, libs, flags)
└── src/
    ├── config.h        # ← Edit this: WiFi, MQTT, probe settings
    ├── main.cpp        # Setup/loop, orchestrates measurements
    ├── metrics.h/.cpp  # RSSI, ping, throughput collection & JSON publish
    └── network.h/.cpp  # WiFi + MQTT connection management
```
