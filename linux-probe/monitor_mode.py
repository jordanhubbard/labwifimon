#!/usr/bin/env python3
"""
LabWiFiMon — Monitor Mode Frame Scanner (optional, advanced).

Sets a WiFi interface to monitor mode and captures 802.11 management frames
(beacons, probe requests/responses).  Extracts HT/VHT/HE/EHT capabilities,
beacon intervals, and supported rates.  Hops across 2.4 GHz, 5 GHz, and
optionally 6 GHz channels.  Publishes frame statistics to MQTT.

MQTT topic published:
  labwifimon/<probe_id>/frames  — beacon/network summary (every scan_interval_seconds)

Requirements:
  pip install scapy          # frame parsing
  sudo / CAP_NET_RAW         # monitor mode + raw socket access

WARNING:
  Monitor mode takes the selected interface OFFLINE for normal WiFi traffic.
  Always use a DEDICATED second adapter (USB dongle or second M.2 card).
  The original interface is restored on clean shutdown (SIGINT / SIGTERM).

Usage:
  sudo python3 monitor_mode.py -i wlan1              # use wlan1 for capture
  sudo python3 monitor_mode.py -i wlan1 --6ghz       # also hop 6 GHz channels
  sudo python3 monitor_mode.py -i wlan1 -c config.yaml
"""

import argparse
import json
import logging
import os
import re
import signal
import subprocess
import sys
import time
import threading
from collections import defaultdict
from typing import Any, Dict, List, Optional, Tuple

import paho.mqtt.client as mqtt
import yaml

log = logging.getLogger("labwifimon.monitor")

# ---------------------------------------------------------------------------
# Channel tables
# ---------------------------------------------------------------------------

CHANNELS_2G = list(range(1, 14))         # 1–13
CHANNELS_5G = [
    36, 40, 44, 48, 52, 56, 60, 64,      # UNII-1/2
    100, 104, 108, 112, 116, 120, 124,    # UNII-2 extended
    128, 132, 136, 140, 144,
    149, 153, 157, 161, 165,              # UNII-3
]
CHANNELS_6G = [                          # WiFi 6E / 7 PSC channels
    1, 5, 9, 13, 17, 21, 25, 29, 33, 37,
    41, 45, 49, 53, 57, 61, 65, 69, 73,
    77, 81, 85, 89, 93,
]

DWELL_MS = 200   # milliseconds per channel before hopping

# ---------------------------------------------------------------------------
# Subprocess helper
# ---------------------------------------------------------------------------

def _run(args: List[str], timeout: int = 10) -> Tuple[int, str, str]:
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout, r.stderr
    except Exception as exc:
        return -1, "", str(exc)

# ---------------------------------------------------------------------------
# Privilege check
# ---------------------------------------------------------------------------

def require_root() -> None:
    if os.geteuid() != 0:
        log.error(
            "Monitor mode requires root (or CAP_NET_RAW + CAP_NET_ADMIN).\n"
            "Re-run with: sudo python3 monitor_mode.py ..."
        )
        sys.exit(1)

# ---------------------------------------------------------------------------
# Monitor mode control
# ---------------------------------------------------------------------------

def set_monitor_mode(iface: str) -> bool:
    """Put iface into monitor mode.  Returns True on success."""
    log.info("Setting %s to monitor mode…", iface)
    _run(["ip", "link", "set", iface, "down"])

    rc, _, err = _run(["iw", "dev", iface, "set", "type", "monitor"])
    if rc != 0:
        # Some drivers need iwconfig instead
        log.debug("iw failed (%s), trying iwconfig…", err.strip())
        rc, _, err = _run(["iwconfig", iface, "mode", "monitor"])
        if rc != 0:
            log.error("Failed to set monitor mode on %s: %s", iface, err.strip())
            return False

    _run(["ip", "link", "set", iface, "up"])
    log.info("%s is now in monitor mode", iface)
    return True


def restore_managed_mode(iface: str) -> None:
    """Restore iface to managed (infrastructure) mode."""
    log.info("Restoring %s to managed mode…", iface)
    _run(["ip", "link", "set", iface, "down"])
    _run(["iw", "dev", iface, "set", "type", "managed"])
    _run(["ip", "link", "set", iface, "up"])
    log.info("%s restored to managed mode", iface)


def set_channel(iface: str, channel: int) -> bool:
    rc, _, _ = _run(["iw", "dev", iface, "set", "channel", str(channel)])
    return rc == 0

