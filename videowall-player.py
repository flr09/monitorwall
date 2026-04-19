#!/usr/bin/env python3
"""Videowall Player — unified fuer Head und Slave.

Erkennt per Hostname automatisch die Rolle:
  head   → SyncMaster, Playlists aus wall_config.json, Pre-Decode
  slave* → SyncSlave, HTTP-API (Port 8081), Asset-Cache, Pull-Loop

Startet 2x mpv (HDMI-A-1, HDMI-A-2) und steuert per IPC.

Aufruf: videowall-player.py [--displays name-1:HDMI-A-1,name-2:HDMI-A-2]
"""

import argparse
import hashlib
import json
import logging
import os
import random
import re
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from functools import partial
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path

from videowall.config import (
    HOSTNAME, IS_HEAD, CONFIG_DIR, DISPLAYS_JSON, ASSET_DIR,
    PLAYBACK_STATE_FILE, VIEWER_CMD_FILE, SLAVES_JSON, PLAYLIST_FILE,
    CONNECTOR_1, CONNECTOR_2, SYNC_PORT, AGENT_PORT,
    HEAD_HOST, HEAD_PORT, USB_MOUNT,
    load_displays, save_displays, resolve_uri,
)
from videowall.sync import (
    TickClock, DeterministicPlaylist, SyncMaster, SyncSlave, hw_now,
)
from videowall.wall import load_wall_config, WALL_CONFIG
from videowall.mpv import MpvInstance

try:
    import systemd.daemon
    HAS_SYSTEMD = True
except ImportError:
    HAS_SYSTEMD = False

# --- Logging ---

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s.%(msecs)03d [%(role)s] %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
# Role-Tag im Log
_role = "head" if IS_HEAD else "slave"
_old_factory = logging.getLogRecordFactory()
def _record_factory(*args, **kwargs):
    record = _old_factory(*args, **kwargs)
    record.role = _role
    return record
logging.setLogRecordFactory(_record_factory)

# --- Pfade ---

COMMAND_FILE = VIEWER_CMD_FILE  # Alias fuer Abwaertskompatibilitaet

# --- Globaler State ---

instances = []          # Liste von MpvInstance
playlists = {}          # monitor_id -> [items]
playback_cfg = {}       # monitor_id -> {"shuffle": bool}
monitor_ids = []        # z.B. ["slave2-1", "slave2-2"]
_sync_slave = None      # Globale Referenz fuer Status-API (nur Slave)

MPV_STARTUP_DELAY = 2   # Sekunden zwischen mpv-Starts (DRM-Konflikte)
PRELOAD_SECONDS = 3     # Pre-Decode: Sekunden vor Wechsel vorladen
PULL_INTERVAL = 30      # Slave: Pull-Intervall in Sekunden


# ============================================================
# Gemeinsamer Code (Head + Slave)
# ============================================================

def get_monitor_ids():
    """Monitor-IDs aus Hostname ableiten: slave1 -> [slave1-1, slave1-2]."""
    return [f"{HOSTNAME}-1", f"{HOSTNAME}-2"]


def write_playback_state(states):
    """Schreibt Playback-State aller Displays."""
    try:
        PLAYBACK_STATE_FILE.write_text(json.dumps(states))
    except Exception:
        pass


def start_mpv_instances(display_list):
    """mpv-Instanzen starten (nacheinander, mit Pause dazwischen)."""
    disp_config = load_displays()
    started = []

    for d in display_list:
        rotation = disp_config.get(d["connector"], {}).get("rotation", 0)
        sock = f"/tmp/mpv-{d['id']}.sock"
        inst = MpvInstance(d["id"], d["connector"], rotation, sock)
        if inst.start():
            started.append(inst)
            logging.info("[%s] mpv laeuft", d["id"])
        else:
            logging.error("[%s] mpv-Start fehlgeschlagen", d["id"])
        if len(started) < len(display_list):
            time.sleep(MPV_STARTUP_DELAY)

    return started


