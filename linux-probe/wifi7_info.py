#!/usr/bin/env python3
"""
LabWiFiMon — WiFi 7 (802.11be) capability detection.

Parses 'iw phy <phy> info' output to detect:
  - EHT (802.11be / WiFi 7) capabilities
  - HE (802.11ax / WiFi 6/6E) capabilities
  - MLO (Multi-Link Operation) status
  - 320 MHz channel support
  - 4096-QAM (MCS 12/13) support
  - 6 GHz band availability and regulatory status
  - Preamble puncturing support

Imported by probe.py when wifi7_monitoring is enabled.
Can also be run standalone for a human-readable capability report:
  python3 wifi7_info.py [interface]
  python3 wifi7_info.py --json wlan0
"""

import json
import logging
import re
import subprocess
import sys
from typing import Any, Dict, List, Optional, Tuple

log = logging.getLogger("labwifimon.wifi7")


# ---------------------------------------------------------------------------
# Subprocess helper
# ---------------------------------------------------------------------------

def _run(args: List[str], timeout: int = 15) -> Tuple[int, str, str]:
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout, r.stderr
    except subprocess.TimeoutExpired:
        return -1, "", "timeout"
    except FileNotFoundError:
        return -1, "", f"{args[0]}: not found"
    except Exception as exc:
        return -1, "", str(exc)


# ---------------------------------------------------------------------------
# PHY resolution
# ---------------------------------------------------------------------------

def get_phy_for_iface(iface: str) -> Optional[str]:
    """Return 'phyN' for the given interface, or None."""
    rc, out, _ = _run(["iw", "dev", iface, "info"])
    if rc != 0:
        return None
    m = re.search(r"wiphy (\d+)", out)
    return f"phy{m.group(1)}" if m else None


# ---------------------------------------------------------------------------
# Band parsing
# ---------------------------------------------------------------------------

def parse_bands(phy_out: str) -> List[Dict[str, Any]]:
    """
    Parse 'Band N:' sections from iw phy output.
    Returns list of band dicts: {band_num, band_name, channels, active_channels}.
    """
    bands: List[Dict[str, Any]] = []
    current: Optional[Dict[str, Any]] = None

    for line in phy_out.splitlines():
        stripped = line.strip()

        m = re.match(r"Band (\d+):", stripped)
        if m:
            if current is not None:
                bands.append(current)
            current = {
                "band_num": int(m.group(1)),
                "channels": [],
                "active_channels": 0,
            }
            continue

        if current is None:
            continue

        # Frequency / channel entry:  "* 5180 MHz [36] (20.0 dBm)"
        fm = re.match(r"\* (\d+) MHz \[(\d+)\](.*)", stripped)
        if fm:
            freq = int(fm.group(1))
            channel = int(fm.group(2))
            details = fm.group(3)
            disabled = "disabled" in details.lower() or "no IR" in details

            current["channels"].append({
                "freq_mhz": freq,
                "channel": channel,
                "disabled": disabled,
            })
            if not disabled:
                current["active_channels"] += 1

    if current is not None:
        bands.append(current)

    # Classify bands by frequency range
    for band in bands:
        freqs = [c["freq_mhz"] for c in band.get("channels", [])]
        if not freqs:
            band["band_name"] = "unknown"
            continue
        min_freq = min(freqs)
        if min_freq < 3000:
            band["band_name"] = "2.4GHz"
        elif min_freq < 5925:
            band["band_name"] = "5GHz"
        else:
            band["band_name"] = "6GHz"

    return bands


# ---------------------------------------------------------------------------
# EHT capability parsing (WiFi 7 / 802.11be)
# ---------------------------------------------------------------------------

def parse_eht_caps(phy_out: str) -> Dict[str, Any]:
    """
    Scan iw phy output for EHT (802.11be) capability blocks.
    Returns dict with eht_capable, qam4096, preamble_puncturing, max_bandwidth_mhz.
    """
    caps: Dict[str, Any] = {
        "eht_capable": False,
        "qam4096": False,
        "preamble_puncturing": False,
        "max_bandwidth_mhz": 0,
    }

    in_eht = False
    max_bw = 0

    for line in phy_out.splitlines():
        s = line.strip()

        # EHT section header
        if re.search(r"EHT\s+(Iftypes|MAC|PHY|Capabilities)", s, re.IGNORECASE):
            in_eht = True
            caps["eht_capable"] = True

        if not in_eht:
            continue

        # Exit EHT block when we hit a non-EHT section
        if re.match(r"(Band \d+|VHT|HT|HE) ", s) and "EHT" not in s:
            in_eht = False
            continue

        # 4096-QAM: present in EHT MCS 12–13 entries
        if re.search(r"MCS[- ]1[23]|4096.?QAM", s, re.IGNORECASE):
            caps["qam4096"] = True

        # Preamble puncturing
        if re.search(r"preamble.?punct", s, re.IGNORECASE):
            caps["preamble_puncturing"] = True

        # Channel widths — track maximum seen
        for bw in (320, 160, 80, 40, 20):
            if re.search(rf"\b{bw}\s*MHz", s):
                if bw > max_bw:
                    max_bw = bw
                break

    if max_bw:
        caps["max_bandwidth_mhz"] = max_bw

    return caps


