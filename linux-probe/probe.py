#!/usr/bin/env python3
"""
LabWiFiMon Linux Probe — WiFi quality monitoring for Linux systems.

Collects WiFi metrics using Linux tools (iw, iwconfig, ip, ping) and publishes
to MQTT in the same nested JSON format used by the ESP32 probes, compatible with
the Telegraf/InfluxDB pipeline defined in pi-server/telegraf/telegraf.conf.

MQTT topics published:
  labwifimon/<probe_id>/metrics  — full metrics (30 s default)
  labwifimon/<probe_id>/scan     — nearby AP scan (300 s default)
  labwifimon/<probe_id>/status   — online heartbeat (retained)

Usage:
  python3 probe.py                   # run as service
  python3 probe.py --once --dry-run  # print one sample, no MQTT
  python3 probe.py -v                # verbose / debug logging
"""

import argparse
import ipaddress
import json
import logging
import os
import re
import signal
import socket
import subprocess
import sys
import time
import threading
from typing import Any, Dict, List, Optional, Tuple

import paho.mqtt.client as mqtt
import requests
import yaml

VERSION = "1.0.0"
PLATFORM = "linux"

DEFAULT_CONFIG: Dict[str, Any] = {
    "interface": "",              # blank → auto-detect
    "location": "",              # optional human label, empty string OK
    "probe_id": "",              # blank → hostname
    "mqtt_host": "localhost",
    "mqtt_port": 1883,
    "mqtt_topic_prefix": "labwifimon",
    "ping_gateway": "auto",      # "auto" → read from default route
    "ping_dns": "1.1.1.1",      # used for dns_avg_ms latency field
    "ping_external": "8.8.8.8",
    "ping_count": 10,
    "throughput_url": "http://localhost:8080/test_payload.bin",
    "throughput_enabled": True,
    "interval_seconds": 30,
    "scan_interval_seconds": 300,
    "heartbeat_interval_seconds": 60,
    "wifi7_monitoring": True,
    "log_level": "INFO",
}

log = logging.getLogger("labwifimon.probe")

# ---------------------------------------------------------------------------
# Subprocess helpers
# ---------------------------------------------------------------------------

def _run(args: List[str], timeout: int = 15) -> Tuple[int, str, str]:
    """Run a command; return (returncode, stdout, stderr). Never raises."""
    try:
        r = subprocess.run(
            args, capture_output=True, text=True, timeout=timeout
        )
        return r.returncode, r.stdout, r.stderr
    except subprocess.TimeoutExpired:
        log.debug("Timeout: %s", " ".join(args))
        return -1, "", "timeout"
    except FileNotFoundError:
        log.debug("Not found: %s", args[0])
        return -1, "", f"{args[0]}: not found"
    except Exception as exc:
        log.debug("Error running %s: %s", args[0], exc)
        return -1, "", str(exc)


# ---------------------------------------------------------------------------
# Network / interface helpers
# ---------------------------------------------------------------------------

def detect_interface() -> str:
    """Return the first wireless interface that is UP, or 'wlan0'."""
    rc, out, _ = _run(["iw", "dev"])
    ifaces = re.findall(r"Interface (\S+)", out)
    for iface in ifaces:
        _, link_out, _ = _run(["ip", "link", "show", iface])
        if "LOWER_UP" in link_out or ",UP," in link_out:
            return iface
    return ifaces[0] if ifaces else "wlan0"


def get_default_gateway() -> Optional[str]:
    """Parse default gateway from 'ip route show default'."""
    rc, out, _ = _run(["ip", "route", "show", "default"])
    m = re.search(r"default via (\S+)", out)
    return m.group(1) if m else None


