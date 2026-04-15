#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────────────────
# LabWiFiMon Linux Probe — Installation Script
#
# Supports: Raspberry Pi 4/5, Ubuntu 22.04+, Debian 11+, Fedora 38+
# Run as root:  sudo bash install.sh
# ──────────────────────────────────────────────────────────────────────────────

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_DIR="/opt/labwifimon"
VENV_DIR="${INSTALL_DIR}/venv"
CONFIG_DIR="/etc/labwifimon"
CONFIG_FILE="${CONFIG_DIR}/config.yaml"
SERVICE_NAME="labwifimon-probe"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"

# Colour helpers
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
BLUE='\033[0;34m'; BOLD='\033[1m'; NC='\033[0m'
info()  { echo -e "${BLUE}[INFO]${NC}  $*"; }
ok()    { echo -e "${GREEN}[ OK ]${NC}  $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
err()   { echo -e "${RED}[ERR ]${NC}  $*" >&2; }
die()   { err "$*"; exit 1; }

# ── Root check ────────────────────────────────────────────────────────────────
[[ $EUID -eq 0 ]] || die "Run as root:  sudo bash $0"

# ── OS detection ─────────────────────────────────────────────────────────────
detect_os() {
    OS_ID="unknown"; OS_LIKE=""
    [[ -f /etc/os-release ]] && { . /etc/os-release; OS_ID="${ID:-unknown}"; OS_LIKE="${ID_LIKE:-}"; }
}

is_debian_like() { [[ "$OS_ID" == debian || "$OS_ID" == ubuntu || "$OS_LIKE" == *debian* ]]; }
is_fedora_like() { [[ "$OS_ID" == fedora || "$OS_ID" == rhel   || "$OS_LIKE" == *rhel*   ]]; }
is_arch_like()   { [[ "$OS_ID" == arch   || "$OS_ID" == manjaro ]]; }

# ── Hardware detection ────────────────────────────────────────────────────────
WIFI7=false
IS_RPI=false
RPI_MODEL=""
RPI_GEN=0

detect_hardware() {
    info "Detecting hardware…"

    # Raspberry Pi
    if [[ -f /proc/device-tree/model ]]; then
        RPI_MODEL=$(tr -d '\0' < /proc/device-tree/model 2>/dev/null || true)
        if [[ "$RPI_MODEL" == *"Raspberry Pi"* ]]; then
            IS_RPI=true
            ok "Raspberry Pi: $RPI_MODEL"
            # Extract generation number
            if [[ "$RPI_MODEL" =~ Raspberry\ Pi\ ([0-9]+) ]]; then
                RPI_GEN="${BASH_REMATCH[1]}"
            fi
        fi
    fi

    # PCIe WiFi cards
    if command -v lspci &>/dev/null; then
        WIFI_CARDS=$(lspci 2>/dev/null | grep -iE "wireless|wifi|wlan|network|802\.11" || true)
        if [[ -n "$WIFI_CARDS" ]]; then
            ok "PCIe WiFi hardware:"
            echo "$WIFI_CARDS" | sed 's/^/      /'
            if echo "$WIFI_CARDS" | grep -qiE "BE200|MT7925|WiFi 7|802\.11be"; then
                ok "WiFi 7 (802.11be) hardware detected!"
                WIFI7=true
            elif echo "$WIFI_CARDS" | grep -qiE "AX210|AX200|AX201|WiFi 6"; then
                ok "WiFi 6/6E hardware detected"
            fi
        fi
    fi

    # USB WiFi
    if command -v lsusb &>/dev/null; then
        USB_WIFI=$(lsusb 2>/dev/null | grep -iE "wireless|wifi|802\.11" || true)
        [[ -n "$USB_WIFI" ]] && { info "USB WiFi:"; echo "$USB_WIFI" | sed 's/^/      /'; }
    fi
}

# ── System packages ───────────────────────────────────────────────────────────
install_system_deps() {
    info "Installing system packages…"

    if is_debian_like; then
        export DEBIAN_FRONTEND=noninteractive
        apt-get update -qq
        apt-get install -y --no-install-recommends \
            iw wireless-tools net-tools iputils-ping \
            python3 python3-pip python3-venv python3-dev \
            libpcap-dev curl ca-certificates
        ok "Debian/Ubuntu packages installed"

    elif is_fedora_like; then
        dnf install -y \
            iw wireless-tools net-tools iputils \
            python3 python3-pip python3-devel \
            libpcap-devel curl ca-certificates
        ok "Fedora/RHEL packages installed"

    elif is_arch_like; then
        pacman -Sy --noconfirm \
            iw wireless_tools net-tools iputils \
            python python-pip libpcap curl
        ok "Arch packages installed"

    else
        warn "Unknown OS — please manually install:"
        warn "  iw wireless-tools python3 python3-pip python3-venv libpcap-dev"
    fi
}

# ── Intel iwlwifi firmware check ──────────────────────────────────────────────
check_iwlwifi() {
    if ! lsmod 2>/dev/null | grep -q iwlwifi; then
        return
    fi
    info "Checking Intel iwlwifi firmware…"

    FW_DIR="/lib/firmware"
    KERNEL=$(uname -r)
    KMAJ=$(uname -r | cut -d. -f1)
    KMIN=$(uname -r | cut -d. -f2)

    # BE200 firmware: iwlwifi-be-*.pnvm + iwlwifi-be-*.ucode
    if ls "${FW_DIR}"/iwlwifi-be* 2>/dev/null | head -1 | grep -q .; then
        ok "Intel BE200 firmware present"
        ls "${FW_DIR}"/iwlwifi-be* | sed 's/^/      /'
    # AX210 firmware: iwlwifi-ty-*
    elif ls "${FW_DIR}"/iwlwifi-ty* 2>/dev/null | head -1 | grep -q .; then
        ok "Intel AX210/AX211 firmware present"
    else
        warn "Intel WiFi firmware not found in ${FW_DIR}"
        if is_debian_like; then
            warn "Install with:  sudo apt install firmware-iwlwifi   (requires non-free repo)"
            warn "Or download from: https://git.kernel.org/pub/scm/linux/kernel/git/firmware/linux-firmware.git"
        fi
    fi

    # Kernel version check for WiFi 7
    if (( KMAJ > 6 || ( KMAJ == 6 && KMIN >= 1 ) )); then
        ok "Kernel ${KERNEL} supports WiFi 7 EHT/MLO"
    else
        warn "Kernel ${KERNEL} may not fully support WiFi 7 (need 6.1+)"
        warn "Consider upgrading the kernel for full BE200/MT7925 support"
    fi
}

# ── Raspberry Pi specifics ────────────────────────────────────────────────────
setup_rpi() {
    [[ "$IS_RPI" == true ]] || return
    info "Applying Raspberry Pi configuration…"

    # Pi 5 — check for M.2 HAT / PCIe WiFi
    if (( RPI_GEN >= 5 )); then
        info "Raspberry Pi 5 — checking for M.2 HAT…"
        if command -v lspci &>/dev/null && lspci 2>/dev/null | grep -qiE "wireless|wifi"; then
            ok "M.2 PCIe WiFi card detected"
        else
            warn "No M.2 PCIe WiFi card found."
            warn "If you have an M.2 HAT installed:"
            warn "  1. Ensure the HAT is firmly seated and the ribbon cable is connected"
            warn "  2. Add to /boot/firmware/config.txt:"
            warn "       dtparam=pciex1"
            warn "  3. For PCIe Gen 3 speed (optional, BE200 benefits):"
            warn "       dtparam=pciex1_gen=3"
            warn "  4. Reboot, then verify with: lspci"
        fi
    fi

    # Unblock WiFi if rfkill has it soft-blocked
    if command -v rfkill &>/dev/null; then
        if rfkill list wifi 2>/dev/null | grep -q "Soft blocked: yes"; then
            warn "WiFi soft-blocked by rfkill — unblocking…"
            rfkill unblock wifi
            ok "WiFi unblocked"
        fi
    fi

    # Country code reminder
    if ! grep -q "^country=" /etc/wpa_supplicant/wpa_supplicant.conf 2>/dev/null && \
       ! grep -q "^country=" /etc/NetworkManager/system-connections/*.nmconnection 2>/dev/null; then
        warn "WiFi regulatory country code may not be set."
        warn "Set it with:  sudo raspi-config → Localisation Options → WLAN Country"
        warn "Or add to /etc/wpa_supplicant/wpa_supplicant.conf:  country=US"
    fi
}

# ── WiFi interface detection ──────────────────────────────────────────────────
detect_iface() {
    local iface=""
    if command -v iw &>/dev/null; then
        iface=$(iw dev 2>/dev/null | awk '/Interface/{print $2; exit}')
    fi
    if [[ -z "$iface" ]]; then
        iface=$(ls /sys/class/net/ 2>/dev/null | grep -E "^wl" | head -1 || true)
    fi
    echo "${iface:-wlan0}"
}

# ── Python venv + deps ────────────────────────────────────────────────────────
install_python_deps() {
    info "Creating Python virtual environment in ${VENV_DIR}…"
    mkdir -p "${INSTALL_DIR}"
    python3 -m venv "${VENV_DIR}"
    "${VENV_DIR}/bin/pip" install --upgrade pip -q

    info "Installing Python dependencies…"
    "${VENV_DIR}/bin/pip" install -r "${SCRIPT_DIR}/requirements.txt" -q
    ok "Python dependencies installed"
}

# ── Probe files ───────────────────────────────────────────────────────────────
install_files() {
    info "Installing probe files to ${INSTALL_DIR}…"
    cp -f "${SCRIPT_DIR}/probe.py"        "${INSTALL_DIR}/"
    cp -f "${SCRIPT_DIR}/wifi7_info.py"   "${INSTALL_DIR}/"
    cp -f "${SCRIPT_DIR}/monitor_mode.py" "${INSTALL_DIR}/"
    chmod +x "${INSTALL_DIR}/probe.py" "${INSTALL_DIR}/monitor_mode.py"

    # Install config (never overwrite an existing user config)
    mkdir -p "${CONFIG_DIR}"
    if [[ ! -f "${CONFIG_FILE}" ]]; then
        IFACE=$(detect_iface)
        HOSTNAME=$(hostname -s)
        sed -e "s/^interface: wlan0/interface: ${IFACE}/" \
            -e "s/^probe_id: pi-probe-1/probe_id: ${HOSTNAME}/" \
            "${SCRIPT_DIR}/config.yaml" > "${CONFIG_FILE}"
        ok "Config written: ${CONFIG_FILE}  (interface=${IFACE}, probe_id=${HOSTNAME})"
    else
        warn "Existing config preserved: ${CONFIG_FILE}"
    fi
}

# ── Systemd service ───────────────────────────────────────────────────────────
install_service() {
    info "Installing systemd service ${SERVICE_NAME}…"
    cat > "${SERVICE_FILE}" <<EOF
[Unit]
Description=LabWiFiMon Linux Probe — WiFi quality monitoring
Documentation=https://github.com/your-org/labwifimon
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=root
WorkingDirectory=${INSTALL_DIR}
Environment=PYTHONPATH=${INSTALL_DIR}
ExecStart=${VENV_DIR}/bin/python3 ${INSTALL_DIR}/probe.py --config ${CONFIG_FILE}
Restart=on-failure
RestartSec=15
StartLimitInterval=120
StartLimitBurst=4

StandardOutput=journal
StandardError=journal
SyslogIdentifier=labwifimon-probe

# Allow interface scanning and raw socket access without running as a separate user
AmbientCapabilities=CAP_NET_ADMIN CAP_NET_RAW
CapabilityBoundingSet=CAP_NET_ADMIN CAP_NET_RAW

# Lightweight resource limits
MemoryMax=128M
CPUQuota=30%

[Install]
WantedBy=multi-user.target
EOF

    systemctl daemon-reload
    ok "Service file written: ${SERVICE_FILE}"
}

# ── Enable + start ────────────────────────────────────────────────────────────
start_service() {
    info "Enabling and starting ${SERVICE_NAME}…"
    systemctl enable "${SERVICE_NAME}"
    systemctl restart "${SERVICE_NAME}"

    # Brief wait for the service to initialise
    sleep 3

    if systemctl is-active --quiet "${SERVICE_NAME}"; then
        ok "Service is running!"
    else
        err "Service failed to start.  Recent logs:"
        journalctl -u "${SERVICE_NAME}" --no-pager -n 25 || true
        warn "Check and edit the config, then:  sudo systemctl restart ${SERVICE_NAME}"
    fi
}

# ── Summary ───────────────────────────────────────────────────────────────────
print_summary() {
    echo ""
    echo -e "${BOLD}${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${BOLD}${GREEN}  LabWiFiMon Linux Probe — Installation Complete${NC}"
    echo -e "${BOLD}${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo ""
    echo "  Probe files  : ${INSTALL_DIR}/"
    echo "  Config       : ${CONFIG_FILE}"
    echo "  Service      : ${SERVICE_NAME}"
    echo ""
    if [[ "$WIFI7" == true ]]; then
        echo -e "  ${GREEN}WiFi 7 hardware detected — wifi7_monitoring is enabled${NC}"
        echo "  Capability check:"
        echo "    ${VENV_DIR}/bin/python3 ${INSTALL_DIR}/wifi7_info.py"
        echo ""
    fi
    echo "  Useful commands:"
    echo "    journalctl -u ${SERVICE_NAME} -f              # live logs"
    echo "    systemctl status ${SERVICE_NAME}              # service status"
    echo "    nano ${CONFIG_FILE}                            # edit config"
    echo "    systemctl restart ${SERVICE_NAME}             # apply changes"
    echo ""
    echo "  Quick test (prints JSON, no MQTT):"
    echo "    ${VENV_DIR}/bin/python3 ${INSTALL_DIR}/probe.py --once --dry-run"
    echo ""
    echo "  Monitor mode (advanced — needs a second adapter):"
    echo "    sudo ${VENV_DIR}/bin/python3 ${INSTALL_DIR}/monitor_mode.py -i wlan1"
    echo ""
}

# ── Main ──────────────────────────────────────────────────────────────────────
main() {
    echo ""
    echo -e "${BOLD}${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${BOLD}${BLUE}  LabWiFiMon Linux Probe — Installer${NC}"
    echo -e "${BOLD}${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo ""

    detect_os
    detect_hardware
    install_system_deps
    check_iwlwifi
    setup_rpi
    install_python_deps
    install_files
    install_service
    start_service
    print_summary
}

main "$@"
