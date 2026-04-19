"""Datenbankzugriff auf die Videowall-SQLite-DB.

Eigene DB (displaywall.db), unabhaengig von Anthias.
Schlankes Schema fuer Asset-Metadaten. Playlists liegen in wall_config.json.
Nur auf dem Head-Pi relevant.
"""

import logging
import sqlite3
from videowall.config import DW_DB_PATH

_SCHEMA = """\
CREATE TABLE IF NOT EXISTS assets (
    asset_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    uri TEXT NOT NULL,
    mimetype TEXT NOT NULL,
    duration INTEGER DEFAULT 10,
    created_at TEXT DEFAULT (datetime('now')),
    file_size INTEGER DEFAULT 0
);
"""

_schema_done = False


def _get_conn(mode="rw"):
    """Verbindung oeffnen, Schema bei Bedarf anlegen."""
    global _schema_done
    if DW_DB_PATH is None:
        return None
    if mode == "ro":
        if not DW_DB_PATH.exists():
            return None
        conn = sqlite3.connect(f"file:{DW_DB_PATH}?mode=ro", uri=True)
    else:
        DW_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(DW_DB_PATH))
        if not _schema_done:
            conn.executescript(_SCHEMA)
            _schema_done = True
    conn.row_factory = sqlite3.Row
    return conn

def get_assets():
    """Alle Assets aus der DB lesen."""
    conn = _get_conn("ro")
    if not conn:
        return []
    try:
        rows = conn.execute(
            "SELECT asset_id, name, uri, mimetype, duration, created_at, file_size "
            "FROM assets ORDER BY name ASC"
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except sqlite3.Error as e:
        logging.error("get_assets Fehler: %s", e)
        return []


def get_asset(asset_id):
    """Einzelnes Asset lesen."""
    conn = _get_conn("ro")
    if not conn:
        return None
    try:
        row = conn.execute(
            "SELECT asset_id, name, uri, mimetype, duration, created_at, file_size "
            "FROM assets WHERE asset_id = ?",
            (asset_id,),
        ).fetchone()
        conn.close()
        return dict(row) if row else None
    except sqlite3.Error:
        return None


def add_asset(asset_id, name, uri, mimetype, duration=10, file_size=0):
    """Neues Asset in die DB eintragen."""
    try:
        conn = _get_conn()
        if not conn:
            return False
        conn.execute(
            "INSERT OR REPLACE INTO assets "
            "(asset_id, name, uri, mimetype, duration, file_size) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (asset_id, name, uri, mimetype, duration, file_size),
        )
        conn.commit()
        conn.close()
        return True
    except sqlite3.Error as e:
        logging.error("add_asset Fehler: %s", e)
        return False


def delete_asset(asset_id):
    """Asset aus DB loeschen (Datei muss separat entfernt werden)."""
    try:
        conn = _get_conn()
        if not conn:
            return False
        conn.execute("DELETE FROM assets WHERE asset_id = ?", (asset_id,))
        conn.commit()
        conn.close()
        return True
    except sqlite3.Error as e:
        logging.error("delete_asset Fehler: %s", e)
        return False


def update_asset(asset_id, **fields):
    """Asset-Felder aktualisieren (name, duration, etc.)."""
    allowed = {"name", "duration", "mimetype", "uri", "file_size"}
    updates = {k: v for k, v in fields.items() if k in allowed}
    if not updates:
        return False
    try:
        conn = _get_conn()
        if not conn:
            return False
        sets = ", ".join(f"{k} = ?" for k in updates)
        vals = list(updates.values()) + [asset_id]
        conn.execute(f"UPDATE assets SET {sets} WHERE asset_id = ?", vals)
        conn.commit()
        conn.close()
        return True
    except sqlite3.Error as e:
        logging.error("update_asset Fehler: %s", e)
        return False


def get_db_mtime():
    """Aenderungszeitpunkt der Datenbank (fuer Change-Detection)."""
    if DW_DB_PATH is None:
        return 0
    try:
        return DW_DB_PATH.stat().st_mtime
    except OSError:
        return 0


def init_db():
    """DB und Schema anlegen (idempotent)."""
    _get_conn()
