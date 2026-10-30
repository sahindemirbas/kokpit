"""Today: one screen assembled from the other modules' views. Owns no data."""
from __future__ import annotations

from datetime import datetime

from ...config import Config
from ..ai_usage import api as ai_api
from ..brain import api as brain_api
from ..briefing import api as briefing_api
from ..career import api as career_api
from ..events import api as events_api
from ..health import api as health_api
from ..organiser import api as organiser_api
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
            "tasks": b["todo"][:6],
            "sessions": b["sessions"][:6],
            "vault_exists": b["exists"],
            "freshness": health_api.freshness(cfg, "brain-check"),
        }
    if cfg.modules.get("health", True):
        h = health_api.view(cfg)
        out["sections"]["health"] = {"available": h["available"], "warnings": h["warnings"],
                                     "failing": h.get("failing", []),
                                     "jobs": len(h["runs"]), "reason": h.get("reason", "")}
    if cfg.modules.get("events", False):
        e = events_api.view(cfg, days=3)
        out["sections"]["events"] = {"configured": e.get("configured", False), "next": e["events"][:5],
                                     "upcoming": e["counts"]["upcoming"], "reason": e.get("reason", ""),
                                     "freshness": e.get("freshness")}
    for name, fn in (("career", _career), ("briefing", _briefing), ("ai_usage", _ai_usage), ("organiser", _organiser)):
        part = fn(cfg)
        if part is not None:
            out["sections"][name] = part
    out["sections"]["weather"] = {**_weather(cfg), "freshness": health_api.freshness(cfg, "weather")}
    out["sections"]["calendar"] = {**_calendar(cfg, now), "freshness": health_api.freshness(cfg, "calendar")}
    return out


def _career(cfg: Config) -> dict | None:
    """Open listing counts and the best scored open listings. None when the jobs module is off."""
    if not cfg.modules.get("jobs", False):
        return None
    d = career_api._listings(cfg, 4)
    keep = ("title", "company", "location", "score", "url")
    return {"counts": d["counts"], "top": [{k: x.get(k) for k in keep} for x in d["listings"]],
            "reason": d.get("reason", ""), "freshness": health_api.freshness(cfg, "career")}


def _briefing(cfg: Config) -> dict | None:
    """Top briefing items plus the audio link. None when the briefing module is off."""
    if not cfg.modules.get("briefing", False):
        return None
    d = briefing_api.view(cfg)
    items = [it for s in d.get("sections", []) for it in s.get("items", [])]
    items.sort(key=lambda it: -(it.get("score") or 0))
    keep = ("title", "headline", "topic", "project", "score", "url", "by_model")
    return {"count": d.get("count", len(items)), "day": d.get("day"), "audio": d.get("audio") or "",
            "top": [{k: it.get(k) for k in keep} for it in items[:4]],
            "reason": d.get("reason", ""), "freshness": d.get("freshness")}


def _ai_usage(cfg: Config) -> dict | None:
    """Measured AI usage for today and the last 7 days. None when the module is off."""
    if not cfg.modules.get("ai_usage", False):
        return None
    try:
        w = ai_api.view(cfg).get("windows", {})
    except Exception as exc:  # a broken reader must not take the whole Today page down
        return {"today": None, "7d": None, "reason": f"ai usage unavailable: {type(exc).__name__}"}
    keep = ("sessions", "tokens", "estimate_usd", "estimate_state")
    out: dict = {k: ({kk: w[k].get(kk) for kk in keep} if k in w else None) for k in ("today", "7d")}
    out["reason"] = ""
    return out


def _organiser(cfg: Config) -> dict | None:
    """Organiser counts and the deterministic morning text. None when the module is off."""
    if not cfg.modules.get("organiser", False):
        return None
    d = organiser_api.view(cfg)
    return {"counts": d.get("counts", {}), "total": d.get("total", 0), "text": organiser_api.morning(cfg),
            "reason": d.get("reason", "")}


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
