#!/usr/bin/env python3
"""Videowall Manager — Web-Server.

HTTP-Server auf Port 8080. Liefert die Web-GUI (statische Dateien)
und die REST-API fuer Asset-Verwaltung und Display-Konfiguration.
"""

import hashlib
import json
import logging
import mimetypes
import re
import subprocess
import urllib.request
import uuid
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse

from videowall.config import (
    WEBUI_PORT, ASSET_DIR, CONFIG_DIR,
    PLAYBACK_STATE_FILE, VIEWER_CMD_FILE, SLAVES_JSON,
    load_displays, save_displays,
)
from videowall.db import get_assets, add_asset, delete_asset, init_db
from videowall.status import get_status
from videowall.wall import (
    load_wall_config,
    save_wall_config,
    set_playlist,
    update_monitor,
)

WEBUI_DIR = Path(__file__).parent / "webui"
THUMB_DIR = ASSET_DIR / ".thumbs"
THUMB_WIDTH = 320

# Slave-Registry: {hostname: {ip, port}}
_DEFAULT_SLAVES = {
    "slave2": {"ip": "192.168.192.63", "port": 8081},
}


def _get_thumbnail(src_path):
    """Thumbnail per ffmpeg erzeugen und cachen. Gibt Thumb-Pfad zurueck oder None."""
    THUMB_DIR.mkdir(parents=True, exist_ok=True)
    stat = src_path.stat()
    key = f"{src_path.name}_{stat.st_size}_{int(stat.st_mtime)}"
    thumb_name = hashlib.md5(key.encode()).hexdigest() + ".jpg"
    thumb_path = THUMB_DIR / thumb_name
    if thumb_path.exists():
        return thumb_path
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-i", str(src_path),
             "-vf", f"scale={THUMB_WIDTH}:-1",
             "-frames:v", "1", "-q:v", "8",
             str(thumb_path)],
            capture_output=True, timeout=5,
        )
        if thumb_path.exists():
            return thumb_path
    except Exception:
        pass
    return None


def _push_rotation_to_slave(monitor_id, rotation):
    """Rotation an den zugehoerigen Slave weiterleiten."""
    if not monitor_id:
        return
    parts = monitor_id.rsplit("-", 1)
    if len(parts) != 2:
        return
    slave_name = parts[0]
    display_num = parts[1]
    connector = f"HDMI-A-{display_num}"
    slaves = _load_slaves()
    info = slaves.get(slave_name)
    if not info or not info.get("ip"):
        return
    try:
        url = f"http://{info['ip']}:{info.get('port', 8081)}/api/rotation"
        body = json.dumps({"connector": connector, "rotation": rotation}).encode()
        req = urllib.request.Request(url, data=body,
                                     headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=2)
    except Exception as e:
        logging.warning("Rotation-Push an %s fehlgeschlagen: %s", slave_name, e)


def _push_playlist_to_slave(monitor_id, playlist, shuffle=False):
    """Playlist an den zugehoerigen Slave weiterleiten."""
    if not monitor_id:
        return
    parts = monitor_id.rsplit("-", 1)
    if len(parts) != 2:
        return
    slave_name = parts[0]
    slaves = _load_slaves()
    info = slaves.get(slave_name)
    if not info or not info.get("ip"):
        return
    url = f"http://{info['ip']}:{info.get('port', 8081)}/api/playlist"
    body = json.dumps({
        "monitor_id": monitor_id,
        "items": playlist,
        "shuffle": shuffle,
    }).encode()
    try:
        req = urllib.request.Request(url, data=body,
                                     headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=3)
        logging.info("Playlist an %s gesendet: %s (%d Assets)", slave_name, monitor_id, len(playlist))
    except Exception as e:
        logging.warning("Playlist-Push an %s fehlgeschlagen: %s", slave_name, e)


