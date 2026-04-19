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
└─────────────────────────────────────┼───────┘
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

## Rollenerkennung

Head vs Slave wird automatisch erkannt: Wenn ein TP-Link Archer T2U PLUS
(USB-ID `2357:0120`) am USB-Bus gefunden wird, ist der Pi ein Head.
Kein Dongle = Slave. Der WLAN-Treiber muss dafuer nicht funktionieren.

## Netzwerk

| Port | Dienst |
|------|--------|
| 8080 | WebUI + REST-API (Head) |
| 8081 | Agent (Slave) — Playlist-Empfang, Status, Commands |
| 1666 | Clock-Sync (UDP, alle Pis) |

### Access Point (geplant)

Der Head-Pi soll ueber `wlan0` einen eigenen Access Point "displaywall"
(Passwort: `12345678`) aufspannen. Slaves verbinden sich dorthin.
Aktuell ist der TP-Link USB-Adapter zwar per USB-ID erkannt, aber das
WLAN-Interface (`wlan1`) wird vom Kernel nicht angelegt — Treiber fehlt.

**Status:** AP nicht aktiv. Head und Slaves sind derzeit im selben
externen WLAN (`gaengeviertel`). Fuer den Standalone-Betrieb muss
entweder der TP-Link-Treiber installiert oder ein anderer USB-WLAN-Adapter
mit AP-Faehigkeit verwendet werden.

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

```bash
# Auf frischem Raspberry Pi OS (Bookworm, arm64):
cd ~/videowall
chmod +x setup.sh
./setup.sh
```

Das Setup:
- Installiert Abhaengigkeiten (mpv, ffmpeg, python3-pil, etc.)
- Richtet systemd-Services ein (videowall-player, videowall-mgr)
- Konfiguriert labwc-Autostart (kein Desktop, nur Displays)
- Erstellt ~/.videowall/ mit Defaults

## WebUI

Erreichbar unter `http://<head-ip>:8080`

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
scp <datei> head@<head-ip>:~/videowall/<pfad>

# Slave
scp <datei> head@<slave-ip>:~/videowall/<pfad>

# Services neustarten
ssh head@<ip> "echo '<pw>' | sudo -S systemctl restart videowall-player videowall-mgr"
```

## Bekannte Einschraenkungen

- TP-Link Archer T2U PLUS: USB-ID wird erkannt, aber kein WLAN-Interface
  (Treiber-Problem auf Bookworm/arm64). Head-Erkennung funktioniert trotzdem.
- Slave-Hostname kann nach Reboot zurueckgesetzt werden (hostnamectl allein
  reicht nicht, `/etc/hostname` muss auch geschrieben werden).