def playback_loop(tick_clock, sync_master=None, sync_slave=None):
    """Gemeinsamer Playback-Loop fuer Head und Slave.

    Tick-basierte Bildwechsel mit Pre-Decode und Barrier-Sync.
    """
    global playlists, playback_cfg

    last_wall_mtime = 0
    last_disp_mtime = 0
    counters = {}        # monitor_id -> DeterministicPlaylist
    shuffle_flags = {}   # monitor_id -> bool
    playback_state = {}
    paused = set()       # Monitor-IDs die pausiert/gestoppt sind
    force_next = set()   # Monitor-IDs die einmalig weiterschalten
    last_tick = -1

    while True:
        # Aktuelle Tick-Nummer — synchron zum Master wenn verfuegbar
        if sync_slave and sync_slave.has_master():
            t = sync_slave.get_local_tick()
            if t is not None:
                current_tick = t[0] if isinstance(t, tuple) else t
            else:
                current_tick = tick_clock.tick()
        else:
            current_tick = tick_clock.tick()

        # --- Crash-Recovery: abgestuerzte mpv-Instanzen neu starten ---
        for inst in instances:
            if not inst.is_alive():
                logging.warning("[%s] mpv abgestuerzt — Neustart", inst.monitor_id)
                disp_config = load_displays()
                rotation = disp_config.get(inst.connector, {}).get("rotation", 0)
                inst.rotation = rotation
                inst.start()
                inst.current_uri = None
                inst._playlist_loaded = False

        # --- Rotation aus displays.json live anwenden ---
        try:
            disp_mtime = DISPLAYS_JSON.stat().st_mtime
        except OSError:
            disp_mtime = 0

        if disp_mtime != last_disp_mtime:
            last_disp_mtime = disp_mtime
            disp_config = load_displays()
            for inst in instances:
                new_rot = disp_config.get(inst.connector, {}).get("rotation", 0)
                inst.set_rotation(new_rot)

        # --- Playlists laden ---
        if IS_HEAD:
            # Head: aus wall_config.json (File-Watch)
            try:
                wall_mtime = WALL_CONFIG.stat().st_mtime
            except OSError:
                wall_mtime = 0

            if wall_mtime != last_wall_mtime:
                last_wall_mtime = wall_mtime
                wc = load_wall_config()
                for inst in instances:
                    old_pl = playlists.get(inst.monitor_id, [])
                    new_pl = wc.get("playlists", {}).get(inst.monitor_id, [])
                    playlists[inst.monitor_id] = new_pl
                    shuffle_flags[inst.monitor_id] = wc.get("playback", {}).get(
                        inst.monitor_id, {}).get("shuffle", False)
                    if new_pl != old_pl:
                        logging.info("[%s] Playlist: %d Assets",
                                     inst.monitor_id, len(new_pl))
                        counters[inst.monitor_id] = DeterministicPlaylist(new_pl)
        else:
            # Slave: Playlists werden extern gesetzt (HTTP-API / Pull-Loop)
            # Nur Counter aktualisieren wenn sich was geaendert hat
            for inst in instances:
                mid = inst.monitor_id
                pl = playlists.get(mid, [])
                shuffle_flags[mid] = playback_cfg.get(mid, {}).get("shuffle", False)
                if mid not in counters and pl:
                    counters[mid] = DeterministicPlaylist(pl)

        # --- Externe Befehle (Head: viewer_cmd.json, Slave: via HTTP → cmd file) ---
        if COMMAND_FILE.exists():
            try:
                cmds = json.loads(COMMAND_FILE.read_text())
                COMMAND_FILE.unlink()
                if not isinstance(cmds, list):
                    cmds = [cmds]
                for cmd in cmds:
                    _process_command(cmd, counters, paused, force_next)
            except Exception as e:
                logging.warning("Command-Datei Fehler: %s", e)

        # --- Tick-basierte Wechsellogik ---
        new_tick = current_tick != last_tick
        if new_tick:
            last_tick = current_tick

        # Pre-Decode: naechstes Bild 3s vor Wechsel vorladen
        if new_tick:
            for inst in instances:
                counter = counters.get(inst.monitor_id)
                if not counter or not counter.playlist or len(counter.playlist) <= 1:
                    continue
                if inst.monitor_id in paused:
                    continue
                if inst._preloaded_uri:
                    continue
                next_switch = counter.next_switch_tick(current_tick)
                ticks_until = next_switch - current_tick
                if 0 < ticks_until <= PRELOAD_SECONDS:
                    next_idx = counter.peek_next_index(current_tick)
                    next_asset = counter.playlist[next_idx]
                    next_uri = _resolve_asset_uri(next_asset)
                    if next_uri and Path(next_uri).exists():
                        inst.preload_next(next_uri)

        # Bildwechsel sammeln
        pending_switches = []
        for inst in instances:
            counter = counters.get(inst.monitor_id)
            if not counter or not counter.playlist:
                continue

            # Force next/prev (auch ohne neuen Tick)
            if inst.monitor_id in force_next:
                force_next.discard(inst.monitor_id)
                new_index = counter.index
                pl = counter.playlist
                asset = pl[new_index]
                uri = _resolve_asset_uri(asset)
                name = asset.get("asset", "Unknown")
                if uri and Path(uri).exists():
                    pending_switches.append((inst, uri, name, new_index))
                    playback_state[inst.monitor_id] = {
                        "index": new_index, "asset": name}
                continue

            # Pausiert: Counter nicht weiterzaehlen
            if inst.monitor_id in paused:
                continue

            if not new_tick:
                continue

            # Shuffle
            shuffle = shuffle_flags.get(inst.monitor_id, False)
            if shuffle:
                old_remaining = counter.remaining
                should_switch, _ = counter.update(current_tick)
                if should_switch:
                    new_index = counter.set_random_index()
                else:
                    continue
            else:
                should_switch, new_index = counter.update(current_tick)

            if not should_switch:
                continue

            pl = counter.playlist
            asset = pl[new_index]
            uri = _resolve_asset_uri(asset)
            name = asset.get("asset", "Unknown")

            if not uri or not Path(uri).exists():
                logging.warning("[%s] Datei fehlt: %s", inst.monitor_id, uri or name)
                counter.force_next()
                continue

            pending_switches.append((inst, uri, name, new_index))
            playback_state[inst.monitor_id] = {"index": new_index, "asset": name}

        # Alle faelligen Displays GLEICHZEITIG wechseln
        if pending_switches:
            if len(pending_switches) > 1:
                barrier = threading.Barrier(len(pending_switches), timeout=3)
            else:
                barrier = None

            def sync_load(inst, uri, pl_index, barrier_ref):
                if barrier_ref:
                    try:
                        barrier_ref.wait()
                    except threading.BrokenBarrierError:
                        pass
                inst.jump_to(pl_index, uri)

            threads = []
            for inst, uri, name, idx in pending_switches:
                logging.info("[%s] %s (idx %d, tick %d)",
                             inst.monitor_id, name, idx, current_tick)
                t = threading.Thread(
                    target=sync_load, args=(inst, uri, idx, barrier))
                threads.append(t)

            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=5)

            write_playback_state(playback_state)

        # Sync-Heartbeat an Slaves (nur Head)
        if IS_HEAD and sync_master and new_tick:
            sync_master.send_tick(0, tick_clock=tick_clock)

        # Sleep bis naechster Tick
        _wait_next_tick(tick_clock, sync_slave)


