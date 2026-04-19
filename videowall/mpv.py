"""MpvInstance — persistenter mpv-Prozess fuer einen HDMI-Ausgang.

Merged aus viewer.py (MpvInstance) und displaywall-agent.py (ViewerThread).
Beste Teile beider Implementierungen:
- Pre-Decode aus viewer.py
- _cleanup_old_mpv aus Agent
- --cursor-autohide aus viewer.py
- --fs-screen={idx} aus Agent (robuster bei identischen Monitoren)
"""

import json
import logging
import os
import socket
import subprocess
import time
from pathlib import Path

MPV_STARTUP_TIMEOUT = 10


class MpvInstance:
    """Ein persistenter mpv-Prozess fuer einen HDMI-Ausgang."""

    def __init__(self, monitor_id, connector, rotation=0, sock_path=None):
        self.monitor_id = monitor_id
        self.connector = connector
        self.rotation = rotation
        self.sock_path = sock_path or f"/tmp/mpv-{monitor_id}.sock"
        self.process = None
        self.current_uri = None
        self.index = 0
        self._playlist_loaded = False
        self._playlist_size = 0
        self._pl_index_map = {}
        self._preloaded_uri = None

    def _cleanup_old_mpv(self):
        """Alte/verwaiste mpv-Prozesse fuer diesen Monitor killen."""
        try:
            result = subprocess.run(
                ["pgrep", "-f", f"mpv.*{self.sock_path}"],
                capture_output=True, text=True)
            for pid_str in result.stdout.strip().split("\n"):
                pid_str = pid_str.strip()
                if pid_str:
                    pid = int(pid_str)
                    if self.process and pid == self.process.pid:
                        continue
                    logging.info("[%s] Raeume alten mpv auf (PID %d)", self.monitor_id, pid)
                    os.kill(pid, 9)
        except Exception:
            pass

    def start(self, initial_file=None):
        """mpv starten. Mit initial_file direkt ein Bild anzeigen."""
        self._cleanup_old_mpv()
        try:
            os.unlink(self.sock_path)
        except OSError:
            pass

        wayland = os.environ.get("WAYLAND_DISPLAY")

        cmd = ["mpv", "--no-terminal", f"--input-ipc-server={self.sock_path}"]

        if wayland:
            # --fs-screen={idx}: robuster bei identischen Monitoren
            # HDMI-A-1 = 0, HDMI-A-2 = 1 (Pi 5 Reihenfolge)
            screen_idx = 0 if self.connector == "HDMI-A-1" else 1
            cmd += [
                "--vo=gpu",
                "--gpu-context=wayland",
                "--fullscreen",
                f"--fs-screen={screen_idx}",
            ]
        else:
            cmd += [
                "--vo=gpu",
                "--gpu-context=drm",
                f"--drm-connector={self.connector}",
            ]

        cmd += [
            "--keep-open=yes",
            "--image-display-duration=inf",
            "--cursor-autohide=always",
        ]

        if not initial_file:
            cmd += ["--idle=yes", "--force-window=yes"]

        if self.rotation:
            cmd.append(f"--video-rotate={self.rotation}")

        if initial_file:
            cmd.extend(["--", initial_file])

        logging.info("[%s] Starte mpv auf %s (Rotation: %d)",
                     self.monitor_id, self.connector, self.rotation)
        logging.info("[%s] CMD: %s", self.monitor_id, " ".join(cmd))

        self.process = subprocess.Popen(
            cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
        )

        # Warten bis Socket bereit
        for _ in range(MPV_STARTUP_TIMEOUT * 10):
            if Path(self.sock_path).exists():
                time.sleep(0.3)
                # Verifikation: mpv antwortet auf IPC?
                ok = self._ipc_send(["get_property", "idle-active"])
                if ok:
                    logging.info("[%s] mpv bereit (IPC verifiziert)", self.monitor_id)
                else:
                    logging.warning("[%s] mpv-Socket da, aber IPC antwortet nicht",
                                    self.monitor_id)
                return True
            time.sleep(0.1)

        # Startup fehlgeschlagen — stderr ausgeben
        try:
            stderr = self.process.stderr.read(2048).decode(errors="replace")
            if stderr:
                logging.error("[%s] mpv stderr: %s", self.monitor_id, stderr[:500])
        except Exception:
            pass

        logging.error("[%s] mpv-Socket nicht bereit", self.monitor_id)
        return False

    def _ipc_send(self, command):
        """IPC-Befehl senden und Antwort lesen.

        Gibt (True, data) bei Erfolg oder (False, error_msg) bei Fehler zurueck.
        Fuer Abwaertskompatibilitaet: bool(result) == True bei Erfolg.
        """
        try:
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            sock.settimeout(2.0)
            sock.connect(self.sock_path)
            payload = json.dumps({"command": command}) + "\n"
            sock.sendall(payload.encode())

            # Antwort lesen — mpv sendet Events + die eigentliche Antwort
            buf = b""
            sock.settimeout(1.0)
            try:
                while True:
                    chunk = sock.recv(4096)
                    if not chunk:
                        break
                    buf += chunk
                    # Antwort-Zeilen parsen: suche nach der mit "error" Key
                    for line in buf.split(b"\n"):
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            resp = json.loads(line)
                            if "error" in resp:
                                sock.close()
                                if resp["error"] == "success":
                                    return True
                                logging.warning("[%s] mpv-Fehler bei %s: %s",
                                                self.monitor_id, command, resp["error"])
                                return False
                        except json.JSONDecodeError:
                            continue
            except socket.timeout:
                pass
            sock.close()

            # Kein "error" Key in Antwort gefunden — Command wurde vermutlich akzeptiert
            if buf:
                logging.debug("[%s] IPC-Antwort ohne error-Key: %s",
                              self.monitor_id, buf[:200])
            return True
        except Exception as e:
            logging.warning("[%s] IPC-Fehler: %s", self.monitor_id, e)
            return False

    def load_file(self, uri):
        """Datei laden per IPC (replace)."""
        ok = self._ipc_send(["loadfile", uri, "replace"])
        if ok:
            self.current_uri = uri
            logging.info("[%s] loadfile OK: %s", self.monitor_id, Path(uri).name)
        else:
            logging.error("[%s] loadfile FEHLGESCHLAGEN: %s", self.monitor_id, uri)
        return ok

    def load_playlist(self, uris_with_indices):
        """Alle Items in mpv-Playlist vorladen fuer sofortigen Wechsel.

        uris_with_indices: Liste von (wall_config_index, uri) Tupeln.
        """
        self._ipc_send(["playlist-clear"])
        self._pl_index_map = {}
        mpv_idx = 0
        for wall_idx, uri in uris_with_indices:
            mode = "append-play" if mpv_idx == 0 else "append"
            self._ipc_send(["loadfile", uri, mode])
            self._pl_index_map[wall_idx] = mpv_idx
            mpv_idx += 1
        self._playlist_loaded = True
        self._playlist_size = mpv_idx
        logging.info("[%s] Playlist vorgeladen: %d Items", self.monitor_id, mpv_idx)

    def preload_next(self, uri):
        """Naechstes Bild vorladen (append). mpv decodiert im Hintergrund."""
        ok = self._ipc_send(["loadfile", uri, "append"])
        if ok:
            self._preloaded_uri = uri
            logging.info("[%s] Pre-decode: %s", self.monitor_id, Path(uri).name)
        return ok

    def switch_preloaded(self, uri):
        """Zum vorgeladenen Bild wechseln (playlist-next). Sofort, kein Decode."""
        if self._preloaded_uri != uri:
            return False
        ok = self._ipc_send(["playlist-next", "force"])
        if ok:
            rm_ok = self._ipc_send(["playlist-remove", "0"])
            self.current_uri = uri
            self._preloaded_uri = None
            logging.info("[%s] switch_preloaded OK: %s (remove=%s)",
                         self.monitor_id, Path(uri).name, rm_ok)
        else:
            logging.error("[%s] switch_preloaded FEHLGESCHLAGEN: playlist-next",
                          self.monitor_id)
        return ok

    def jump_to(self, index, uri=None):
        """Bild wechseln — preloaded (instant) oder loadfile replace (Fallback)."""
        if not uri:
            return False
        # Vorgeladen? -> playlist-next (sofort)
        if self.switch_preloaded(uri):
            return True
        # Fallback: loadfile replace
        if self._preloaded_uri:
            logging.debug("[%s] Preload-Miss: erwartet %s, geladen %s",
                          self.monitor_id, Path(uri).name,
                          Path(self._preloaded_uri).name if self._preloaded_uri else "nix")
        self._preloaded_uri = None
        return self.load_file(uri)

    def set_rotation(self, rotation):
        """Rotation live per IPC aendern."""
        if rotation != self.rotation:
            logging.info("[%s] Rotation: %d -> %d", self.monitor_id, self.rotation, rotation)
            self._ipc_send(["set_property", "video-rotate", rotation])
            self.rotation = rotation

    def is_alive(self):
        """Prueft ob mpv-Prozess noch laeuft."""
        return self.process and self.process.poll() is None

    def stop(self):
        """mpv-Prozess beenden."""
        if self.process and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
        try:
            os.unlink(self.sock_path)
        except OSError:
            pass
