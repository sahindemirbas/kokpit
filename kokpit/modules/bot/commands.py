"""Commands and the morning line. Read-only, deterministic, built from data Kokpit already has."""
from __future__ import annotations

import re
from datetime import datetime, timezone

from ...config import Config

HELP = ("Commands:\n/today  the morning line now\n/health  current warnings\n/plan  next Instagram slots\n/help  this list")
UNKNOWN = "Unknown command. Send /help for the list."


def morning_line(cfg: Config, now: datetime | None = None) -> str:
    """One line from the Today pieces that exist; Health warnings only when something is wrong."""
    from ..today import api as today_api
    now = now or datetime.now()
    v = today_api.view(cfg, now)
    s = v["sections"]
    parts = [f"Good morning. {now.strftime('%A %d %B')}."]
    w = s.get("weather") or {}
    day = next((d for d in w.get("days", []) if d.get("date") == now.date().isoformat()), None)
    if w.get("status") in ("ok", "stale") and day:
        txt = f"Weather: {day.get('text', 'n/a')}"
        if day.get("min") is not None and day.get("max") is not None:
            txt += f", {round(day['min'])} to {round(day['max'])} C"
        if day.get("rain_pct"):
            txt += f", rain {day['rain_pct']}%"
        parts.append(txt + ".")
    c = s.get("calendar") or {}
    if c.get("status") == "connected":
        evs = [e for e in c.get("today", [])]
        timed = [m.group(0) for e in evs if not e.get("all_day") and (m := re.search(r"\d{2}:\d{2}", str(e.get("start") or "")))]
        if evs:
            parts.append(f"Calendar: {len(evs)} event(s) today" + (f", first at {min(timed)}" if timed else "") + ".")
        else:
            parts.append("Calendar: nothing today.")
    b = s.get("brain") or {}
    if b.get("vault_exists") and b.get("overdue"):
        parts.append(f"Tasks: {b['overdue']} overdue.")
    ev = s.get("events") or {}
    if ev.get("configured") and ev.get("upcoming"):
        parts.append(f"Events: {ev['upcoming']} coming up.")
    ws = health_warnings(cfg)
    if ws:
        parts.append(f"Warnings: {len(ws)}. First: {ws[0]}")
    from ..organiser import api as organiser_api
    org = organiser_api.morning(cfg)
    if org and not org.endswith("no open proposals."):
        parts.append(org.splitlines()[0])
    return " ".join(parts)


def health_warnings(cfg: Config) -> list[str]:
    from ..health import api as health_api
    h = health_api.view(cfg)
    return list(h.get("warnings") or []) + [f"{f['job']}: failing since {f['since']}" for f in h.get("failing") or []]


def health_text(cfg: Config) -> str:
    ws = health_warnings(cfg)
    if not ws:
        return "All good."
    return f"{len(ws)} warning(s):\n" + "\n".join(f"- {w}" for w in ws[:10]) + (f"\n... and {len(ws) - 10} more" if len(ws) > 10 else "")


def plan_text(cfg: Config, now: datetime | None = None) -> str:
    if not cfg.modules.get("instagram", False):
        return "Instagram module is off."
    from ..instagram import planapi
    now = now or datetime.now(timezone.utc)
    stamp = now.strftime("%Y-%m-%dT%H:%M:%S")
    slots = [x for x in planapi.view(cfg, now)["slots"] if x["status"] in ("planned", "ready", "approved") and x["due_at"] >= stamp]
    slots.sort(key=lambda x: x["due_at"])
    if not slots:
        return "Nothing planned."
    return "Next slots (UTC):\n" + "\n".join(f"- {x['due_at'].replace('T', ' ')[:16]} {x['kind']} ({x['status']})" for x in slots[:5])


def command(text: str) -> str:
    cmd = (text or "").strip().split(maxsplit=1)[0].lower() if (text or "").strip() else ""
    return cmd.split("@", 1)[0]


def reply(cfg: Config, text: str) -> str:
    cmd = command(text)
    if cmd in ("/help", "/start"):
        return HELP
    if cmd == "/today":
        return morning_line(cfg)
    if cmd == "/health":
        return health_text(cfg)
    if cmd == "/plan":
        return plan_text(cfg)
    return UNKNOWN