# ---------------------------------------------------------------------------
# Frame statistics accumulator
# ---------------------------------------------------------------------------

class FrameStats:
    """Thread-safe store for 802.11 management frame counters and beacon info."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._networks: Dict[str, Dict[str, Any]] = {}  # keyed by BSSID
        self._probe_requests = 0
        self._probe_responses = 0
        self._total_frames = 0
        self._start_time = time.time()

    def record_beacon(
        self,
        bssid: str,
        ssid: str,
        channel: int,
        rssi: int,
        beacon_interval: int,
        rates_mbps: List[float],
        caps: Dict[str, Any],
    ) -> None:
        with self._lock:
            self._total_frames += 1
            existing = self._networks.get(bssid, {})
            # Keep running RSSI average (simple EMA α=0.3)
            prev_rssi = existing.get("rssi", rssi)
            smooth_rssi = round(0.3 * rssi + 0.7 * prev_rssi)
            self._networks[bssid] = {
                "bssid": bssid,
                "ssid": ssid or existing.get("ssid", ""),
                "channel": channel or existing.get("channel", 0),
                "rssi": smooth_rssi,
                "beacon_interval_tu": beacon_interval,
                "rates_mbps": rates_mbps or existing.get("rates_mbps", []),
                "last_seen_s": int(time.time()),
                **caps,
            }

    def record_probe_request(self) -> None:
        with self._lock:
            self._probe_requests += 1
            self._total_frames += 1

    def record_probe_response(self) -> None:
        with self._lock:
            self._probe_responses += 1
            self._total_frames += 1

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            elapsed = max(time.time() - self._start_time, 1.0)
            sorted_nets = sorted(
                self._networks.values(),
                key=lambda n: n.get("rssi", -999),
                reverse=True,
            )
            return {
                "timestamp_ms": int(time.time() * 1000),
                "elapsed_s": round(elapsed, 1),
                "total_frames": self._total_frames,
                "unique_bss": len(self._networks),
                "probe_requests": self._probe_requests,
                "probe_responses": self._probe_responses,
                "fps": round(self._total_frames / elapsed, 1),
                "networks": sorted_nets[:50],
            }

    def reset(self) -> None:
        """Reset per-interval counters (keeps network table intact)."""
        with self._lock:
            self._probe_requests = 0
            self._probe_responses = 0
            self._total_frames = 0
            self._start_time = time.time()

# ---------------------------------------------------------------------------
# Scapy integration (optional)
# ---------------------------------------------------------------------------

try:
    from scapy.all import (
        sniff,
        Dot11,
        Dot11Beacon,
        Dot11ProbeReq,
        Dot11ProbeResp,
        Dot11Elt,
        RadioTap,
    )
    _SCAPY_OK = True
except ImportError:
    _SCAPY_OK = False
    log.debug("scapy not installed — frame capture unavailable")


def _rssi_from_radiotap(pkt: Any) -> int:
    """Extract RSSI (dBm) from RadioTap header."""
    try:
        rt = pkt.getlayer(RadioTap)  # type: ignore[union-attr]
        if hasattr(rt, "dBm_AntSignal"):
            v = rt.dBm_AntSignal
            # Some drivers report as unsigned byte (e.g. 191 → -65)
            if isinstance(v, int) and v > 127:
                v = v - 256
            return int(v)
    except Exception:
        pass
    return -99


def _parse_ies(pkt: Any) -> Tuple[str, int, List[float], int, Dict[str, Any]]:
    """
    Walk the 802.11 Information Elements chain.
    Returns (ssid, channel, rates_mbps, beacon_interval_tu, capabilities).
    """
    ssid = ""
    channel = 0
    rates: List[float] = []
    beacon_interval = 0
    caps: Dict[str, Any] = {}

    try:
        # Beacon interval is in the fixed parameters, before IEs
        dot11_beacon = pkt.getlayer(Dot11Beacon)  # type: ignore[union-attr]
        if dot11_beacon and hasattr(dot11_beacon, "beacon_interval"):
            beacon_interval = int(dot11_beacon.beacon_interval)
    except Exception:
        pass

    elt = pkt.getlayer(Dot11Elt)  # type: ignore[union-attr]
    while elt is not None:
        try:
            eid = elt.ID
            info = bytes(elt.info) if elt.info else b""

            # SSID (0)
            if eid == 0:
                ssid = info.decode("utf-8", errors="replace").rstrip("\x00")

            # DS parameter set = current channel (3)
            elif eid == 3 and info:
                channel = info[0]

            # Supported rates (1) and Extended supported rates (50)
            elif eid in (1, 50):
                for byte in info:
                    rate = (byte & 0x7F) * 0.5
                    if rate not in rates:
                        rates.append(rate)

            # HT capabilities (45) — 802.11n
            elif eid == 45 and len(info) >= 2:
                caps["ht_capable"] = True
                ht_cap = int.from_bytes(info[:2], "little")
                caps["ht_40mhz"] = bool(ht_cap & (1 << 1))
                caps["ht_sgi_20"] = bool(ht_cap & (1 << 5))
                caps["ht_sgi_40"] = bool(ht_cap & (1 << 6))

            # VHT capabilities (191) — 802.11ac
            elif eid == 191 and len(info) >= 4:
                caps["vht_capable"] = True
                vht_cap = int.from_bytes(info[:4], "little")
                sup_width = (vht_cap >> 2) & 0x3
                caps["vht_160mhz"] = sup_width in (1, 2)

            # Extended IEs (255) — identified by first byte extension ID
            elif eid == 255 and info:
                ext_id = info[0]

                # HE Capabilities (35) — 802.11ax / WiFi 6
                if ext_id == 35:
                    caps["he_capable"] = True

                # EHT Capabilities (108) — 802.11be / WiFi 7
                elif ext_id == 108:
                    caps["eht_capable"] = True
                    if len(info) >= 4:
                        # PHY cap byte 1 has 320 MHz support flag (bit 1)
                        phy_byte = info[3] if len(info) > 3 else 0
                        caps["eht_320mhz"] = bool(phy_byte & 0x02)

                # Multi-Link element (107) — MLO
                elif ext_id == 107:
                    caps["mlo_capable"] = True

        except Exception:
            pass

        try:
            elt = elt.payload.getlayer(Dot11Elt)  # type: ignore[union-attr]
        except Exception:
            break

    rates.sort(reverse=True)
    return ssid, channel, rates, beacon_interval, caps


def _process_frame(pkt: Any, stats: FrameStats) -> None:
    """Dispatch a captured frame to the appropriate handler."""
    try:
        if pkt.haslayer(Dot11Beacon):  # type: ignore[union-attr]
            dot11 = pkt.getlayer(Dot11)  # type: ignore[union-attr]
            bssid = dot11.addr3 if dot11 else "00:00:00:00:00:00"
            rssi = _rssi_from_radiotap(pkt)
            ssid, channel, rates, bint, caps = _parse_ies(pkt)
            stats.record_beacon(bssid, ssid, channel, rssi, bint, rates, caps)

        elif pkt.haslayer(Dot11ProbeReq):  # type: ignore[union-attr]
            stats.record_probe_request()

        elif pkt.haslayer(Dot11ProbeResp):  # type: ignore[union-attr]
            stats.record_probe_response()

    except Exception as exc:
        log.debug("Frame processing error: %s", exc)

# ---------------------------------------------------------------------------
# Channel hopper thread
# ---------------------------------------------------------------------------

class ChannelHopper(threading.Thread):
    """Cycles through WiFi channels at DWELL_MS intervals."""

    def __init__(self, iface: str, enable_6ghz: bool = False) -> None:
        super().__init__(daemon=True, name="channel-hopper")
        self.iface = iface
        self._running = False
        channels = CHANNELS_2G + CHANNELS_5G
        if enable_6ghz:
            channels += CHANNELS_6G
        self._channels = channels

    def run(self) -> None:
        self._running = True
        idx = 0
        dwell = DWELL_MS / 1000.0
        while self._running:
            ch = self._channels[idx % len(self._channels)]
            set_channel(self.iface, ch)
            time.sleep(dwell)
            idx += 1

    def stop(self) -> None:
        self._running = False

# ---------------------------------------------------------------------------
# MQTT publisher helper
# ---------------------------------------------------------------------------

def _make_mqtt_client(cfg: Dict[str, Any]) -> mqtt.Client:
    probe_id = cfg.get("probe_id", "pi-probe-1")
    client = mqtt.Client(client_id=f"monitor-{probe_id}", clean_session=True)
    username = cfg.get("mqtt_username")
    if username:
        client.username_pw_set(username, cfg.get("mqtt_password", ""))
    client.connect(cfg.get("mqtt_host", "localhost"), cfg.get("mqtt_port", 1883), keepalive=60)
    client.loop_start()
    return client

# ---------------------------------------------------------------------------
# MonitorScanner
# ---------------------------------------------------------------------------

class MonitorScanner:
    def __init__(self, cfg: Dict[str, Any], iface: str, enable_6ghz: bool = False) -> None:
        self._cfg = cfg
        self._iface = iface
        self._enable_6ghz = enable_6ghz
        self._stats = FrameStats()
        self._running = False
        self._hopper: Optional[ChannelHopper] = None
        self._mqtt_client: Optional[mqtt.Client] = None

    def _publish(self, payload: Dict[str, Any]) -> None:
        if not self._mqtt_client:
            return
        probe_id = self._cfg.get("probe_id", "pi-probe-1")
        prefix = self._cfg.get("mqtt_topic_prefix", "labwifimon")
        topic = f"{prefix}/{probe_id}/frames"
        data = json.dumps(payload, default=str)
        result = self._mqtt_client.publish(topic, data, qos=1)
        if result.rc == mqtt.MQTT_ERR_SUCCESS:
            log.info("Published %d BSSes / %d frames → %s",
                     payload["unique_bss"], payload["total_frames"], topic)
        else:
            log.warning("Publish failed rc=%d", result.rc)

    def _publish_loop(self) -> None:
        interval = self._cfg.get("scan_interval_seconds", 60)
        while self._running:
            for _ in range(interval * 10):
                if not self._running:
                    return
                time.sleep(0.1)
            snap = self._stats.snapshot()
            self._publish(snap)
            self._stats.reset()

    def start(self) -> None:
        require_root()

        if not _SCAPY_OK:
            log.error(
                "scapy is not installed.\n"
                "Install it with:  pip install scapy\n"
                "Then re-run this script."
            )
            sys.exit(1)

        if not set_monitor_mode(self._iface):
            sys.exit(1)

        try:
            self._mqtt_client = _make_mqtt_client(self._cfg)
        except Exception as exc:
            log.warning("MQTT connect failed (%s) — will retry in background", exc)

        self._hopper = ChannelHopper(self._iface, self._enable_6ghz)
        self._hopper.start()
        self._running = True

        pub_thread = threading.Thread(
            target=self._publish_loop, daemon=True, name="frame-publisher"
        )
        pub_thread.start()

        def _shutdown(sig: int, frame: Any) -> None:
            log.info("Signal %d — shutting down monitor scanner", sig)
            self._running = False
            if self._hopper:
                self._hopper.stop()
            restore_managed_mode(self._iface)
            if self._mqtt_client:
                self._mqtt_client.loop_stop()
                self._mqtt_client.disconnect()
            sys.exit(0)

        signal.signal(signal.SIGTERM, _shutdown)
        signal.signal(signal.SIGINT, _shutdown)

        log.info(
            "Capturing 802.11 management frames on %s (%s) — Ctrl+C to stop",
            self._iface,
            "2.4+5+6 GHz" if self._enable_6ghz else "2.4+5 GHz",
        )

        # sniff() is blocking — runs until process is killed
        sniff(  # type: ignore[name-defined]
            iface=self._iface,
            prn=lambda pkt: _process_frame(pkt, self._stats),
            store=False,
        )

# ---------------------------------------------------------------------------
# Config loader
# ---------------------------------------------------------------------------

def _load_config(path: str) -> Dict[str, Any]:
    defaults: Dict[str, Any] = {
        "probe_id": "pi-probe-1",
        "mqtt_host": "localhost",
        "mqtt_port": 1883,
        "mqtt_topic_prefix": "labwifimon",
        "scan_interval_seconds": 60,
        "wifi7_monitoring": False,
    }
    if os.path.isfile(path):
        with open(path) as f:
            file_cfg = yaml.safe_load(f) or {}
        defaults.update(file_cfg)
    return defaults

# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="LabWiFiMon Monitor Mode Frame Scanner",
        epilog="WARNING: the capture interface will be taken offline for normal traffic.",
    )
    parser.add_argument(
        "-i", "--interface", required=True,
        help="Dedicated monitor-mode interface (e.g. wlan1, wlan2)"
    )
    parser.add_argument(
        "-c", "--config", default="config.yaml",
        help="Path to config.yaml (default: config.yaml)"
    )
    parser.add_argument(
        "--6ghz", dest="enable_6ghz", action="store_true",
        help="Also hop 6 GHz channels (WiFi 6E/7)"
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(name)-22s %(levelname)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )

    cfg = _load_config(args.config)

    scanner = MonitorScanner(cfg, iface=args.interface, enable_6ghz=args.enable_6ghz)
    scanner.start()


if __name__ == "__main__":
    main()
