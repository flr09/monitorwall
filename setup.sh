#!/bin/bash
# Videowall Setup — macht aus einem frischen "head"-Pi einen Head oder Slave.
#
# Erkennung: TP-Link USB-Dongle (wlan1) vorhanden → Head, sonst → Slave.
#
# Aufruf:
#   sudo bash setup.sh              # Auto-Detect
#   sudo bash setup.sh slave2       # Erzwingt Slave mit Nummer 2
#
# Was passiert:
#   1. Rolle erkennen (Head/Slave)
#   2. Hostname setzen (head / slave<N>)
#   3. mpv + Python installieren
#   4. Videowall-Code kopieren
#   5. Autostart einrichten (systemd)
#   6. Cursor verstecken
#   7. Reboot

set -e

VIDEOWALL_DIR="$HOME/videowall"
FORCE_SLAVE_NAME="${1:-}"

# --- Farben ---
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

info()  { echo -e "${GREEN}[setup]${NC} $1"; }
warn()  { echo -e "${YELLOW}[setup]${NC} $1"; }
error() { echo -e "${RED}[setup]${NC} $1"; }

# --- Root-Check ---
if [ "$EUID" -ne 0 ]; then
    error "Bitte als root ausfuehren: sudo bash setup.sh"
    exit 1
fi

REAL_USER="${SUDO_USER:-$(whoami)}"
REAL_HOME=$(eval echo "~$REAL_USER")

# --- Rolle erkennen ---
if lsusb 2>/dev/null | grep -q "2357:0120"; then
    ROLE="head"
    NEW_HOSTNAME="head"
    info "TP-Link Dongle erkannt (2357:0120) → Rolle: HEAD"
else
    ROLE="slave"
    if [ -n "$FORCE_SLAVE_NAME" ]; then
        NEW_HOSTNAME="$FORCE_SLAVE_NAME"
    else
        error "Slave-Name fehlt. Aufruf: sudo bash setup.sh slave2"
        exit 1
    fi
    info "Kein TP-Link Dongle → Rolle: SLAVE ($NEW_HOSTNAME)"
fi

CURRENT_HOSTNAME=$(hostname)
info "Aktueller Hostname: $CURRENT_HOSTNAME → Neuer Hostname: $NEW_HOSTNAME"

# --- Hostname aendern ---
if [ "$CURRENT_HOSTNAME" != "$NEW_HOSTNAME" ]; then
    info "Setze Hostname auf $NEW_HOSTNAME..."
    hostnamectl set-hostname "$NEW_HOSTNAME"
    sed -i "s/127.0.1.1.*/127.0.1.1\t$NEW_HOSTNAME/g" /etc/hosts
fi

# --- Pakete installieren ---
info "Installiere Pakete..."
apt-get update -qq
apt-get install -y -qq mpv python3 ffmpeg

# --- Cursor verstecken (labwc/Xcursor) ---
info "Verstecke Maus-Cursor..."
XCURSOR_DIR="$REAL_HOME/.icons/default"
mkdir -p "$XCURSOR_DIR/cursors"
cat > "$XCURSOR_DIR/index.theme" << 'THEME'
[Icon Theme]
Inherits=Adwaita
THEME
# Leere Cursor-Datei (1x1 transparent)
if command -v xcursorgen &>/dev/null; then
    TMPDIR=$(mktemp -d)
    convert -size 1x1 xc:transparent "$TMPDIR/blank.png" 2>/dev/null || true
    echo "1 0 0 $TMPDIR/blank.png" > "$TMPDIR/blank.cursor"
    for cursor_name in left_ptr arrow default; do
        xcursorgen "$TMPDIR/blank.cursor" "$XCURSOR_DIR/cursors/$cursor_name" 2>/dev/null || true
    done
    rm -rf "$TMPDIR"
fi

# --- Videowall-Code sicherstellen ---
VIDEOWALL_SRC="$(cd "$(dirname "$0")" && pwd)"
VIDEOWALL_DEST="$REAL_HOME/videowall"

if [ "$VIDEOWALL_SRC" != "$VIDEOWALL_DEST" ]; then
    info "Kopiere Videowall-Code nach $VIDEOWALL_DEST..."
    cp -r "$VIDEOWALL_SRC" "$VIDEOWALL_DEST"
    chown -R "$REAL_USER:$REAL_USER" "$VIDEOWALL_DEST"
fi

# --- Config-Verzeichnisse ---
CONFIG_DIR="$REAL_HOME/.videowall"
mkdir -p "$CONFIG_DIR"
chown "$REAL_USER:$REAL_USER" "$CONFIG_DIR"

if [ "$ROLE" = "head" ]; then
    ASSET_DIR="$REAL_HOME/screenly_assets"
else
    ASSET_DIR="$REAL_HOME/videowall_assets"
fi
mkdir -p "$ASSET_DIR"
chown "$REAL_USER:$REAL_USER" "$ASSET_DIR"

