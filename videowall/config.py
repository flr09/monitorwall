"""Zentrale Konfiguration fuer alle Videowall-Komponenten.

Pfade, Konstanten und Display-Konfiguration.
Head vs Slave wird per Hardware erkannt: TP-Link USB-Dongle vorhanden → Head.
"""

import json
import socket
from pathlib import Path

# --- Rolle ---

HOSTNAME = socket.gethostname()


def _detect_head():
    """Head-Erkennung: TP-Link USB-WLAN-Dongle steckt nur im Head-Pi.

    Prueft per USB-ID ob der TP-Link Archer T2U PLUS (2357:0120) vorhanden ist.
    Funktioniert auch ohne installierten WLAN-Treiber.
    """
    tp_link_id = "2357:0120"
    try:
        for dev in Path("/sys/bus/usb/devices").iterdir():
            vendor = (dev / "idVendor").read_text().strip() if (dev / "idVendor").exists() else ""
            product = (dev / "idProduct").read_text().strip() if (dev / "idProduct").exists() else ""
            if f"{vendor}:{product}" == tp_link_id:
                return True
    except Exception:
        pass
    return False


IS_HEAD = _detect_head()

# --- Pfade ---

HOME = Path.home()
CONFIG_DIR = HOME / ".videowall"
DISPLAYS_JSON = CONFIG_DIR / "displays.json"
PLAYBACK_STATE_FILE = CONFIG_DIR / "playback_state.json"
VIEWER_CMD_FILE = CONFIG_DIR / "viewer_cmd.json"
SLAVES_JSON = CONFIG_DIR / "slaves.json"
PLAYLIST_FILE = CONFIG_DIR / "playlists.json"  # Slave: persistenter Cache

if IS_HEAD:
    # Head: Assets im screenly-Verzeichnis (Kompatibilitaet mit Anthias-DB)
    ASSET_DIR = HOME / "screenly_assets"
    # Anthias-DB fuer Asset-Metadaten
    SCREENLY_DIR = HOME / ".screenly"
    DB_PATH = SCREENLY_DIR / "screenly.db"
    DW_DB_PATH = SCREENLY_DIR / "displaywall.db"
else:
    # Slave: eigenes Asset-Verzeichnis
    ASSET_DIR = HOME / "videowall_assets"
    SCREENLY_DIR = CONFIG_DIR  # Fallback, wird auf Slaves nicht genutzt
    DB_PATH = None
    DW_DB_PATH = None

# --- Konstanten ---

# DRM/Display
CONNECTOR_1 = "HDMI-A-1"
CONNECTOR_2 = "HDMI-A-2"

# Netzwerk
WEBUI_PORT = 8080
AGENT_PORT = 8081
SYNC_PORT = 1666

# Head-Pi Adresse (fuer Slaves)
# Aus ~/.videowall/head.conf lesen, Fallback auf "head-pi"
_HEAD_CONF = CONFIG_DIR / "head.conf"


def _read_head_host():
    """Head-Adresse aus head.conf lesen. Fallback auf 'head-pi'."""
    try:
        return _HEAD_CONF.read_text().strip()
    except (FileNotFoundError, OSError):
        return "head-pi"


HEAD_HOST = _read_head_host()
HEAD_PORT = WEBUI_PORT

# USB-Mount-Punkt (Slave)
USB_MOUNT = Path("/media/displaywall")

# Prefix im Asset-Namen fuer Display-Zuweisung
DISPLAY_PREFIX = "2:"

# Docker-Pfad-Umschreibung (Legacy, Anthias-Kompatibilitaet)
DOCKER_DATA_PREFIX = "/data/"
HOST_DATA_PREFIX = str(HOME) + "/"


# --- Display-Konfiguration (displays.json) ---

_DEFAULT_DISPLAYS = {
    CONNECTOR_1: {"rotation": 0, "resolution": "2560x1440"},
    CONNECTOR_2: {"rotation": 0, "resolution": "2560x1440"},
}


def load_displays():
    """Liest Display-Konfiguration. Legt Default an falls nicht vorhanden."""
    try:
        with open(DISPLAYS_JSON) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        save_displays(_DEFAULT_DISPLAYS)
        return _DEFAULT_DISPLAYS.copy()


def save_displays(data):
    """Speichert Display-Konfiguration."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with open(DISPLAYS_JSON, "w") as f:
        json.dump(data, f, indent=2)


def resolve_uri(uri):
    """Docker-Pfade (/data/...) auf Host-Pfade (/home/head/...) umschreiben."""
    if uri.startswith(DOCKER_DATA_PREFIX):
        return HOST_DATA_PREFIX + uri[len(DOCKER_DATA_PREFIX):]
    return uri