def _load_slaves():
    try:
        return json.loads(SLAVES_JSON.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        SLAVES_JSON.parent.mkdir(parents=True, exist_ok=True)
        SLAVES_JSON.write_text(json.dumps(_DEFAULT_SLAVES, indent=2))
        return _DEFAULT_SLAVES.copy()


def _query_slave(name, info):
    """Status eines Slaves abfragen (mit Timeout)."""
    ip = info.get("ip", "")
    port = info.get("port", 8081)
    if not ip:
        return {"hostname": name, "online": False, "error": "Keine IP konfiguriert"}
    url = f"http://{ip}:{port}/api/status"
    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=2) as resp:
            data = json.loads(resp.read())
            data["online"] = True
            return data
    except Exception as e:
        return {"hostname": name, "ip": ip, "online": False, "error": str(e)}


def _read_playback_state():
    """Liest den Playback-Status aller Viewer (Head + Slaves)."""
    state = {}
    try:
        if PLAYBACK_STATE_FILE.is_file():
            state = json.loads(PLAYBACK_STATE_FILE.read_text())
    except Exception:
        pass
    # Head-Monitor-IDs merken — duerfen nicht von Slaves ueberschrieben werden
    head_ids = {k for k in state}
    slaves = _load_slaves()
    for name, info in slaves.items():
        ip = info.get("ip")
        if not ip:
            continue
        try:
            url = f"http://{ip}:{info.get('port', 8081)}/api/playback"
            with urllib.request.urlopen(url, timeout=1) as resp:
                slave_state = json.loads(resp.read())
                # Nur Slave-eigene Monitor-IDs uebernehmen, NICHT Head-IDs
                for mid, val in slave_state.items():
                    if mid not in head_ids:
                        state[mid] = val
        except Exception:
            pass
    return state


# Mapping Monitor-ID <-> Connector (Head-Pi)
_MONITOR_TO_CONNECTOR = {
    "head-1": "HDMI-A-1",
    "head-2": "HDMI-A-2",
}
_CONNECTOR_TO_MONITOR = {v: k for k, v in _MONITOR_TO_CONNECTOR.items()}


def _sync_rotation_to_wall(connector, rotation):
    """Rotation aus displays.json in wall_config.json synchronisieren."""
    monitor_id = _CONNECTOR_TO_MONITOR.get(connector)
    if monitor_id:
        update_monitor(monitor_id, {"rotation": rotation})