def _process_command(cmd, counters, paused, force_next):
    """Einen Steuerbefehl verarbeiten (next/prev/stop/play)."""
    action = cmd.get("cmd", "") or cmd.get("command", "")
    target = cmd.get("monitor", "")
    for inst in instances:
        if target and inst.monitor_id != target:
            continue
        counter = counters.get(inst.monitor_id)
        if not counter or not counter.playlist:
            continue
        if action == "next":
            counter.force_next()
            force_next.add(inst.monitor_id)
        elif action == "prev":
            counter.force_prev()
            force_next.add(inst.monitor_id)
        elif action in ("stop", "pause"):
            paused.add(inst.monitor_id)
        elif action == "play":
            paused.discard(inst.monitor_id)
            force_next.add(inst.monitor_id)
        logging.info("[%s] Befehl: %s", inst.monitor_id, action)


def _resolve_asset_uri(asset):
    """URI eines Assets aufloesen — Head: resolve_uri, Slave: cache_asset."""
    uri = asset.get("uri", "")
    name = asset.get("asset", "")
    if IS_HEAD:
        return resolve_uri(uri)
    else:
        return cache_asset(uri, name)


def _wait_next_tick(tick_clock, sync_slave=None):
    """Bis zum naechsten Tick schlafen (200ms-Intervalle, Busy-Wait letzte 20ms)."""
    next_tick_hw = tick_clock.next_tick_hw()

    # Sync-korrigiert: wenn SyncSlave aktiv, T0-Offset beruecksichtigen
    if sync_slave and sync_slave.has_master():
        master_t0 = sync_slave.get_master_t0()
        if master_t0 is not None:
            with sync_slave._lock:
                avg_off = sync_slave._avg_offset()
            local_tick = int(hw_now() + avg_off - master_t0)
            next_tick_hw = master_t0 - avg_off + local_tick + 1

    delta = next_tick_hw - hw_now()
    if delta <= 0:
        return

    # Grob schlafen, letzte 20ms busy-wait
    sleep_until = time.time() + min(delta, 1.0)
    while time.time() < sleep_until - 0.02:
        if COMMAND_FILE.exists():
            return  # Sofort zurueck, Command verarbeiten
        time.sleep(min(0.2, max(0, sleep_until - time.time() - 0.02)))

    # Busy-Wait fuer Praezision (mit Command-Abbruch)
    while hw_now() < next_tick_hw:
        if COMMAND_FILE.exists():
            return


