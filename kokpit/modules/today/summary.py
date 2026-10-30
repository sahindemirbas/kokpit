"""Header summary card: date, today's weather, next calendar event, task and on-you counts.

Read-only. Assembled from the Today and Brain views; a disabled module yields null fields
(never a fake 0). Answers are cached in memory for one minute per config file.
"""
from __future__ import annotations

import threading
import time
from datetime import date, datetime, timedelta

from ...config import Config
from ..brain import api as brain_api
from . import api as today_api

TTL = 60.0
EVENT_DAYS = 14
RAIN_MIN = 30

_lock = threading.Lock()
_cache: dict[str, tuple[float, dict]] = {}


def clear_cache() -> None:
    with _lock:
        _cache.clear()


def view(cfg: Config, now: datetime | None = None, clock=time.monotonic) -> dict:
    key = str(cfg.path)
    t = clock()
    with _lock:
        hit = _cache.get(key)
        if hit and t - hit[0] < TTL:
            return hit[1]
    out = build(cfg, now)
    with _lock:
        _cache[key] = (t, out)
    return out


def build(cfg: Config, now: datetime | None = None) -> dict:
    now = now or datetime.now()
    today_on = cfg.modules.get("today", True)
    brain_on = cfg.modules.get("brain", True)
    out = {"date": now.date().isoformat(), "weather": None, "event": None, "tasks": None, "on_you": None}
    if brain_on:
        b = brain_api.view(cfg)
        if b.get("exists"):
            out["on_you"] = len(b["lanes"]["on_you"])
            if today_on:
                out["tasks"] = len(b["todo"])
    if today_on:
        out["weather"] = _weather(cfg, now.date())
        out["event"] = _event(cfg, now)
    return out


def _weather(cfg: Config, day: date) -> dict | None:
    w = today_api._weather(cfg)
    if w.get("status") not in ("ok", "stale"):
        return None
    iso = day.isoformat()
    d = next((x for x in w.get("days") or [] if x.get("date") == iso), None)
    if not d or (d.get("max") is None and d.get("min") is None):
        return None
    pct = d.get("rain_pct")
    return {"city": w.get("city") or None, "max": d.get("max"), "min": d.get("min"), "text": d.get("text"),
            "rain_pct": pct if isinstance(pct, (int, float)) and pct >= RAIN_MIN else None,
            "stale": w.get("status") == "stale"}


def _event(cfg: Config, now: datetime) -> dict | None:
    c = today_api._calendar(cfg, now)
    if c.get("status") != "connected":
        return None
    hm = now.strftime("%H:%M")
    for e in c.get("today") or []:
        if e.get("all_day") or (e.get("end") or e.get("start") or "") >= hm or e.get("start") == "…":
            return _ev(now.date().isoformat(), e)
    last = (now.date() + timedelta(days=EVENT_DAYS)).isoformat()
    for d in c.get("upcoming") or []:
        if d["date"] > last:
            break
        if d.get("events"):
            return _ev(d["date"], d["events"][0])
    return None


def _ev(day: str, e: dict) -> dict:
    return {"date": day, "start": None if e.get("all_day") else e.get("start"), "all_day": bool(e.get("all_day")),
            "title": e.get("title") or ""}
