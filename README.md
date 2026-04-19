# Monitorwall

Videowall-System fuer Raspberry Pi 5. Ein Codestand laeuft auf Head und Slaves —
die Rolle wird automatisch per Hardware erkannt (TP-Link USB-Dongle = Head).

## Architektur

```
┌─────────────────────────────────────────────┐
│  Head-Pi (TP-Link USB-Dongle vorhanden)     │
│                                             │
│  videowall-player.py ─── mpv (HDMI-1)       │
│           │          └── mpv (HDMI-2)       │
│           │                                 │
│  videowall-mgr.py ────── WebUI (:8080)      │
│           │          └── REST-API           │
│           │                                 │
│  SyncMaster ─────── UDP :1666 ──────┐       │
│                                     │       │
│  wlan0 (TP-Link USB) ── AP "displaywall"    │
│  wlan1 (Broadcom)    ── Admin-WLAN          │
└─────────────────────────────────────┼───────┘
                                      │
       WLAN "displaywall" (10.0.0.0/24)
                                      │
┌─────────────────────────────────────┼───────┐
│  Slave-Pi (kein Dongle)             │       │
│                                     │       │
│  videowall-player.py ─── mpv (HDMI-1)       │
│           │          └── mpv (HDMI-2)       │
│           │                                 │
│  Agent (:8081) ──── Playlist-Empfang        │
│                                             │
│  SyncSlave ─────── UDP :1666 (PLL)          │
│                                             │
│  wlan0 ── Client → "displaywall"            │
└─────────────────────────────────────────────┘
```

## Komponenten

| Datei | Beschreibung |
|-------|-------------|
| `videowall-player.py` | Unified Player — Head und Slave in einem Binary |
| `videowall-mgr.py` | Web-Manager (nur Head) — GUI, REST-API, Playlist-Verwaltung |
| `videowall/config.py` | Zentrale Konfiguration — Pfade, Ports, Rolle |
| `videowall/mpv.py` | mpv-Wrapper — IPC, Pre-Decode, Displaysteuerung |
| `webui/` | Browser-GUI — Playlist-Editor, Canvas, Live-Preview |
| `setup.sh` | Installation auf frischem Raspberry Pi OS |
| `setup-ap.sh` | Access-Point-Setup (wird von setup.sh aufgerufen, nur Head) |

## Rollenerkennung

Head vs Slave wird automatisch erkannt: Wenn ein TP-Link Archer T2U PLUS
(USB-ID `2357:0120`) am USB-Bus gefunden wird, ist der Pi ein Head.
Kein Dongle = Slave. Der WLAN-Treiber muss dafuer nicht funktionieren.

## Netzwerk

### Ports

| Port | Dienst |
|------|--------|
| 8080 | WebUI + REST-API (Head) |
| 8081 | Agent (Slave) — Playlist-Empfang, Status, Commands |
| 1666 | Clock-Sync (UDP, alle Pis) |

### Access Point

Der Head-Pi spannt ueber den TP-Link USB-Adapter ein eigenes WLAN auf:

| Eigenschaft | Wert |
|-------------|------|
| SSID | `displaywall` |
| Passwort | `12345678` |
| Head-IP | `10.0.0.1` |
| DHCP-Range | `10.0.0.10 - 10.0.0.254` |
| Interface | `wlan0` (TP-Link USB, RTL8821AU) |

Der TP-Link USB-Adapter wird fuer den AP genutzt, weil er bessere
AP-Unterstuetzung hat als der eingebaute Broadcom-Chip (concurrent mode).

Das Admin-WLAN (fuer SSH/Internet) laeuft auf `wlan1` (Broadcom intern).
Interface-Zuordnung ist per udev-Regel nach MAC-Adresse fixiert.

### Captive Portal

Beim Verbinden mit "displaywall" kann man `http://displaywall` im Browser
eingeben, um direkt auf die Web-GUI zu gelangen. DNS im AP-Netz leitet
alle Domains auf den Head-Pi um.

