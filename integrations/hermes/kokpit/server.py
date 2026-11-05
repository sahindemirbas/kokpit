"""Optional server module: a read-only look at this machine.

Disk use for the configured paths, CPU temperature, load average, memory, uptime and whether the
configured systemd --user units are active. Anything the machine does not expose is "unknown".
The only command run is `systemctl --user is-active <name>`, with a list argv (no shell), a short
timeout and a unit name that passed NAME below. Nothing is started, stopped or changed.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9@_.:-]{0,127}$")
THERMAL = Path("/sys/class/thermal")
PROC = Path("/proc")
TIMEOUT = 3
MAX_PATHS = 10
MAX_UNITS = 20
DISK_WARN = 90.0   # percent used
TEMP_WARN = 80.0   # degrees C


def _gb(n: int) -> float:
    return round(n / 1024 ** 3, 1)


def disks(paths) -> list[dict]:
    out = []
    for raw in (paths or ["/"])[:MAX_PATHS]:
        p = os.path.expanduser(str(raw))
        try:
            u = shutil.disk_usage(p)
        except OSError as e:
            out.append({"path": str(raw), "status": f"unknown ({type(e).__name__})"})
            continue
        pct = round(100.0 * u.used / u.total, 1) if u.total else None
        out.append({"path": str(raw), "used_pct": pct, "free_gb": _gb(u.free), "total_gb": _gb(u.total),
                    "status": "warn" if pct is not None and pct >= DISK_WARN else "ok"})
    return out


def cpu_temp(root: Path = THERMAL):
    """Hottest thermal zone in degrees C, or "unknown"."""
    best = None
    try:
        zones = sorted(root.glob("thermal_zone*/temp"))
    except OSError:
        zones = []
    for z in zones:
        try:
            v = int(z.read_text().strip()) / 1000.0
        except (OSError, ValueError):
            continue
        if -40 < v < 150:
            best = v if best is None else max(best, v)
    return round(best, 1) if best is not None else "unknown"


def load_avg():
    try:
        return [round(x, 2) for x in os.getloadavg()]
    except (OSError, AttributeError):
        return "unknown"


def memory(root: Path = PROC):
    try:
        info = {}
        for line in (root / "meminfo").read_text().splitlines():
            k, _, v = line.partition(":")
            info[k] = int(v.split()[0]) * 1024
        total, avail = info["MemTotal"], info.get("MemAvailable", info.get("MemFree", 0))
    except (OSError, KeyError, ValueError, IndexError):
        return "unknown"
    return {"total_gb": _gb(total), "available_gb": _gb(avail),
            "used_pct": round(100.0 * (total - avail) / total, 1) if total else None}


def uptime(root: Path = PROC):
    try:
        secs = int(float((root / "uptime").read_text().split()[0]))
    except (OSError, ValueError, IndexError):
        return "unknown"
    d, rest = divmod(secs, 86400)
    return {"seconds": secs, "text": (f"{d}d " if d else "") + f"{rest // 3600}h {rest % 3600 // 60}m"}


def unit_state(name: str) -> str:
    """active / inactive / failed / ... from systemctl, "invalid name" or "unknown"."""
    if not isinstance(name, str) or not NAME.match(name):
        return "invalid name"
    exe = shutil.which("systemctl")
    if not exe:
        return "unknown"
    try:
        r = subprocess.run([exe, "--user", "is-active", "--", name], capture_output=True, text=True,
                           timeout=TIMEOUT, check=False, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    out = (r.stdout or "").strip().splitlines()
    return out[0][:40] if out else "unknown"


def status(paths, units) -> dict:
    units = [str(u) for u in (units or [])][:MAX_UNITS]
    body = {"disks": disks(paths), "cpu_temp_c": cpu_temp(), "load": load_avg(), "memory": memory(),
            "uptime": uptime(), "services": [{"name": u, "state": unit_state(u)} for u in units]}
    body["warnings"] = warnings(body)
    return body


def warnings(body: dict) -> list[str]:
    w = [f"disk {d['path']} {d['used_pct']}% full" for d in body.get("disks", []) if d.get("status") == "warn"]
    t = body.get("cpu_temp_c")
    if isinstance(t, (int, float)) and t >= TEMP_WARN:
        w.append(f"CPU at {t} C")
    m = body.get("memory")
    if isinstance(m, dict) and (m.get("used_pct") or 0) >= 95:
        w.append(f"memory {m['used_pct']}% used")
    w += [f"service {s['name']}: {s['state']}" for s in body.get("services", [])
          if s["state"] not in ("active", "unknown")]
    return w
