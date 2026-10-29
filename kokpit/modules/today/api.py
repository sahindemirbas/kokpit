"""Today: one screen assembled from the other modules' views. Owns no data."""
from __future__ import annotations

from datetime import datetime

from ...config import Config
from ..brain import api as brain_api
from ..events import api as events_api
from ..health import api as health_api
from . import calendar, weather


def view(cfg: Config, now: datetime | None = None) -> dict:
    now = now or datetime.now()
    out = {"now": now.isoformat(timespec="minutes"), "day": now.strftime("%A, %d %B %Y"), "sections": {}}
    if cfg.modules.get("brain", True):
        b = brain_api.view(cfg)
        out["sections"]["brain"] = {
            "threads": len(b["threads"]),
            "on_you": b["lanes"]["on_you"][:6],
            "upcoming": b["upcoming"][:6],
            "open_tasks": len(b["todo"]),
            "overdue": sum(1 for t in b["todo"] if t["overdue"]),
            "last_session": b["sessions"][0] if b["sessions"] else None,
            "inbox": len(b["inbox"]),
            "vault_exists": b["exists"],
        }
    if cfg.modules.get("health", True):
        h = health_api.view(cfg)
        out["sections"]["health"] = {"available": h["available"], "warnings": h["warnings"],
                                     "failing": h.get("failing", []),
                                     "jobs": len(h["runs"]), "reason": h.get("reason", "")}
    if cfg.modules.get("events", False):
        e = events_api.view(cfg, days=3)
        out["sections"]["events"] = {"configured": e.get("configured", False), "next": e["events"][:5],
                                     "upcoming": e["counts"]["upcoming"], "reason": e.get("reason", "")}
    out["sections"]["weather"] = _weather(cfg)
    out["sections"]["calendar"] = _calendar(cfg, now)
    return out


def _calendar(cfg: Config, now: datetime) -> dict:
    """Cached calendar rows. Never fetches, never returns an ICS address (they are secrets)."""
    if not cfg.calendar_ics_urls:
        return {"status": "not connected", "note": "set [today] calendar_ics_urls (ICS); Google read-only is planned, see docs/connections.md"}
    base = {"feeds": len(cfg.calendar_ics_urls)}
    if not cfg.db.exists():
        return {**base, "status": "not fetched", "note": "run `kokpit run-job calendar`"}
    from ...db import connect
    conn = connect(cfg.db, readonly=True)
    try:
        c = calendar.latest(conn, now)
    finally:
        conn.close()
    if not c:
        return {**base, "status": "not fetched", "note": "run `kokpit run-job calendar`"}
    today = now.date().isoformat()
    rows = [e for e in c.get("events", []) if e["date"] >= today]
    days: dict[str, list] = {}
    for e in rows:
        days.setdefault(e["date"], []).append({k: e[k] for k in ("start", "end", "all_day", "title", "location")})
    out = {**base, "status": "connected", "today": days.pop(today, []),
           "upcoming": [{"date": d, "events": evs} for d, evs in sorted(days.items())],
           "fetched": c.get("fetched"), "age_hours": c.get("age_hours"), "stale": c.get("stale", False),
           "unsupported_rrule": c.get("unsupported_rrule", 0)}
    if not c["last_ok"] or c["errors"]:
        out["error"] = "; ".join(c["errors"])[:300] or "last fetch failed"
        if "fetched" not in c:
            out["status"] = "error"
    return out


def _weather(cfg: Config) -> dict:
    if not cfg.has_place:
        return {"status": "needs a place", "note": "run `kokpit init --city ...`"}
    if not cfg.db.exists():
        return {"status": "not fetched", "note": "run `kokpit run-job weather`"}
    from ...db import connect
    conn = connect(cfg.db, readonly=True)
    try:
        w = weather.latest(conn)
    finally:
        conn.close()
    if not w:
        return {"status": "not fetched", "note": "run `kokpit run-job weather`"}
    w["status"] = "stale" if w["stale"] else "ok"
    w["city"] = cfg.place.get("city")
    return w