# ============================================================
# Slave-spezifischer Code
# ============================================================

def get_asset_dir():
    """Asset-Verzeichnis: USB wenn vorhanden, sonst SD-Karte."""
    usb_assets = USB_MOUNT / "videowall_assets"
    if USB_MOUNT.is_mount():
        usb_assets.mkdir(parents=True, exist_ok=True)
        return usb_assets
    ASSET_DIR.mkdir(parents=True, exist_ok=True)
    return ASSET_DIR


def cache_asset(uri, asset_name):
    """Asset vom Head-Pi laden falls nicht lokal vorhanden. Gibt lokalen Pfad zurueck."""
    asset_dir = get_asset_dir()

    if "/" in uri:
        filename = uri.rsplit("/", 1)[-1]
    else:
        filename = asset_name

    local_path = asset_dir / filename
    if local_path.is_file() and local_path.stat().st_size > 100:
        return str(local_path)

    # Vom Head-Pi herunterladen
    match = re.search(r'screenly_assets/(.+)$', uri)
    if match:
        remote_file = match.group(1)
        url = f"http://{HEAD_HOST}:{HEAD_PORT}/assets/{remote_file}"
    else:
        url = uri

    logging.info("Lade Asset: %s -> %s", url, local_path)
    try:
        urllib.request.urlretrieve(url, str(local_path))
        if local_path.stat().st_size < 100:
            logging.warning("Download zu klein (%d Bytes), loesche: %s",
                            local_path.stat().st_size, local_path)
            local_path.unlink()
            return None
        return str(local_path)
    except Exception as e:
        logging.error("Download fehlgeschlagen: %s — %s", url, e)
        if local_path.is_file():
            local_path.unlink()
        return None


def get_disk_info():
    """Speicherplatz-Info fuer aktives Asset-Verzeichnis."""
    asset_dir = get_asset_dir()
    try:
        total, used, free = shutil.disk_usage(str(asset_dir))
        return {
            "path": str(asset_dir),
            "total_gb": round(total / (1024**3), 1),
            "used_gb": round(used / (1024**3), 1),
            "free_gb": round(free / (1024**3), 1),
            "usb": USB_MOUNT.is_mount(),
        }
    except Exception:
        return {"path": str(asset_dir), "error": "nicht lesbar"}