### Dual-WLAN (Head)

| Interface | Adapter | Funktion | IP | Metrik |
|-----------|---------|----------|-----|--------|
| wlan0 | TP-Link USB | AP "displaywall" | 10.0.0.1/24 | 200 |
| wlan1 | Broadcom (intern) | Admin-WLAN | DHCP | 100 |

Metrik 100 = hohe Prioritaet (Internet), Metrik 200 = niedrig (kein Uplink).

## Konfigurationsdateien (~/.videowall/)

| Datei | Inhalt |
|-------|--------|
| `displays.json` | Display-Einstellungen (Rotation, Aufloesung) |
| `playback_state.json` | Aktueller Playback-Index pro Monitor |
| `viewer_cmd.json` | Steuerkommandos (play/stop/next/prev) |
| `slaves.json` | Slave-Adressen (IP, Port) |
| `playlists.json` | Playlist-Cache (Slave) |
| `head.conf` | Head-IP (Slave — fuer Pull-Requests) |

## Installation

### Gleiches Image fuer Head und Slave

SD-Karte mit Pi-Imager flashen:
- OS: Raspberry Pi OS (64-bit)
- User: `head`, Passwort: `12345678`
- WLAN: `gaengeviertel` (oder anderes Setup-WLAN mit Internet, fuer apt)
- SSH: aktiviert

### Setup ausfuehren

```bash
cd ~/videowall
chmod +x setup.sh

# Head (TP-Link USB muss eingesteckt sein):
sudo ADMIN_SSID="gaengeviertel" ADMIN_PASS="KommInDieGaenge!" bash setup.sh

# Slave:
sudo bash setup.sh slave2
```

### Was passiert

**Head:**
- Erkennt TP-Link USB-Dongle → Rolle: Head
- Installiert RTL8821AU-Treiber (DKMS)
- Richtet AP "displaywall" auf wlan0 ein
- Verbindet wlan1 mit Admin-WLAN
- Fixiert Interface-Namen per udev (nach MAC)
- Startet videowall-player + videowall-mgr Services
- Richtet Captive Portal ein (Port 80 → 8080)

**Slave:**
- Kein Dongle → Rolle: Slave
- Konfiguriert WLAN "displaywall" per Netplan
- Schreibt Head-IP (10.0.0.1) in `~/.videowall/head.conf`
- Startet videowall-player Service

### Nach dem Setup

```bash
sudo reboot
```

Head: AP aktiv, Web-GUI unter `http://displaywall` oder `http://10.0.0.1:8080`.
Slave: Verbindet sich automatisch mit "displaywall", pullt Playlists vom Head.

## WebUI

Erreichbar unter `http://displaywall` oder `http://10.0.0.1:8080`

- **Playlist-Tab:** Monitor auswaehlen, Assets per Drag&Drop zuweisen
- **Pool-Tab:** Alle Assets mit Thumbnails, Dreh-Button (90° CW), Loeschen
- **Canvas-Tab:** Monitor-Anordnung per Drag&Drop
- **Devices-Tab:** Rotation, Aufloesung, Status aller Pis
- **Aktuell:** Live-Preview des gerade spielenden Assets (gedreht wie Monitor)
- **Toolbar:** Play/Stop/Vor/Zurueck, Shuffle

## Deployment

Aenderungen muessen auf ALLE Pis kopiert werden (ein Codestand):

```bash
# Head
scp <datei> head@10.0.0.1:~/videowall/<pfad>

# Slave
scp <datei> head@<slave-ip>:~/videowall/<pfad>

# Services neustarten
ssh head@<ip> "echo '12345678' | sudo -S systemctl restart videowall-player videowall-mgr"
```

## Zugangsdaten

| Was | User | Passwort |
|-----|------|----------|
| Pi (SSH) | `head` | `12345678` |
| WLAN "displaywall" | — | `12345678` |
| Web-GUI | — | kein Login |
