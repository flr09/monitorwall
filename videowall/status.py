"""System-Status: Viewer-Prozesse, Temperatur, Netzwerk, Hardware."""

import socket
import subprocess


def _run(cmd, timeout=3):
    """Hilfsfunktion: Kommando ausfuehren, stdout zurueckgeben."""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.stdout.strip()
    except Exception:
        return ""


def _read_file(path):
    """Datei lesen, leerer String bei Fehler."""
    try:
        with open(path) as f:
            return f.read().strip()
    except Exception:
        return ""


def _get_ip():
    """Primaere IP-Adresse ermitteln."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return ""


def _get_mac(interface="wlan0"):
    """MAC-Adresse eines Interfaces lesen."""
    return _read_file(f"/sys/class/net/{interface}/address")


def _get_throttle():
    """Throttle-Status als Hex-String."""
    out = _run(["vcgencmd", "get_throttled"])
    if "=" in out:
        return out.split("=", 1)[1]
    return out


def _get_uptime():
    """Uptime als lesbarer String."""
    raw = _read_file("/proc/uptime")
    if not raw:
        return ""
    secs = int(float(raw.split()[0]))
    days = secs // 86400
    hours = (secs % 86400) // 3600
    mins = (secs % 3600) // 60
    if days:
        return f"{days}d {hours}h {mins}m"
    if hours:
        return f"{hours}h {mins}m"
    return f"{mins}m"


def _get_disk_usage():
    """Festplattenbelegung der Root-Partition."""
    import shutil
    try:
        total, used, free = shutil.disk_usage("/")
        total_gb = total / (1024 ** 3)
        used_gb = used / (1024 ** 3)
        pct = int(used / total * 100)
        return f"{used_gb:.1f}/{total_gb:.1f} GB ({pct}%)"
    except Exception:
        return ""


def _get_memory():
    """RAM-Nutzung."""
    raw = _read_file("/proc/meminfo")
    if not raw:
        return ""
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
    return ""


def _viewer_running():
    """Prueft ob der videowall-player Prozess laeuft."""
    out = _run(["pgrep", "-f", "videowall-player"])
    return bool(out)


def get_status():
    """Aktuellen System-Status abfragen."""
    hostname = socket.gethostname()
    viewer = _viewer_running()
    return {
        "hostname": hostname,
        "ip": _get_ip(),
        "mac_wlan": _get_mac("wlan0"),
        "mac_eth": _get_mac("eth0"),
        "temperature": _run(["vcgencmd", "measure_temp"]),
        "throttle": _get_throttle(),
        "uptime": _get_uptime(),
        "disk": _get_disk_usage(),
        "memory": _get_memory(),
        "viewer1_running": viewer,
        "viewer2_running": viewer,
    }
