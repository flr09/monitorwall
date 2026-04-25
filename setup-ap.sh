#!/bin/bash
# Monitorwall AP-Setup — Dual-WLAN auf dem Head-Pi
#
# Architektur (nach displaywall-Vorbild):
#   wlan0 (USB/TP-Link)     → Access Point "displaywall" (10.0.0.1/24)
#   wlan1 (intern/Broadcom) → Admin-WLAN fuer SSH/Internet (DHCP)
#
# Grund: TP-Link hat bessere AP-Unterstuetzung (concurrent mode),
# Broadcom kann keine Interface-Kombinationen.
#
# Voraussetzungen:
#   - TP-Link Archer T2U PLUS (RTL8821AU) eingesteckt
#   - Treiber 88XXau installiert (wird hier geprueft)
#   - Aufruf ueber Admin-WLAN (wlan1), NICHT ueber wlan0!
#
# Aufruf:
#   sudo bash setup-ap.sh "ADMIN_SSID" "ADMIN_PASSWORT"
#
#

set -euo pipefail

AP_SSID="displaywall"
AP_PASS="12345678"
AP_IP="10.0.0.1/24"
AP_CON_NAME="Monitorwall-Hotspot"
ADMIN_CON_NAME="Admin-USB"

# Broadcom-MAC und TP-Link-MAC fuer udev-Regeln
BROADCOM_MAC=""
TPLINK_MAC=""

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

info()  { echo -e "${GREEN}[ap]${NC} $1"; }
warn()  { echo -e "${YELLOW}[ap]${NC} $1"; }
error() { echo -e "${RED}[ap]${NC} $1"; }

# --- Root-Check ---
if [ "$EUID" -ne 0 ]; then
    error "Bitte als root ausfuehren: sudo bash setup-ap.sh SSID PASSWORT"
    exit 1
fi

ADMIN_SSID="${1:-}"
ADMIN_PASS="${2:-}"

if [ -z "$ADMIN_SSID" ] || [ -z "$ADMIN_PASS" ]; then
    error "Usage: sudo bash setup-ap.sh \"ADMIN_SSID\" \"ADMIN_PASSWORT\""
    exit 1
fi

# --- 1. TP-Link Dongle pruefen ---
info "1. Pruefe TP-Link USB-Dongle..."
if ! lsusb 2>/dev/null | grep -q "2357:0120"; then
    error "TP-Link Archer T2U PLUS (2357:0120) nicht gefunden!"
    error "Ohne USB-Dongle kein Dual-WLAN moeglich."
    exit 1
fi
info "   TP-Link erkannt."

# --- 2. Treiber pruefen/installieren ---
info "2. Pruefe RTL8821AU-Treiber (88XXau)..."
if ! lsmod | grep -q 88XXau; then
    if modprobe 88XXau 2>/dev/null; then
        info "   Treiber geladen."
    else
        warn "   Treiber nicht vorhanden — installiere via DKMS..."
        apt-get install -y -qq dkms
        TMPDIR=$(mktemp -d)
        git clone --depth 1 https://github.com/aircrack-ng/rtl8812au.git "$TMPDIR/rtl8812au"
        VER=$(sed -n 's/PACKAGE_VERSION="\(.*\)"/\1/p' "$TMPDIR/rtl8812au/dkms.conf")
        rsync --exclude=.git -a "$TMPDIR/rtl8812au/" "/usr/src/realtek-rtl88xxau-$VER/"
        dkms add -m realtek-rtl88xxau -v "$VER"
        dkms build -m realtek-rtl88xxau -v "$VER"
        dkms install -m realtek-rtl88xxau -v "$VER"
        rm -rf "$TMPDIR"
        modprobe 88XXau
        info "   Treiber installiert und geladen."
    fi
else
    info "   Treiber bereits geladen."
fi

# Treiber beim Boot laden
echo "88XXau" > /etc/modules-load.d/rtl8812au.conf

# --- 3. Warten auf wlan1 ---
info "3. Warte auf wlan1..."
for i in $(seq 1 10); do
    if ip link show wlan1 &>/dev/null; then
        break
    fi
    sleep 1
done
if ! ip link show wlan1 &>/dev/null; then
    error "wlan1 nicht gefunden nach Treiber-Load!"
    exit 1
fi
info "   wlan1 vorhanden."

# --- 4. MAC-Adressen ermitteln und Interface-Zuordnung fixieren ---
info "4. Fixiere Interface-Namen per udev..."