def _sync_rotation_to_displays(monitor_id, rotation):
    """Rotation aus wall_config.json in displays.json synchronisieren."""
    connector = _MONITOR_TO_CONNECTOR.get(monitor_id)
    if connector:
        displays = load_displays()
        if connector in displays:
            displays[connector]["rotation"] = rotation
            save_displays(displays)
    else:
        _push_rotation_to_slave(monitor_id, rotation)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def _send_json(self, data, code=200):
        body = json.dumps(data).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", len(body))
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, filepath):
        if not filepath.is_file():
            self.send_error(404)
            return
        content_type, _ = mimetypes.guess_type(str(filepath))
        body = filepath.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type or "application/octet-stream")
        self.send_header("Content-Length", len(body))
        # Kein Browser-Cache fuer JS/CSS/HTML — verhindert veraltete Versionen
        if content_type and content_type.startswith(("text/", "application/javascript")):
            self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self):
        length = int(self.headers.get("Content-Length", 0))
        return json.loads(self.rfile.read(length)) if length else {}

    # Captive-Portal: Probe-URLs der Betriebssysteme erkennen
    _CAPTIVE_PORTAL_PATHS = {
        "/hotspot-detect.html",        # Apple iOS/macOS
        "/library/test/success.html",  # Apple (alternativ)
        "/generate_204",               # Android/Chrome
        "/gen_204",                     # Android (alternativ)
        "/connecttest.txt",             # Windows
        "/ncsi.txt",                    # Windows (alternativ)
        "/redirect",                    # Windows 10+
        "/canonical.html",             # Firefox
        "/success.txt",                # Firefox (alternativ)
    }

    def do_GET(self):
        path = urlparse(self.path).path

        # Captive-Portal-Erkennung: Probe-URLs der Betriebssysteme
        if path.lower() in self._CAPTIVE_PORTAL_PATHS:
            self.send_response(302)
            self.send_header("Location", "http://10.0.0.1:8080/")
            self.end_headers()
            return

        if path == "/" or path == "":
            self._send_file(WEBUI_DIR / "index.html")
        elif path.startswith("/static/"):
            filename = path[len("/static/"):]
            safe_path = (WEBUI_DIR / filename).resolve()
            if safe_path.is_relative_to(WEBUI_DIR):
                self._send_file(safe_path)
            else:
                self.send_error(403)
        elif path.startswith("/assets/"):
            filename = path[len("/assets/"):]
            safe_path = (ASSET_DIR / filename).resolve()
            if safe_path.is_relative_to(ASSET_DIR):
                self._send_file(safe_path)
            else:
                self.send_error(403)
        elif path.startswith("/thumb/"):
            filename = path[len("/thumb/"):]
            safe_path = (ASSET_DIR / filename).resolve()
            if safe_path.is_relative_to(ASSET_DIR) and safe_path.exists():
                thumb = _get_thumbnail(safe_path)
                if thumb:
                    self._send_file(thumb)
                else:
                    self._send_file(safe_path)
            else:
                self.send_error(404)
        elif path == "/api/assets":
            self._send_json(get_assets())
        elif path == "/api/displays":
            self._send_json(load_displays())
        elif path == "/api/status":
            self._send_json(get_status())
        elif path == "/api/wall":
            self._send_json(load_wall_config())
        elif path == "/api/pool":
            self._send_json(get_assets())
        elif path == "/api/playback":
            self._send_json(_read_playback_state())
        elif path == "/api/slaves":
            slaves = _load_slaves()
            result = {}
            for name, info in slaves.items():
                result[name] = _query_slave(name, info)
            self._send_json(result)
        elif path == "/api/provision":
            self._handle_provision()
        elif path == "/api/provision/player":
            self._send_file(Path(__file__).parent / "videowall-player.py")
        elif path == "/api/provision/setup":
            self._send_file(Path(__file__).parent / "setup-slave.sh")
        else:
            self.send_error(404)

    def do_POST(self):
        path = urlparse(self.path).path

        if path == "/api/upload":
            self._handle_upload()
            return

        data = self._read_body()

        if path == "/api/rotation":
            displays = load_displays()
            output = data.get("output")
            rotation = data.get("rotation", 0)
            if output in displays:
                displays[output]["rotation"] = rotation
                save_displays(displays)
            _sync_rotation_to_wall(output, rotation)
            self._send_json({"ok": True})

        elif path == "/api/wall":
            save_wall_config(data)
            for mon in data.get("canvas", {}).get("monitors", []):
                mid = mon.get("id", "")
                rot = mon.get("rotation")
                if rot is not None:
                    _sync_rotation_to_displays(mid, rot)
            self._send_json({"ok": True})

        elif path == "/api/playlist":
            output_id = data.get("output")
            playlist = data.get("playlist", [])
            ok = set_playlist(output_id, playlist)
            _push_playlist_to_slave(output_id, playlist, data.get("shuffle", False))
            self._send_json({"ok": ok})

        elif path == "/api/delete":
            asset_id = data.get("asset_id")
            if asset_id:
                ok = delete_asset(asset_id)
                if ok:
                    for f in ASSET_DIR.glob(f"{asset_id}.*"):
                        f.unlink(missing_ok=True)
                    wc = load_wall_config()
                    changed = False
                    for pl_id, pl in wc.get("playlists", {}).items():
                        before = len(pl)
                        wc["playlists"][pl_id] = [
                            a for a in pl
                            if asset_id not in a.get("uri", "")
                        ]
                        if len(wc["playlists"][pl_id]) < before:
                            changed = True
                    if changed:
                        save_wall_config(wc)
                self._send_json({"ok": ok})
            else:
                self._send_json({"ok": False, "error": "asset_id fehlt"}, 400)

        elif path == "/api/asset/rotate":
            self._handle_asset_rotate(data)

        elif path == "/api/slaves":
            slaves = _load_slaves()
            slaves.update(data)
            SLAVES_JSON.write_text(json.dumps(slaves, indent=2))
            self._send_json({"ok": True})

        elif path == "/api/viewer/command":
            VIEWER_CMD_FILE.write_text(json.dumps(data))
            monitor = data.get("monitor", "")
            if not monitor or not monitor.startswith("head"):
                self._forward_command_to_slaves(data)
            self._send_json({"ok": True})

        elif path == "/api/slave/command":
            self._handle_slave_command(data)

        elif path == "/api/monitor":
            monitor_id = data.get("id")
            updates = data.get("updates", {})
            ok = update_monitor(monitor_id, updates)
            if "rotation" in updates and monitor_id:
                _sync_rotation_to_displays(monitor_id, updates["rotation"])
            self._send_json({"ok": ok})

        else:
            self.send_error(404)

    def _handle_upload(self):
        """Datei-Upload: speichert in ASSET_DIR, traegt in DB ein."""
        content_type = self.headers.get("Content-Type", "")
        content_length = int(self.headers.get("Content-Length", 0))

        if "multipart/form-data" not in content_type:
            self._send_json({"ok": False, "error": "multipart erwartet"}, 400)
            return

        body = self.rfile.read(content_length)

        m = re.search(r"boundary=([^\s;]+)", content_type)
        if not m:
            self._send_json({"ok": False, "error": "Kein boundary"}, 400)
            return
        boundary = m.group(1).encode()

        parts = {}
        chunks = body.split(b"--" + boundary)
        for part in chunks:
            if b"Content-Disposition" not in part:
                continue
            header, _, payload = part.partition(b"\r\n\r\n")
            if payload.endswith(b"\r\n"):
                payload = payload[:-2]

            name_m = re.search(rb'name="([^"]+)"', header)
            if not name_m:
                continue
            name = name_m.group(1).decode()

            fname_m = re.search(rb'filename="([^"]+)"', header)
            if fname_m:
                parts[name] = {"data": payload, "filename": fname_m.group(1).decode()}
            else:
                parts[name] = {"data": payload.decode(errors="replace")}

        file_part = parts.get("file")
        if not file_part or "data" not in file_part:
            self._send_json({"ok": False, "error": "Keine Datei"}, 400)
            return

        file_data = file_part["data"]
        filename = file_part.get("filename", "upload")
        duration = parts.get("duration", {}).get("data", "10")

        mime, _ = mimetypes.guess_type(filename)
        if not mime:
            mime = "application/octet-stream"

        ASSET_DIR.mkdir(parents=True, exist_ok=True)
        asset_id = str(uuid.uuid4())
        ext = Path(filename).suffix
        dest = ASSET_DIR / (asset_id + ext)
        dest.write_bytes(file_data)

        MAX_W, MAX_H = 2560, 1440
        if mime and mime.startswith("image/"):
            try:
                from PIL import Image
                img = Image.open(dest)
                w, h = img.size
                if w > MAX_W or h > MAX_H:
                    ratio = min(MAX_W / w, MAX_H / h)
                    new_w, new_h = int(w * ratio), int(h * ratio)
                    img = img.resize((new_w, new_h), Image.LANCZOS)
                    img.save(dest, quality=92)
                    logging.info("Asset skaliert: %s %dx%d -> %dx%d", filename, w, h, new_w, new_h)
                img.close()
            except Exception as e:
                logging.warning("Skalierung fehlgeschlagen: %s — %s", filename, e)

        uri = str(dest)
        ok = add_asset(asset_id, filename, uri, mime, int(duration), len(file_data))
        self._send_json({"ok": ok, "asset_id": asset_id, "name": filename})

    def _handle_asset_rotate(self, data):
        """Asset-Bild um 90 Grad im Uhrzeigersinn drehen (permanent, per PIL)."""
        asset_id = data.get("asset_id", "")
        filename = data.get("filename", "")
        if not asset_id and not filename:
            self._send_json({"ok": False, "error": "asset_id oder filename fehlt"}, 400)
            return
        # Asset-Datei finden: zuerst per Filename, dann per UUID-Glob
        src = ASSET_DIR / filename if filename else None
        if not src or not src.exists():
            matches = list(ASSET_DIR.glob(f"{asset_id}.*"))
            src = matches[0] if matches else None
        if not src or not src.exists():
            self._send_json({"ok": False, "error": "Datei nicht gefunden"}, 404)
            return
        try:
            from PIL import Image
            img = Image.open(src)
            # EXIF-Orientierung anwenden falls vorhanden
            try:
                from PIL import ImageOps
                img = ImageOps.exif_transpose(img)
            except Exception:
                pass
            # 90 Grad im Uhrzeigersinn = 270 Grad gegen Uhrzeigersinn (PIL-Konvention)
            rotated = img.rotate(-90, expand=True)
            rotated.save(src, quality=95)
            # Thumbnail-Cache invalidieren
            THUMB_DIR.mkdir(parents=True, exist_ok=True)
            for t in THUMB_DIR.glob("*"):
                t.unlink(missing_ok=True)
            logging.info("Asset rotiert: %s", src.name)
            self._send_json({"ok": True, "asset": src.name})
        except Exception as e:
            self._send_json({"ok": False, "error": str(e)})

    def _forward_command_to_slaves(self, data):
        """Befehl an alle Slaves weiterleiten (fire-and-forget)."""
        slaves = _load_slaves()
        for name, info in slaves.items():
            ip = info.get("ip")
            if not ip:
                continue
            url = f"http://{ip}:{info.get('port', 8081)}/api/command"
            try:
                body = json.dumps({"cmd": data.get("cmd", ""), "monitor": ""}).encode()
                req = urllib.request.Request(url, data=body,
                                             headers={"Content-Type": "application/json"})
                urllib.request.urlopen(req, timeout=2)
            except Exception:
                pass

    def _handle_slave_command(self, data):
        """Befehl an einen Slave weiterleiten."""
        slave_name = data.get("slave", "")
        slaves = _load_slaves()
        info = slaves.get(slave_name)
        if not info or not info.get("ip"):
            self._send_json({"ok": False, "error": f"Slave {slave_name} nicht konfiguriert"}, 404)
            return
        url = f"http://{info['ip']}:{info.get('port', 8081)}/api/command"
        try:
            body = json.dumps(data).encode()
            req = urllib.request.Request(url, data=body,
                                         headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=3) as resp:
                result = json.loads(resp.read())
                self._send_json(result)
        except Exception as e:
            self._send_json({"ok": False, "error": str(e)})

    def _handle_provision(self):
        """Provisioning-Info fuer neue Slaves."""
        import socket
        head_ip = ""
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            head_ip = s.getsockname()[0]
            s.close()
        except Exception:
            pass

        wc = load_wall_config()
        monitors = wc.get("canvas", {}).get("monitors", [])
        used = set()
        for m in monitors:
            mid = m.get("id", "")
            if mid.startswith("slave"):
                used.add(mid.split("-")[0])

        info = {
            "head_ip": head_ip,
            "head_port": WEBUI_PORT,
            "setup_url": f"http://{head_ip}:{WEBUI_PORT}/api/provision/setup",
            "player_url": f"http://{head_ip}:{WEBUI_PORT}/api/provision/player",
            "known_slaves": sorted(used),
            "wall_config": wc,
        }
        self._send_json(info)


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [mgr] %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )
    init_db()
    load_displays()
    server = HTTPServer(("0.0.0.0", WEBUI_PORT), Handler)
    logging.info("Videowall Manager auf Port %d", WEBUI_PORT)
    logging.info("Web-UI: %s", WEBUI_DIR)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    server.server_close()


if __name__ == "__main__":
    main()