# ---------------------------------------------------------------------------
# HE capability parsing (WiFi 6 / 802.11ax)
# ---------------------------------------------------------------------------

def parse_he_caps(phy_out: str) -> Dict[str, Any]:
    """Parse HE (802.11ax) capabilities as fallback when EHT is not present."""
    caps: Dict[str, Any] = {
        "he_capable": False,
        "max_bandwidth_mhz": 80,
    }

    in_he = False

    for line in phy_out.splitlines():
        s = line.strip()

        if re.search(r"HE\s+(Iftypes|MAC|PHY|Capabilities)", s, re.IGNORECASE):
            in_he = True
            caps["he_capable"] = True

        if not in_he:
            continue

        # 160 MHz capable
        if re.search(r"\b160\s*MHz", s):
            caps["max_bandwidth_mhz"] = 160

    return caps


# ---------------------------------------------------------------------------
# MLO detection
# ---------------------------------------------------------------------------

def get_mlo_status(iface: str) -> Dict[str, Any]:
    """
    Check if the associated AP is using MLO (Multi-Link Operation).
    Parses 'iw dev <iface> link' for link[N] blocks present in WiFi 7 connections.
    """
    rc, out, _ = _run(["iw", "dev", iface, "link"])
    result: Dict[str, Any] = {
        "mlo_active": False,
        "mlo_capable": False,
        "link_count": 0,
        "links": [],
    }

    if rc != 0 or "Not connected" in out:
        return result

    # WiFi 7 kernel drivers report individual links as:
    #   link[0] ... addr aa:bb:cc:...
    #              freq: 6115
    #              signal: -45 dBm
    link_blocks = re.findall(
        r"link\[(\d+)\].*?addr\s+([0-9a-f:]{17})",
        out,
        re.IGNORECASE | re.DOTALL,
    )

    if link_blocks:
        # Also grab per-link freq and signal (may not always be present)
        details = re.findall(
            r"link\[(\d+)\].*?freq:\s*(\d+).*?signal:\s*(-?\d+)",
            out,
            re.IGNORECASE | re.DOTALL,
        )
        link_map = {int(lid): {"freq_mhz": int(f), "rssi_dbm": int(s)}
                    for lid, f, s in details}

        links = []
        for lid, bssid in link_blocks:
            lid_i = int(lid)
            entry: Dict[str, Any] = {"id": lid_i, "bssid": bssid}
            if lid_i in link_map:
                entry.update(link_map[lid_i])
            links.append(entry)

        result["mlo_active"] = True
        result["mlo_capable"] = True
        result["link_count"] = len(links)
        result["links"] = links

    return result


# ---------------------------------------------------------------------------
# Regulatory info
# ---------------------------------------------------------------------------

def get_regulatory_info() -> Dict[str, Any]:
    """Parse 'iw reg get' for country code and 6 GHz permission."""
    rc, out, _ = _run(["iw", "reg", "get"])
    result: Dict[str, Any] = {"country": "00", "6ghz_allowed": False}
    if rc != 0:
        return result

    m = re.search(r"country (\w{2}):", out)
    if m:
        result["country"] = m.group(1)

    # 6 GHz frequencies start at 5925 MHz; look for any entry in that range.
    if re.search(r"5925\d*\s*-\s*7\d{3}", out):
        result["6ghz_allowed"] = True

    return result


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_wifi7_capabilities(iface: str) -> Dict[str, Any]:
    """
    Return a structured dict of WiFi capabilities for the given interface.

    Keys:
      eht_capable        bool  — 802.11be / WiFi 7 supported
      he_capable         bool  — 802.11ax / WiFi 6 supported
      mlo_capable        bool  — MLO reported by driver
      mlo_active         bool  — currently connected via MLO
      mlo_links          list  — [{id, bssid, freq_mhz, rssi_dbm}]
      band_6ghz_supported bool — 6 GHz band present in phy
      max_bandwidth_mhz  int   — highest reported channel width (MHz)
      preamble_puncturing bool — EHT preamble puncturing supported
      qam4096            bool  — EHT MCS 12/13 (4096-QAM) supported
      regulatory_country str   — two-letter regulatory domain
      6ghz_regulatory_allowed bool
      phy                str   — 'phyN' backing device
      bands              list  — ['2.4GHz', '5GHz', '6GHz']
    """
    result: Dict[str, Any] = {
        "eht_capable": False,
        "he_capable": False,
        "mlo_capable": False,
        "mlo_active": False,
        "mlo_links": [],
        "band_6ghz_supported": False,
        "max_bandwidth_mhz": 0,
        "preamble_puncturing": False,
        "qam4096": False,
        "regulatory_country": "00",
        "6ghz_regulatory_allowed": False,
        "phy": None,
        "bands": [],
    }

    phy = get_phy_for_iface(iface)
    if not phy:
        log.warning("Cannot determine PHY for %s — iw not available?", iface)
        return result

    result["phy"] = phy

    rc, phy_out, _ = _run(["iw", "phy", phy, "info"], timeout=10)
    if rc != 0:
        log.warning("iw phy %s info failed", phy)
        return result

    # Band support
    bands = parse_bands(phy_out)
    band_names = [b["band_name"] for b in bands if "band_name" in b]
    result["bands"] = band_names
    result["band_6ghz_supported"] = "6GHz" in band_names

    # EHT (WiFi 7)
    eht = parse_eht_caps(phy_out)
    result["eht_capable"] = eht["eht_capable"]
    result["qam4096"] = eht["qam4096"]
    result["preamble_puncturing"] = eht["preamble_puncturing"]

    if eht["eht_capable"]:
        bw = eht["max_bandwidth_mhz"]
        # WiFi 7 on 6 GHz can do 320 MHz; if iw didn't report it, infer
        if bw == 0 and result["band_6ghz_supported"]:
            bw = 320 if "320" in phy_out else 160
        result["max_bandwidth_mhz"] = bw
    else:
        # HE (WiFi 6/6E) fallback
        he = parse_he_caps(phy_out)
        result["he_capable"] = he["he_capable"]
        result["max_bandwidth_mhz"] = he["max_bandwidth_mhz"]

    # MLO status from current connection
    mlo = get_mlo_status(iface)
    result["mlo_active"] = mlo["mlo_active"]
    # A WiFi 7 EHT-capable card is MLO-capable even if not currently connected
    result["mlo_capable"] = mlo["mlo_capable"] or eht["eht_capable"]
    if mlo.get("links"):
        result["mlo_links"] = mlo["links"]

    # Regulatory
    reg = get_regulatory_info()
    result["regulatory_country"] = reg["country"]
    result["6ghz_regulatory_allowed"] = reg["6ghz_allowed"]

    return result