def get_ip_and_mac(iface: str) -> Tuple[Optional[str], Optional[str]]:
    """Return (ip_address, mac_address) for an interface."""
    rc, out, _ = _run(["ip", "addr", "show", iface])
    ip = None
    mac = None
    m = re.search(r"inet (\d+\.\d+\.\d+\.\d+)/", out)
    if m:
        ip = m.group(1)
    m = re.search(r"link/ether ([0-9a-f:]{17})", out, re.IGNORECASE)
    if m:
        mac = m.group(1)
    return ip, mac


def get_uptime_s() -> int:
    """Read system uptime from /proc/uptime."""
    try:
        with open("/proc/uptime") as f:
            return int(float(f.read().split()[0]))
    except Exception:
        return 0


# ---------------------------------------------------------------------------
# WiFi link info
# ---------------------------------------------------------------------------

def _classify_band(freq_mhz: int) -> str:
    if freq_mhz < 3000:
        return "2.4GHz"
    elif freq_mhz < 5925:
        return "5GHz"
    return "6GHz"


def _standard_from_iw(text: str) -> str:
    """Infer 802.11 standard from iw link bitrate keywords."""
    if "EHT-MCS" in text:
        return "802.11be"
    if "HE-MCS" in text:
        return "802.11ax"
    if "VHT-MCS" in text:
        return "802.11ac"
    if "HT-MCS" in text:
        return "802.11n"
    return "802.11a/g"


def parse_iw_link(iface: str) -> Dict[str, Any]:
    """
    Parse 'iw dev <iface> link'.
    Returns dict with keys: connected, ssid, bssid, rssi_dbm, frequency_mhz,
    tx_rate_mbps, rx_rate_mbps, standard, mlo_active, mlo_links.
    """
    rc, out, _ = _run(["iw", "dev", iface, "link"])
    result: Dict[str, Any] = {"connected": False}

    if rc != 0 or "Not connected" in out:
        return result

    result["connected"] = True

    m = re.search(r"Connected to ([0-9a-f:]{17})", out, re.IGNORECASE)
    if m:
        result["bssid"] = m.group(1)

    m = re.search(r"SSID: (.+)", out)
    if m:
        result["ssid"] = m.group(1).strip()

    m = re.search(r"signal: (-?\d+)\s*dBm", out)
    if m:
        result["rssi_dbm"] = int(m.group(1))

    m = re.search(r"freq: (\d+)", out)
    if m:
        result["frequency_mhz"] = int(m.group(1))

    m = re.search(r"tx bitrate: ([\d.]+)\s*MBit/s", out)
    if m:
        result["tx_rate_mbps"] = float(m.group(1))

    m = re.search(r"rx bitrate: ([\d.]+)\s*MBit/s", out)
    if m:
        result["rx_rate_mbps"] = float(m.group(1))

    result["standard"] = _standard_from_iw(out)

    # MLO links (WiFi 7) — iw shows "link[N]" blocks when MLO is active
    mlo_blocks = re.findall(
        r"link\[(\d+)\][^\n]*\n(?:.*\n)*?.*?freq: (\d+).*\n(?:.*\n)*?.*?signal: (-?\d+)",
        out,
    )
    if mlo_blocks:
        result["mlo_active"] = True
        result["mlo_links"] = [
            {"id": int(lid), "frequency_mhz": int(freq), "rssi_dbm": int(sig)}
            for lid, freq, sig in mlo_blocks
        ]
    else:
        result["mlo_active"] = False

    return result


def parse_iw_info(iface: str) -> Dict[str, Any]:
    """Parse 'iw dev <iface> info' for channel, bandwidth, txpower."""
    rc, out, _ = _run(["iw", "dev", iface, "info"])
    result: Dict[str, Any] = {}
    if rc != 0:
        return result

    m = re.search(
        r"channel (\d+) \((\d+) MHz\), width: (\d+) MHz(?:, center1: (\d+) MHz)?",
        out,
    )
    if m:
        result["channel"] = int(m.group(1))
        result["frequency_mhz"] = int(m.group(2))
        result["bandwidth_mhz"] = int(m.group(3))
        if m.group(4):
            result["center_freq_mhz"] = int(m.group(4))

    m = re.search(r"txpower ([\d.]+) dBm", out)
    if m:
        result["tx_power_dbm"] = float(m.group(1))

    return result


