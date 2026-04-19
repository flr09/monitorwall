"""wall_config.json Verwaltung — Canvas-Layout, Playlists, Sync-Config.

Laedt, validiert und speichert die zentrale Displaywall-Konfiguration.
"""

import json
from pathlib import Path

from videowall.config import CONFIG_DIR

WALL_CONFIG = CONFIG_DIR / "wall_config.json"

# Hardware-Outputs: <hostname>-<hdmi_nr>
ALL_OUTPUTS = [
    "head-1", "head-2",
    "slave1-1", "slave1-2",
    "slave2-1", "slave2-2",
]

_DEFAULT_CONFIG = {
    "version": 1,
    "canvas": {
        "width": 8000,
        "height": 3000,
        "monitors": [
            {
                "id": "head-1",
                "label": "Head Display 1",
                "x": 0,
                "y": 0,
                "width": 2560,
                "height": 1440,
                "rotation": 0,
                "output": "head-1",
            },
            {
                "id": "head-2",
                "label": "Head Display 2",
                "x": 2660,
                "y": 0,
                "width": 2560,
                "height": 1440,
                "rotation": 0,
                "output": "head-2",
            },
            {
                "id": "slave1-1",
                "label": "Slave1 Display 1",
                "x": 0,
                "y": 1540,
                "width": 2560,
                "height": 1440,
                "rotation": 0,
                "output": "slave1-1",
            },
            {
                "id": "slave1-2",
                "label": "Slave1 Display 2",
                "x": 2660,
                "y": 1540,
                "width": 2560,
                "height": 1440,
                "rotation": 0,
                "output": "slave1-2",
            },
            {
                "id": "slave2-1",
                "label": "Slave2 Display 1",
                "x": 5320,
                "y": 0,
                "width": 2560,
                "height": 1440,
                "rotation": 0,
                "output": "slave2-1",
            },
            {
                "id": "slave2-2",
                "label": "Slave2 Display 2",
                "x": 5320,
                "y": 1540,
                "width": 2560,
                "height": 1440,
                "rotation": 0,
                "output": "slave2-2",
            },
        ],
    },
    "playlists": {
        "head-1": [],
        "head-2": [],
        "slave1-1": [],
        "slave1-2": [],
        "slave2-1": [],
        "slave2-2": [],
    },
    "sync": {
        "enabled": False,
        "master": "head-pi",
        "port": 1666,
        "offsets": {out: 0 for out in ALL_OUTPUTS},
    },
}


def load_wall_config():
    """Wall-Konfiguration laden. Legt Default an falls nicht vorhanden.

    Prueft ob Asset-Dateien existieren und markiert fehlende mit 'missing': True.
    """
    try:
        with open(WALL_CONFIG) as f:
            config = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        save_wall_config(_DEFAULT_CONFIG)
        return _default_copy()
    _validate_assets(config)
    return config


def _validate_assets(config):
    """Jedes Playlist-Item pruefen: existiert die Asset-Datei?"""
    for output_id, playlist in config.get("playlists", {}).items():
        for item in playlist:
            uri = item.get("uri", "")
            asset_path = Path(uri)
            if asset_path.is_file() and asset_path.stat().st_size > 100:
                item.pop("missing", None)
            else:
                item["missing"] = True


def save_wall_config(data):
    """Wall-Konfiguration speichern."""
    WALL_CONFIG.parent.mkdir(parents=True, exist_ok=True)
    with open(WALL_CONFIG, "w") as f:
        json.dump(data, f, indent=2)


def get_playlist(output_id):
    """Playlist fuer einen bestimmten Output lesen."""
    config = load_wall_config()
    return config.get("playlists", {}).get(output_id, [])


def set_playlist(output_id, playlist):
    """Playlist fuer einen Output setzen und speichern."""
    config = load_wall_config()
    if "playlists" not in config:
        config["playlists"] = {}
    config["playlists"][output_id] = playlist
    save_wall_config(config)
    return True


def update_monitor(monitor_id, updates):
    """Monitor-Eigenschaften im Canvas aktualisieren (Position, Rotation, etc.)."""
    config = load_wall_config()
    for mon in config.get("canvas", {}).get("monitors", []):
        if mon["id"] == monitor_id:
            mon.update(updates)
            save_wall_config(config)
            return True
    return False


def _default_copy():
    """Tiefe Kopie der Default-Konfiguration."""
    return json.loads(json.dumps(_DEFAULT_CONFIG))