def save_playlists():
    """Playlists persistent speichern (Slave)."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    data = {"playlists": playlists, "playback": playback_cfg}
    PLAYLIST_FILE.write_text(json.dumps(data, indent=2))


def load_playlists():
    """Gespeicherte Playlists laden (Slave, fuer Neustart ohne Head)."""
    global playlists, playback_cfg
    try:
        data = json.loads(PLAYLIST_FILE.read_text())
        playlists = data.get("playlists", {})
        playback_cfg = data.get("playback", {})
    except (FileNotFoundError, json.JSONDecodeError):
        playlists = {}
        playback_cfg = {}


def pull_wall_from_head():
    """Holt wall_config.json vom Head und uebernimmt relevante Playlists."""
    url = f"http://{HEAD_HOST}:{HEAD_PORT}/api/wall"
    try:
        with urllib.request.urlopen(url, timeout=3) as resp:
            wall = json.loads(resp.read())
    except Exception as e:
        logging.info("Pull vom Head nicht moeglich (%s) — nutze lokalen Cache", e)
        return False

    pls = wall.get("playlists", {})
    pb = wall.get("playback", {})
    changed = False
    for mid in monitor_ids:
        if mid not in pls:
            continue
        new_items = pls[mid]
        new_shuffle = pb.get(mid, {}).get("shuffle", False)
        old_items = playlists.get(mid, [])
        old_shuffle = playback_cfg.get(mid, {}).get("shuffle", False)
        if new_items == old_items and new_shuffle == old_shuffle:
            continue
        playlists[mid] = new_items
        playback_cfg[mid] = {"shuffle": new_shuffle}
        changed = True
        logging.info("[%s] Pull vom Head: %d Assets (shuffle=%s)",
                     mid, len(new_items), new_shuffle)
    if changed:
        save_playlists()
    return True


def pull_loop():
    """Zyklischer Pull vom Head alle 30s."""
    while True:
        time.sleep(PULL_INTERVAL)
        try:
            pull_wall_from_head()
        except Exception as e:
            logging.warning("Pull-Loop Ausnahme: %s", e)


# --- Slave HTTP-API ---

class AgentHandler(BaseHTTPRequestHandler):
    """REST-API fuer den Slave."""

    def log_message(self, fmt, *args):
        logging.debug("HTTP %s", fmt % args)

    def send_json(self, data, code=200):
        body = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", len(body))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/api/status":
            self.handle_status()
        elif self.path == "/api/playback":
            self.handle_playback()
        elif self.path == "/api/disk":
            self.send_json(get_disk_info())
        elif self.path.startswith("/assets/"):
            self.handle_asset()
        else:
            self.send_json({"agent": HOSTNAME, "monitors": monitor_ids})

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length) if length else b""

        if self.path == "/api/playlist":
            self.handle_set_playlist(body)
        elif self.path == "/api/command":
            self.handle_command(body)
        elif self.path == "/api/displays":
            self.handle_set_displays(body)
        elif self.path == "/api/rotation":
            self.handle_rotation(body)
        else:
            self.send_json({"error": "not found"}, 404)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def handle_status(self):
        """System-Status: Temperatur, Speicher, Viewer-State."""
        temp = _run(["vcgencmd", "measure_temp"])
        throttle = _run(["vcgencmd", "get_throttled"])

        sync_info = {}
        if _sync_slave:
            sync_info = {
                "has_master": _sync_slave.has_master(),
                "offset_ms": round(_sync_slave.get_offset_ms(), 1),
                "master_t0": _sync_slave.get_master_t0(),
            }
            lt = _sync_slave.get_local_tick()
            if lt is not None:
                if isinstance(lt, tuple):
                    sync_info["local_tick"] = lt[0]
                else:
                    sync_info["local_tick"] = lt

        viewer_states = {}
        for inst in instances:
            viewer_states[inst.monitor_id] = {
                "monitor": inst.monitor_id,
                "connector": inst.connector,
                "index": inst.index,
                "asset": inst.current_uri or "",
                "running": inst.is_alive(),
                "rotation": inst.rotation,
            }

        status = {
            "hostname": HOSTNAME,
            "ip": _get_ip(),
            "temperature": temp,
            "throttle": throttle.split("=")[-1] if "=" in throttle else throttle,
            "uptime": _get_uptime(),
            "disk": get_disk_info(),
            "memory": _get_memory(),
            "viewers": viewer_states,
            "sync": sync_info,
        }
        self.send_json(status)

    def handle_playback(self):
        """Aktueller Playback-State aller Viewer."""
        states = {}
        for inst in instances:
            states[inst.monitor_id] = {
                "monitor": inst.monitor_id,
                "index": inst.index,
                "asset": inst.current_uri or "",
                "running": inst.is_alive(),
            }
        self.send_json(states)

    def handle_set_playlist(self, body):
        """Playlist setzen: {"monitor_id": "slave2-1", "items": [...], "shuffle": false}"""
        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            self.send_json({"error": "invalid JSON"}, 400)
            return

        mid = data.get("monitor_id", "")
        items = data.get("items", [])
        shuffle = data.get("shuffle", False)

        found = any(inst.monitor_id == mid for inst in instances)
        if not found:
            self.send_json({"error": f"unknown monitor: {mid}"}, 404)
            return

        playlists[mid] = items
        playback_cfg[mid] = {"shuffle": shuffle}
        save_playlists()
        logging.info("[%s] Playlist gesetzt: %d Assets, shuffle=%s", mid, len(items), shuffle)

        self.send_json({"ok": True, "monitor": mid, "count": len(items)})

    def handle_command(self, body):
        """Steuerbefehl: {"command": "next|prev|stop|play", "monitor": "all"}"""
        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            self.send_json({"error": "invalid JSON"}, 400)
            return

        # Befehl in Command-File schreiben (wird vom Playback-Loop gelesen)
        cmd = data.get("command", "") or data.get("cmd", "")
        target = data.get("monitor", "all")

        # Direkt verarbeiten: Befehle muessen sofort wirken
        # Wir schreiben in die Command-Datei, damit der Loop sie aufnimmt
        cmd_data = {"cmd": cmd, "monitor": target}
        try:
            COMMAND_FILE.write_text(json.dumps(cmd_data))
        except Exception:
            pass

        self.send_json({"ok": True, "command": cmd, "target": target})

    def handle_set_displays(self, body):
        """Display-Konfiguration setzen (Rotation wird live angewendet)."""
        try:
            data = json.loads(body)
            save_displays(data)
            self.send_json({"ok": True})
        except json.JSONDecodeError:
            self.send_json({"error": "invalid JSON"}, 400)

    def handle_rotation(self, body):
        """Rotation fuer einen Connector: {"connector": "HDMI-A-1", "rotation": 90}"""
        try:
            data = json.loads(body)
            connector = data.get("connector", "")
            rotation = int(data.get("rotation", 0))
            displays = load_displays()
            if connector in displays:
                displays[connector]["rotation"] = rotation
                save_displays(displays)
            for inst in instances:
                if inst.connector == connector:
                    inst.set_rotation(rotation)
            self.send_json({"ok": True, "connector": connector, "rotation": rotation})
        except (json.JSONDecodeError, ValueError):
            self.send_json({"error": "invalid JSON"}, 400)

    def handle_asset(self):
        """Lokales Asset ausliefern."""
        filename = self.path.split("/assets/", 1)[-1]
        if ".." in filename or "/" in filename:
            self.send_json({"error": "forbidden"}, 403)
            return
        asset_dir = get_asset_dir()
        fpath = asset_dir / filename
        if not fpath.is_file():
            self.send_json({"error": "not found"}, 404)
            return
        self.send_response(200)
        ct = "application/octet-stream"
        lower = filename.lower()
        if lower.endswith((".jpg", ".jpeg")):
            ct = "image/jpeg"
        elif lower.endswith(".png"):
            ct = "image/png"
        elif lower.endswith(".mp4"):
            ct = "video/mp4"
        elif lower.endswith(".webm"):
            ct = "video/webm"
        self.send_header("Content-Type", ct)
        self.send_header("Content-Length", fpath.stat().st_size)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        with open(fpath, "rb") as f:
            shutil.copyfileobj(f, self.wfile)


# --- Hilfsfunktionen ---

def _run(cmd, timeout=3):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.stdout.strip()
    except Exception:
        return ""


def _get_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return ""


def _get_uptime():
    try:
        with open("/proc/uptime") as f:
            secs = int(float(f.read().split()[0]))
        days = secs // 86400
        hours = (secs % 86400) // 3600
        mins = (secs % 3600) // 60
        if days:
            return f"{days}d {hours}h {mins}m"
        if hours:
            return f"{hours}h {mins}m"
        return f"{mins}m"
    except Exception:
        return ""


def _get_memory():
    try:
        with open("/proc/meminfo") as f:
            raw = f.read()
        info = {}
        for line in raw.splitlines():
            parts = line.split()
            if len(parts) >= 2:
                info[parts[0].rstrip(":")] = int(parts[1])
        total = info.get("MemTotal", 0)
        avail = info.get("MemAvailable", 0)
        if total:
            used = total - avail
            return f"{used // 1024}/{total // 1024} MB ({int(used / total * 100)}%)"
    except Exception:
        pass
    return ""


# ============================================================
# Main
# ============================================================

def main():
    global monitor_ids, _sync_slave

    monitor_ids = get_monitor_ids()

    logging.info("Videowall Player gestartet: %s (Rolle: %s)",
                 HOSTNAME, "HEAD" if IS_HEAD else "SLAVE")
    logging.info("Monitore: %s", monitor_ids)

    # Verzeichnisse anlegen
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    ASSET_DIR.mkdir(parents=True, exist_ok=True)

    # Display-Liste aus Argumenten oder Default
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--displays",
        default=f"{HOSTNAME}-1:{CONNECTOR_1},{HOSTNAME}-2:{CONNECTOR_2}",
        help="Komma-getrennte Liste von monitor_id:connector Paaren",
    )
    args = parser.parse_args()

    display_list = []
    for pair in args.displays.split(","):
        parts = pair.strip().split(":")
        if len(parts) == 2:
            display_list.append({"id": parts[0], "connector": parts[1]})

    if not display_list:
        logging.error("Keine Displays konfiguriert")
        sys.exit(1)

    # SCHED_FIFO fuer praezises Timing
    try:
        os.sched_setscheduler(0, os.SCHED_FIFO, os.sched_param(1))
        logging.info("SCHED_FIFO aktiv (Prioritaet 1)")
    except PermissionError:
        logging.warning("SCHED_FIFO nicht verfuegbar (keine Berechtigung)")

    # mpv starten
    global instances
    instances = start_mpv_instances(display_list)

    if not instances:
        logging.error("Keine mpv-Instanz gestartet")
        sys.exit(1)

    logging.info("%d Display(s) aktiv", len(instances))

    # Signal-Handler
    def handle_signal(sig, frame):
        logging.info("Signal %s — beende...", sig)
        for inst in instances:
            inst.stop()
        sys.exit(0)

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    # Sync + TickClock
    sync_master = None
    sync_slave = None
    tick_clock = TickClock()
    logging.info("TickClock gestartet (T0=%.3f)", tick_clock.t0)

    if IS_HEAD:
        # Head: SyncMaster starten
        slave_ips = []
        if SLAVES_JSON.exists():
            try:
                slaves = json.loads(SLAVES_JSON.read_text())
                slave_ips = [s["ip"] for s in slaves.values() if s.get("ip")]
            except Exception:
                pass
        sync_master = SyncMaster(slave_ips=slave_ips)
        logging.info("SyncMaster aktiv (Port %d, Slaves: %s)",
                     SYNC_PORT, slave_ips or "nur Broadcast")
    else:
        # Slave: SyncSlave + HTTP-API + Pull-Loop
        sync_slave = SyncSlave()
        _sync_slave = sync_slave
        sync_slave.start()
        logging.info("SyncSlave aktiv (Port %d)", SYNC_PORT)

        # Gespeicherte Playlists laden
        load_playlists()

        # Einmaliger Pull vom Head
        pull_wall_from_head()

        # Pull-Loop im Hintergrund
        threading.Thread(target=pull_loop, daemon=True).start()
        logging.info("Pull-Loop aktiv (alle %ds)", PULL_INTERVAL)

        # HTTP-API im Hintergrund
        server = HTTPServer(("0.0.0.0", AGENT_PORT), AgentHandler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        logging.info("API lauscht auf Port %d", AGENT_PORT)

        # Systemd-Watchdog
        if HAS_SYSTEMD:
            def watchdog_loop():
                while True:
                    systemd.daemon.notify("WATCHDOG=1")
                    time.sleep(30)
            threading.Thread(target=watchdog_loop, daemon=True).start()
            logging.info("Systemd-Watchdog aktiviert")

    # Playback-Loop (blockiert)
    playback_loop(tick_clock, sync_master=sync_master, sync_slave=sync_slave)


if __name__ == "__main__":
    main()