def fallback_iwconfig(iface: str) -> Dict[str, Any]:
    """Fallback RSSI/SSID parsing via iwconfig if iw is absent."""
    rc, out, _ = _run(["iwconfig", iface])
    result: Dict[str, Any] = {}
    if rc != 0:
        return result

    m = re.search(r'ESSID:"([^"]*)"', out)
    if m:
        result["ssid"] = m.group(1)

    # "Signal level=-65 dBm" or "Signal level=175/100"
    m = re.search(r"Signal level[=:](-?\d+)\s*dBm", out)
    if m:
        result["rssi_dbm"] = int(m.group(1))
    else:
        m = re.search(r"Signal level[=:](\d+)/100", out)
        if m:
            result["rssi_dbm"] = int(m.group(1)) - 100

    m = re.search(r"Frequency[=:]([\d.]+)\s*GHz", out)
    if m:
        result["frequency_mhz"] = int(float(m.group(1)) * 1000)

    m = re.search(r"Bit Rate[=:]([\d.]+)\s*Mb/s", out)
    if m:
        result["tx_rate_mbps"] = float(m.group(1))

    return result


# ---------------------------------------------------------------------------
# Ping / latency
# ---------------------------------------------------------------------------

def _parse_ping(stdout: str) -> Dict[str, Any]:
    """Extract latency and loss stats from ping output."""
    result: Dict[str, Any] = {"reachable": False}

    m = re.search(r"(\d+)% packet loss", stdout)
    if m:
        result["packet_loss_pct"] = float(m.group(1))
        result["reachable"] = result["packet_loss_pct"] < 100

    m = re.search(r"(\d+) packets transmitted, (\d+) (?:packets )?received", stdout)
    if m:
        result["sent"] = int(m.group(1))
        result["received"] = int(m.group(2))

    # rtt min/avg/max/mdev
    m = re.search(
        r"rtt min/avg/max/mdev = ([\d.]+)/([\d.]+)/([\d.]+)/([\d.]+)", stdout
    )
    if m:
        result["min_ms"] = float(m.group(1))
        result["avg_ms"] = float(m.group(2))
        result["max_ms"] = float(m.group(3))
        result["mdev_ms"] = float(m.group(4))

    return result


def ping_host(target: str, count: int = 10) -> Dict[str, Any]:
    """Ping target and return parsed stats dict."""
    rc, out, _ = _run(
        ["ping", "-c", str(count), "-W", "2", target],
        timeout=count * 3 + 5,
    )
    if rc < 0:
        return {"target": target, "reachable": False}
    parsed = _parse_ping(out)
    parsed["target"] = target
    return parsed


# ---------------------------------------------------------------------------
# Throughput
# ---------------------------------------------------------------------------

def measure_throughput(url: str, max_bytes: int = 20 * 1024 * 1024) -> Dict[str, Any]:
    """
    Download from url and measure throughput.
    Returns dict with download_kbps, or error string.
    """
    result: Dict[str, Any] = {"download_kbps": None, "error": None}
    try:
        t0 = time.monotonic()
        downloaded = 0
        with requests.get(url, stream=True, timeout=15) as resp:
            resp.raise_for_status()
            for chunk in resp.iter_content(chunk_size=65536):
                downloaded += len(chunk)
                if downloaded >= max_bytes:
                    break
        elapsed = max(time.monotonic() - t0, 0.001)
        result["download_kbps"] = round((downloaded * 8) / (elapsed * 1000), 1)
        result["test_bytes"] = downloaded
        result["test_duration_ms"] = int(elapsed * 1000)
    except requests.exceptions.ConnectionError:
        result["error"] = "connection refused"
    except requests.exceptions.Timeout:
        result["error"] = "timeout"
    except Exception as exc:
        result["error"] = str(exc)
    return result