# ---------------------------------------------------------------------------
# Standalone report
# ---------------------------------------------------------------------------

def print_report(iface: str) -> None:
    """Print a human-readable WiFi capability report."""
    caps = get_wifi7_capabilities(iface)

    phy_label = caps.get("phy") or "unknown phy"
    print(f"\n{'━'*62}")
    print(f"  WiFi Capability Report — {iface}  ({phy_label})")
    print(f"{'━'*62}")

    if caps["eht_capable"]:
        standard = "WiFi 7  (802.11be / EHT)"
    elif caps["he_capable"] and caps["band_6ghz_supported"]:
        standard = "WiFi 6E (802.11ax / HE + 6 GHz)"
    elif caps["he_capable"]:
        standard = "WiFi 6  (802.11ax / HE)"
    else:
        standard = "Pre-WiFi 6"

    bands_str = ", ".join(caps["bands"]) if caps["bands"] else "unknown"
    max_bw = f"{caps['max_bandwidth_mhz']} MHz" if caps["max_bandwidth_mhz"] else "unknown"

    rows = [
        ("Standard",          standard),
        ("PHY device",        phy_label),
        ("Bands",             bands_str),
        ("6 GHz supported",   "Yes" if caps["band_6ghz_supported"] else "No"),
        ("6 GHz (regulatory)","Yes" if caps["6ghz_regulatory_allowed"] else "No"),
        ("Regulatory domain", caps["regulatory_country"]),
        ("Max channel width", max_bw),
        ("4096-QAM (MCS12/13)", "Yes" if caps["qam4096"] else "No"),
        ("Preamble puncturing", "Yes" if caps["preamble_puncturing"] else "No"),
        ("MLO capable",       "Yes" if caps["mlo_capable"] else "No"),
        ("MLO active",        "Yes" if caps["mlo_active"] else "No"),
    ]
    width = max(len(r[0]) for r in rows)
    for label, value in rows:
        print(f"  {label:<{width}}  {value}")

    if caps.get("mlo_links"):
        print(f"\n  Active MLO Links ({len(caps['mlo_links'])}):")
        for link in caps["mlo_links"]:
            freq = link.get("freq_mhz", 0)
            if freq >= 5925:
                band = "6 GHz"
            elif freq >= 3000:
                band = "5 GHz"
            else:
                band = "2.4 GHz"
            rssi = link.get("rssi_dbm", "?")
            bssid = link.get("bssid", "?")
            print(f"    Link {link['id']}: {bssid}  {freq} MHz ({band})  RSSI {rssi} dBm")

    print()


def main():
    import argparse
    parser = argparse.ArgumentParser(
        description="LabWiFiMon WiFi 7 capability detection"
    )
    parser.add_argument(
        "interface", nargs="?", default="wlan0",
        help="WiFi interface to query (default: wlan0)"
    )
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    args = parser.parse_args()

    logging.basicConfig(level=logging.WARNING)

    if args.json:
        caps = get_wifi7_capabilities(args.interface)
        print(json.dumps(caps, indent=2, default=str))
    else:
        print_report(args.interface)


if __name__ == "__main__":
    main()