# Broadcom = internes WiFi (brcmfmac)
# TP-Link = USB WiFi (rtl88XXau)
for iface in wlan0 wlan1; do
    driver=$(readlink "/sys/class/net/$iface/device/driver" 2>/dev/null | xargs basename 2>/dev/null || echo "")
    mac=$(cat "/sys/class/net/$iface/address" 2>/dev/null || echo "")
    if [[ "$driver" == "brcmfmac" ]]; then
        BROADCOM_MAC="$mac"
    elif [[ "$driver" == "rtl88XXau" ]]; then
        TPLINK_MAC="$mac"
    fi
done

if [ -z "$BROADCOM_MAC" ] || [ -z "$TPLINK_MAC" ]; then
    error "Konnte Broadcom/TP-Link MACs nicht ermitteln!"
    error "Broadcom=$BROADCOM_MAC, TP-Link=$TPLINK_MAC"
    exit 1
fi

cat > /etc/udev/rules.d/70-wifi-names.rules << EOF
# Monitorwall: Interface-Zuordnung nach MAC-Adresse
# USB TP-Link RTL8821AU = wlan0 (Access Point — bessere AP-Unterstuetzung)
SUBSYSTEM=="net", ACTION=="add", ATTR{address}=="$TPLINK_MAC", NAME="wlan0"
# Internes Broadcom WiFi = wlan1 (Admin-WLAN)
SUBSYSTEM=="net", ACTION=="add", ATTR{address}=="$BROADCOM_MAC", NAME="wlan1"
EOF
info "   wlan0=$TPLINK_MAC (TP-Link/AP), wlan1=$BROADCOM_MAC (Broadcom/Admin)"

# --- 5. Alte Netplan-WiFi-Configs deaktivieren ---
info "5. Deaktiviere alte Netplan-WiFi-Configs..."
for f in /etc/netplan/*wlan*; do
    if [ -f "$f" ] && [[ "$f" != *.bak ]]; then
        mv "$f" "${f}.bak"
        info "   $f → ${f}.bak"
    fi
done

# --- 6. Admin-WLAN auf wlan1 (Broadcom/intern) einrichten ---
info "6. Richte Admin-WLAN auf wlan1 ein ($ADMIN_SSID)..."
nmcli con delete "$ADMIN_CON_NAME" 2>/dev/null || true
nmcli con add type wifi ifname wlan1 con-name "$ADMIN_CON_NAME" ssid "$ADMIN_SSID"
nmcli con modify "$ADMIN_CON_NAME" \
    802-11-wireless-security.key-mgmt wpa-psk \
    802-11-wireless-security.psk "$ADMIN_PASS" \
    ipv4.method auto \
    ipv4.route-metric 100 \
    connection.autoconnect yes \
    connection.autoconnect-priority 90

# --- 7. Access Point auf wlan0 (TP-Link/USB) einrichten ---
info "7. Richte Access Point auf wlan0 ein ($AP_SSID)..."
nmcli con delete "$AP_CON_NAME" 2>/dev/null || true
nmcli con add type wifi ifname wlan0 con-name "$AP_CON_NAME" \
    autoconnect yes \
    ssid "$AP_SSID" \
    wifi.mode ap \
    wifi.band bg \
    wifi-sec.key-mgmt wpa-psk \
    wifi-sec.psk "$AP_PASS" \
    ipv4.method shared \
    ipv4.addresses "$AP_IP" \
    ipv4.route-metric 200 \
    connection.autoconnect-priority 100

# --- 8. Alte wlan0-Verbindungen deaktivieren, AP starten ---
info "8. Starte Access Point..."
# Alle wlan0-Verbindungen runterfahren die nicht der AP sind
for con in $(nmcli -t -f NAME,DEVICE con show --active | grep ":wlan0" | cut -d: -f1); do
    if [ "$con" != "$AP_CON_NAME" ]; then
        nmcli con down "$con" 2>/dev/null || true
    fi
done

nmcli con up "$AP_CON_NAME" 2>/dev/null
nmcli con up "$ADMIN_CON_NAME" 2>/dev/null || warn "   Admin-WLAN konnte nicht verbunden werden (nach Reboot pruefen)"

# --- 9. Zusammenfassung ---
echo ""
echo "============================================"
info "AP-Setup abgeschlossen!"
echo ""
info "Access Point:"
info "  SSID:     $AP_SSID"
info "  Passwort: $AP_PASS"
info "  Head-IP:  ${AP_IP%/*}"
info "  DHCP:     10.0.0.10 - 10.0.0.254"
info "  Metrik:   200 (niedrige Prioritaet)"
echo ""
info "Admin-WLAN:"
info "  SSID:     $ADMIN_SSID"
info "  Interface: wlan1 (USB)"
info "  Metrik:   100 (hohe Prioritaet, Internet)"
echo ""
warn "Neustart empfohlen: sudo reboot"
warn "Danach SSH ueber die Admin-WLAN-IP (DHCP, per Router nachschauen)"
echo "============================================"