# ---------------------------------------------------------------------------
# BSS scan
# ---------------------------------------------------------------------------

def scan_bss(iface: str, max_networks: int = 30) -> Dict[str, Any]:
    """
    Scan for nearby access points via 'iw dev <iface> scan'.
    Returns scan payload dict matching telegraf's wifi_scan schema.
    Requires CAP_NET_ADMIN (or root).
    """
    t0 = time.monotonic()
    rc, out, err = _run(["iw", "dev", iface, "scan"], timeout=30)
    scan_duration_ms = int((time.monotonic() - t0) * 1000)

    networks: List[Dict[str, Any]] = []
    error: Optional[str] = None

    if rc != 0:
        if "Operation not permitted" in err:
            error = "CAP_NET_ADMIN required"
        elif "Device or resource busy" in err:
            error = "interface busy"
        else:
            error = err.strip() or "scan failed"
        log.warning("BSS scan: %s", error)
        return {
            "scan_duration_ms": scan_duration_ms,
            "count": 0,
            "networks": [],
            "error": error,
        }

    current: Dict[str, Any] = {}

    def _flush():
        nonlocal current
        if current and len(networks) < max_networks:
            networks.append(current)
        current = {}

    for line in out.splitlines():
        stripped = line.strip()

        if stripped.startswith("BSS "):
            _flush()
            m = re.match(r"BSS ([0-9a-f:]{17})", stripped, re.IGNORECASE)
            current = {"bssid": m.group(1) if m else "unknown"}

        elif stripped.startswith("SSID:"):
            current["ssid"] = stripped[5:].strip()

        elif stripped.startswith("freq:"):
            m = re.search(r"(\d+)", stripped)
            if m:
                freq = int(m.group(1))
                current["frequency_mhz"] = freq
                current["band"] = _classify_band(freq)

        elif stripped.startswith("signal:"):
            m = re.search(r"(-?[\d.]+)\s*dBm", stripped)
            if m:
                current["rssi"] = round(float(m.group(1)))

        elif "* primary channel:" in stripped:
            m = re.search(r"(\d+)", stripped)
            if m:
                current["channel"] = int(m.group(1))

        elif "* channel width:" in stripped:
            m = re.search(r"(\d+)\s*MHz", stripped)
            if m:
                current["bandwidth_mhz"] = int(m.group(1))

        elif "capability:" in stripped:
            # "Privacy" bit means encryption is required
            current["encrypted"] = "Privacy" in stripped

        elif "EHT" in stripped:
            current["eht_capable"] = True

        elif "HE capabilities" in stripped:
            current["he_capable"] = True

    _flush()

    return {
        "scan_duration_ms": scan_duration_ms,
        "count": len(networks),
        "networks": networks,
    }


# ---------------------------------------------------------------------------
# WiFi 7 metrics (optional — requires wifi7_info.py)
# ---------------------------------------------------------------------------