# --- Systemd Service: Player ---
info "Richte Autostart ein (videowall-player)..."
cat > /etc/systemd/system/videowall-player.service << EOF
[Unit]
Description=Videowall Player
After=graphical.target network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$REAL_USER
Environment=WAYLAND_DISPLAY=wayland-0
Environment=XDG_RUNTIME_DIR=/run/user/$(id -u "$REAL_USER")
WorkingDirectory=$VIDEOWALL_DEST
ExecStart=/usr/bin/python3 $VIDEOWALL_DEST/videowall-player.py
Restart=always
RestartSec=5

[Install]
WantedBy=graphical.target
EOF

systemctl daemon-reload
systemctl enable videowall-player.service

# --- Head: Manager-Service + AP ---
if [ "$ROLE" = "head" ]; then
    info "Richte Manager-Service ein..."
    cat > /etc/systemd/system/videowall-mgr.service << EOF
[Unit]
Description=Videowall Manager (Web-GUI)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$REAL_USER
WorkingDirectory=$VIDEOWALL_DEST
ExecStart=/usr/bin/python3 $VIDEOWALL_DEST/videowall-mgr.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

    systemctl daemon-reload
    systemctl enable videowall-mgr.service

    # --- Head: Access Point aufsetzen ---
    info "Richte Access Point ein..."
    if [ -f "$VIDEOWALL_SRC/setup-ap.sh" ]; then
        # Admin-WLAN muss als Parameter uebergeben werden
        if [ -n "${ADMIN_SSID:-}" ] && [ -n "${ADMIN_PASS:-}" ]; then
            bash "$VIDEOWALL_SRC/setup-ap.sh" "$ADMIN_SSID" "$ADMIN_PASS"
        else
            warn "AP-Setup uebersprungen: ADMIN_SSID und ADMIN_PASS nicht gesetzt."
            warn "Spaeter manuell ausfuehren: sudo bash setup-ap.sh SSID PASSWORT"
        fi
    fi

    # --- Head: Captive Portal (Port 80 → 8080) ---
    info "Richte Captive Portal ein..."
    mkdir -p /etc/nftables.d
    cat > /etc/nftables.d/captive-portal.nft << 'NFTEOF'
table ip monitorwall-captive {
    chain prerouting {
        type nat hook prerouting priority dstnat;
        iifname "wlan0" tcp dport 80 redirect to :8080
    }
}
NFTEOF

    cat > /etc/systemd/system/monitorwall-captive.service << 'EOF'
[Unit]
Description=Monitorwall Captive Portal (Port 80 → 8080)
After=network-online.target NetworkManager.service
Wants=network-online.target

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/usr/sbin/nft -f /etc/nftables.d/captive-portal.nft

[Install]
WantedBy=multi-user.target
EOF

    systemctl daemon-reload
    systemctl enable monitorwall-captive.service
    nft -f /etc/nftables.d/captive-portal.nft 2>/dev/null || true
fi

# --- Slave: WLAN "displaywall" + Head-IP konfigurieren ---
if [ "$ROLE" = "slave" ]; then
    info "Konfiguriere WLAN-Verbindung zum Head-AP..."

    # Netplan-Config fuer displaywall-WLAN (DHCP, Head vergibt IPs)
    cat > /etc/netplan/90-displaywall.yaml << 'NETEOF'
network:
  version: 2
  wifis:
    wlan0:
      renderer: NetworkManager
      match: {}
      dhcp4: true
      access-points:
        "displaywall":
          auth:
            key-management: "psk"
            password: "12345678"
NETEOF
    chmod 600 /etc/netplan/90-displaywall.yaml

    # Alte WLAN-Configs deaktivieren (z.B. gaengeviertel vom Flash)
    for f in /etc/netplan/*gaengeviertel*; do
        if [ -f "$f" ] && [[ "$f" != *.bak ]]; then
            mv "$f" "${f}.bak"
            info "  Alte Config deaktiviert: $f"
        fi
    done

    # Head-IP fuer Pull/Sync konfigurieren
    mkdir -p "$CONFIG_DIR"
    echo "10.0.0.1" > "$CONFIG_DIR/head.conf"
    chown "$REAL_USER:$REAL_USER" "$CONFIG_DIR/head.conf"
    info "Head-IP gesetzt: 10.0.0.1"
fi

# --- Zusammenfassung ---
echo ""
echo "============================================"
info "Setup abgeschlossen!"
echo ""
info "Rolle:      $ROLE"
info "Hostname:   $NEW_HOSTNAME"
info "User:       $REAL_USER"
info "Code:       $VIDEOWALL_DEST"
info "Config:     $CONFIG_DIR"
info "Assets:     $ASSET_DIR"
echo ""
if [ "$ROLE" = "head" ]; then
    info "Services:   videowall-player, videowall-mgr"
    info "Web-GUI:    http://10.0.0.1:8080 oder http://displaywall"
    info "AP:         SSID 'displaywall', PW '12345678'"
else
    info "Services:   videowall-player"
    info "Head:       10.0.0.1 (aus head.conf)"
    info "WLAN:       verbindet sich mit 'displaywall'"
fi
echo ""
warn "Neustart erforderlich: sudo reboot"
echo "============================================"