def _get_wifi7_metrics(iface: str, link: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    try:
        from wifi7_info import get_wifi7_capabilities
    except ImportError:
        return None

    try:
        caps = get_wifi7_capabilities(iface)
    except Exception as exc:
        log.debug("wifi7_info failed: %s", exc)
        return None

    freq = link.get("frequency_mhz", 0)
    return {
        "eht_capable": caps.get("eht_capable", False),
        "he_capable": caps.get("he_capable", False),
        "mlo_capable": caps.get("mlo_capable", False),
        "mlo_active": link.get("mlo_active", False),
        "mlo_links": link.get("mlo_links", []),
        "band_6ghz_supported": caps.get("band_6ghz_supported", False),
        "on_6ghz": freq >= 5925 if freq else False,
        "max_bandwidth_mhz": caps.get("max_bandwidth_mhz", 0),
        "preamble_puncturing": caps.get("preamble_puncturing", False),
        "qam4096": caps.get("qam4096", False),
        "regulatory_country": caps.get("regulatory_country", ""),
    }


# ---------------------------------------------------------------------------
# Full metric collection
# ---------------------------------------------------------------------------

def collect_metrics(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """
    Collect one round of metrics and return as nested dict.
    Schema matches telegraf.conf inputs.mqtt_consumer json_v2 definitions.
    """
    iface = cfg["interface"]

    # ── WiFi link ──
    link = parse_iw_link(iface)
    info = parse_iw_info(iface)

    # Merge iw info into link (info has channel/bandwidth, link has rssi/ssid)
    for k in ("channel", "bandwidth_mhz", "center_freq_mhz", "tx_power_dbm"):
        if k in info and k not in link:
            link[k] = info[k]
    if "frequency_mhz" in info:
        link["frequency_mhz"] = info["frequency_mhz"]

    if not link.get("connected"):
        # iwconfig fallback
        fb = fallback_iwconfig(iface)
        if fb:
            link.update(fb)
            link["connected"] = bool(fb.get("ssid") or fb.get("rssi_dbm") is not None)

    freq = link.get("frequency_mhz", 0)
    if freq:
        link["band"] = _classify_band(freq)

    wifi_block: Dict[str, Any] = {}
    for k in ("ssid", "bssid", "channel", "rssi_dbm", "tx_power_dbm",
               "bandwidth_mhz", "frequency_mhz", "band", "standard",
               "tx_rate_mbps", "rx_rate_mbps"):
        if k in link:
            wifi_block[k] = link[k]

    # ── Latency ──
    ping_count = cfg.get("ping_count", 10)

    gateway = cfg.get("ping_gateway", "auto")
    if gateway == "auto":
        gateway = get_default_gateway()

    gw_ping: Dict[str, Any] = {}
    if gateway:
        gw_ping = ping_host(gateway, count=5)

    dns_ping: Dict[str, Any] = {}
    dns_target = cfg.get("ping_dns", "1.1.1.1")
    if dns_target:
        dns_ping = ping_host(dns_target, count=5)

    ext_ping: Dict[str, Any] = {}
    ext_target = cfg.get("ping_external", "8.8.8.8")
    if ext_target:
        ext_ping = ping_host(ext_target, count=ping_count)

    latency_block: Dict[str, Any] = {
        "samples": ping_count,
    }
    if gw_ping.get("reachable"):
        latency_block["gateway_avg_ms"] = gw_ping.get("avg_ms")
        latency_block["gateway_min_ms"] = gw_ping.get("min_ms")
        latency_block["gateway_max_ms"] = gw_ping.get("max_ms")
    else:
        latency_block["gateway_avg_ms"] = None
        latency_block["gateway_min_ms"] = None
        latency_block["gateway_max_ms"] = None

    if dns_ping.get("reachable"):
        latency_block["dns_avg_ms"] = dns_ping.get("avg_ms")
    else:
        latency_block["dns_avg_ms"] = None

    if ext_ping.get("reachable"):
        latency_block["internet_avg_ms"] = ext_ping.get("avg_ms")
        latency_block["internet_min_ms"] = ext_ping.get("min_ms")
        latency_block["internet_max_ms"] = ext_ping.get("max_ms")
        # jitter = mdev (mean deviation of RTT) from ping
        latency_block["jitter_ms"] = ext_ping.get("mdev_ms")
    else:
        latency_block["internet_avg_ms"] = None
        latency_block["internet_min_ms"] = None
        latency_block["internet_max_ms"] = None
        latency_block["jitter_ms"] = None

    # ── Packet loss ──
    sent = ext_ping.get("sent", ping_count)
    received = ext_ping.get("received", 0)
    loss_pct = ext_ping.get("packet_loss_pct", 100.0)
    packet_loss_block: Dict[str, Any] = {
        "percent": loss_pct,
        "sent": sent,
        "received": received,
    }

    # ── Throughput ──
    throughput_block: Dict[str, Any] = {
        "download_kbps": None,
        "success": False,
    }
    if cfg.get("throughput_enabled", True) and cfg.get("throughput_url"):
        t = measure_throughput(cfg["throughput_url"])
        if t.get("download_kbps") is not None:
            throughput_block["download_kbps"] = t["download_kbps"]
            throughput_block["test_bytes"] = t.get("test_bytes", 0)
            throughput_block["test_duration_ms"] = t.get("test_duration_ms", 0)
            throughput_block["success"] = True
        elif t.get("error"):
            throughput_block["error"] = t["error"]
            log.debug("Throughput skipped: %s", t["error"])

    # ── Build final payload ──
    payload: Dict[str, Any] = {
        "probe_id": cfg["probe_id"],
        "timestamp_ms": int(time.time() * 1000),
        "uptime_s": get_uptime_s(),
        "platform": PLATFORM,
        "firmware": VERSION,
        "wifi": wifi_block,
        "latency": latency_block,
        "packet_loss": packet_loss_block,
        "throughput": throughput_block,
    }

    if cfg.get("location"):
        payload["location"] = cfg["location"]

    # ── WiFi 7 extras ──
    if cfg.get("wifi7_monitoring", True):
        w7 = _get_wifi7_metrics(iface, link)
        if w7:
            payload["wifi7"] = w7

    return payload


# ---------------------------------------------------------------------------
# MQTT publisher
# ---------------------------------------------------------------------------

class MQTTPublisher:
    def __init__(self, cfg: Dict[str, Any]):
        self._cfg = cfg
        self._prefix = cfg.get("mqtt_topic_prefix", "labwifimon")
        self._probe_id = cfg["probe_id"]
        self._connected = False
        self._lock = threading.Lock()

        client_id = f"linux-probe-{self._probe_id}"
        self._client = mqtt.Client(client_id=client_id, clean_session=True)
        self._client.on_connect = self._on_connect
        self._client.on_disconnect = self._on_disconnect

        username = cfg.get("mqtt_username")
        password = cfg.get("mqtt_password")
        if username:
            self._client.username_pw_set(username, password)

    def _on_connect(self, client, userdata, flags, rc):
        if rc == 0:
            self._connected = True
            log.info("MQTT connected to %s:%d", self._cfg["mqtt_host"], self._cfg["mqtt_port"])
        else:
            log.error("MQTT connection refused (rc=%d)", rc)

    def _on_disconnect(self, client, userdata, rc):
        self._connected = False
        if rc != 0:
            log.warning("MQTT disconnected unexpectedly (rc=%d)", rc)

    def connect(self):
        self._client.connect_async(
            self._cfg["mqtt_host"],
            self._cfg["mqtt_port"],
            keepalive=60,
        )
        self._client.loop_start()
        deadline = time.monotonic() + 8
        while not self._connected and time.monotonic() < deadline:
            time.sleep(0.1)
        if not self._connected:
            log.warning("MQTT broker not reachable yet — will publish when connected")

    def publish(self, subtopic: str, payload: Dict[str, Any], retain: bool = False):
        topic = f"{self._prefix}/{self._probe_id}/{subtopic}"
        data = json.dumps(payload, default=str)
        result = self._client.publish(topic, data, qos=1, retain=retain)
        if result.rc == mqtt.MQTT_ERR_SUCCESS:
            log.debug("→ %s (%d bytes)", topic, len(data))
        else:
            log.warning("Publish failed rc=%d topic=%s", result.rc, topic)

    def disconnect(self):
        self._client.loop_stop()
        self._client.disconnect()


# ---------------------------------------------------------------------------
# Status heartbeat
# ---------------------------------------------------------------------------

def build_status(cfg: Dict[str, Any], status: str = "online") -> Dict[str, Any]:
    iface = cfg["interface"]
    ip, mac = get_ip_and_mac(iface)
    _, link_out, _ = _run(["iw", "dev", iface, "link"])
    rssi = None
    m = re.search(r"signal: (-?\d+)\s*dBm", link_out)
    if m:
        rssi = int(m.group(1))

    return {
        "probe_id": cfg["probe_id"],
        "timestamp_ms": int(time.time() * 1000),
        "status": status,
        "firmware": VERSION,
        "platform": PLATFORM,
        "ip_address": ip or "",
        "mac_address": mac or "",
        "uptime_s": get_uptime_s(),
        "wifi_rssi_dbm": rssi,
    }


# ---------------------------------------------------------------------------
# Main probe loop
# ---------------------------------------------------------------------------

class LinuxProbe:
    def __init__(self, cfg: Dict[str, Any]):
        self._cfg = cfg
        self._running = False
        self._last_scan: Dict[str, Any] = {}
        self._scan_lock = threading.Lock()
        self._mqtt = MQTTPublisher(cfg)

    def _scan_worker(self):
        interval = self._cfg.get("scan_interval_seconds", 300)
        while self._running:
            log.debug("Starting BSS scan on %s…", self._cfg["interface"])
            scan = scan_bss(self._cfg["interface"])
            with self._scan_lock:
                self._last_scan = scan
            count = scan.get("count", 0)
            log.info("BSS scan complete: %d networks", count)

            for _ in range(interval * 10):
                if not self._running:
                    return
                time.sleep(0.1)

    def _heartbeat_worker(self):
        interval = self._cfg.get("heartbeat_interval_seconds", 60)
        while self._running:
            for _ in range(interval * 10):
                if not self._running:
                    return
                time.sleep(0.1)
            status = build_status(self._cfg)
            self._mqtt.publish("status", status, retain=True)
            log.debug("Heartbeat published")

    def start(self):
        self._running = True
        self._mqtt.connect()

        # Publish initial online status
        self._mqtt.publish("status", build_status(self._cfg), retain=True)

        scan_t = threading.Thread(
            target=self._scan_worker, daemon=True, name="bss-scan"
        )
        hb_t = threading.Thread(
            target=self._heartbeat_worker, daemon=True, name="heartbeat"
        )
        scan_t.start()
        hb_t.start()

        log.info(
            "Probe started: id=%s interface=%s interval=%ds",
            self._cfg["probe_id"],
            self._cfg["interface"],
            self._cfg["interval_seconds"],
        )

        while self._running:
            t0 = time.monotonic()
            try:
                metrics = collect_metrics(self._cfg)

                # Attach latest scan results to metrics
                with self._scan_lock:
                    if self._last_scan:
                        metrics["scan_summary"] = {
                            "count": self._last_scan.get("count", 0),
                            "scan_duration_ms": self._last_scan.get("scan_duration_ms"),
                        }

                self._mqtt.publish("metrics", metrics)

                log.info(
                    "rssi=%s  gw=%sms  internet=%sms  jitter=%sms  loss=%s%%  tput=%skbps",
                    metrics["wifi"].get("rssi_dbm"),
                    metrics["latency"].get("gateway_avg_ms"),
                    metrics["latency"].get("internet_avg_ms"),
                    metrics["latency"].get("jitter_ms"),
                    metrics["packet_loss"].get("percent"),
                    metrics["throughput"].get("download_kbps"),
                )

                # Also publish scan payload separately when fresh
                with self._scan_lock:
                    scan_payload = dict(self._last_scan)
                if scan_payload and scan_payload.get("count", 0) > 0:
                    scan_payload["probe_id"] = self._cfg["probe_id"]
                    scan_payload["timestamp_ms"] = int(time.time() * 1000)
                    self._mqtt.publish("scan", scan_payload)

            except Exception as exc:
                log.error("Collection error: %s", exc, exc_info=True)

            elapsed = time.monotonic() - t0
            sleep_remaining = max(0.0, self._cfg["interval_seconds"] - elapsed)
            for _ in range(int(sleep_remaining * 10)):
                if not self._running:
                    break
                time.sleep(0.1)

    def stop(self):
        log.info("Stopping probe…")
        self._running = False
        try:
            self._mqtt.publish("status", build_status(self._cfg, status="offline"), retain=True)
        except Exception:
            pass
        self._mqtt.disconnect()


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

def load_config(config_path: Optional[str] = None) -> Dict[str, Any]:
    """Load config.yaml, then apply environment variable overrides."""
    cfg = dict(DEFAULT_CONFIG)

    search = [config_path, "config.yaml", "/etc/labwifimon/config.yaml"]
    for path in search:
        if path and os.path.isfile(path):
            with open(path) as f:
                file_cfg = yaml.safe_load(f) or {}
            cfg.update({k: v for k, v in file_cfg.items() if v is not None})
            log.debug("Loaded config: %s", path)
            break

    # Environment overrides — LABWIFIMON_<UPPER_KEY>
    env_overrides = {
        "LABWIFIMON_INTERFACE": ("interface", str),
        "LABWIFIMON_PROBE_ID": ("probe_id", str),
        "LABWIFIMON_LOCATION": ("location", str),
        "LABWIFIMON_MQTT_HOST": ("mqtt_host", str),
        "LABWIFIMON_MQTT_PORT": ("mqtt_port", int),
        "LABWIFIMON_MQTT_USERNAME": ("mqtt_username", str),
        "LABWIFIMON_MQTT_PASSWORD": ("mqtt_password", str),
        "LABWIFIMON_PING_GATEWAY": ("ping_gateway", str),
        "LABWIFIMON_PING_EXTERNAL": ("ping_external", str),
        "LABWIFIMON_THROUGHPUT_URL": ("throughput_url", str),
        "LABWIFIMON_INTERVAL": ("interval_seconds", int),
        "LABWIFIMON_SCAN_INTERVAL": ("scan_interval_seconds", int),
        "LABWIFIMON_LOG_LEVEL": ("log_level", str),
    }
    for env_key, (cfg_key, cast) in env_overrides.items():
        val = os.environ.get(env_key)
        if val is not None:
            try:
                cfg[cfg_key] = cast(val)
            except ValueError:
                log.warning("Invalid env %s=%r — ignoring", env_key, val)

    # Defaults that need runtime resolution
    if not cfg.get("probe_id"):
        cfg["probe_id"] = socket.gethostname()
    if not cfg.get("interface"):
        cfg["interface"] = detect_interface()
        log.info("Auto-detected interface: %s", cfg["interface"])

    return cfg


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="LabWiFiMon Linux Probe — WiFi quality monitoring"
    )
    parser.add_argument("-c", "--config", metavar="FILE", help="Path to config.yaml")
    parser.add_argument("-v", "--verbose", action="store_true", help="Debug logging")
    parser.add_argument(
        "--once", action="store_true", help="Collect one sample and exit"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print metrics JSON to stdout, do not publish",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(name)-20s %(levelname)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )

    cfg = load_config(args.config)
    if not args.verbose:
        logging.getLogger().setLevel(cfg.get("log_level", "INFO").upper())

    log.info("LabWiFiMon Linux Probe v%s  probe_id=%s  interface=%s",
             VERSION, cfg["probe_id"], cfg["interface"])

    if args.dry_run or args.once:
        metrics = collect_metrics(cfg)
        print(json.dumps(metrics, indent=2, default=str))
        return

    probe = LinuxProbe(cfg)

    def _signal_handler(sig, frame):
        log.info("Signal %d — shutting down", sig)
        probe.stop()
        sys.exit(0)

    signal.signal(signal.SIGTERM, _signal_handler)
    signal.signal(signal.SIGINT, _signal_handler)

    probe.start()


if __name__ == "__main__":
    main()
