"""Scheduled jobs. Every job records its outcome in the health table.

``kokpit run-job <name>`` runs one. The web app never runs jobs.
"""
from __future__ import annotations

import traceback
from collections.abc import Callable

from .config import Config
from .db import connect
from .modules.brain import reader
from .modules.ai_usage import collect as ai_usage_collect
from .modules.career import collect as career_collect
from .modules.events import api as events_api
from .modules.instagram import collect as instagram_collect
from .modules.health import store
from .modules.today import calendar, weather

Job = Callable[[Config], tuple[int, dict, list[str]]]


def heartbeat(cfg: Config):
    """Proves the pipeline: writes one row so the health table is never empty."""
    return 1, {"note": "heartbeat"}, []


def brain_check(cfg: Config):
    """Reads the vault and warns about missing files or files over their size limit."""
    data = reader.read_vault(cfg.vault)
    warnings = []
    if not data["exists"]:
        return 0, {"vault": str(cfg.vault)}, [f"vault folder missing: {cfg.vault}"]
    for f in data["files"]:
        if not f["exists"]:
            warnings.append(f"{f['file']} missing")
        elif f["over"]:
            warnings.append(f"{f['file']} is {f['chars']} chars, limit {f['limit']}")
    overdue = [t for t in data["todo"] if t["overdue"]]
    if overdue:
        warnings.append(f"{len(overdue)} overdue task(s)")
    rows = len(data["threads"]) + len(data["todo"])
    return rows, {"threads": len(data["threads"]), "tasks": len(data["todo"]), "inbox": len(data["inbox"])}, warnings


def events_collect(cfg: Config):
    """Fetches the enabled event sources for the configured place and upserts the events table."""
    conn = connect(cfg.db)
    try:
        return events_api.collect(cfg, conn)
    finally:
        conn.close()


def career(cfg: Config):
    """Collects job listings from the enabled sources (adapters, optional LinkedIn guest) and scores them."""
    conn = connect(cfg.db)
    try:
        return career_collect.collect(cfg, conn)
    finally:
        conn.close()


def ai_usage(cfg: Config):
    """Reads the enabled local AI usage logs (Hermes, Claude Code, Codex) into the usage table; read-only."""
    conn = connect(cfg.db)
    try:
        return ai_usage_collect.collect(cfg, conn)
    finally:
        conn.close()


def instagram(cfg: Config):
    """Reads Instagram account, media and story insights through the official API (GET only); run twice a day."""
    conn = connect(cfg.db)
    try:
        return instagram_collect.collect(cfg, conn)
    finally:
        conn.close()


def weather_job(cfg: Config):
    """Fetches a 3-day Open-Meteo forecast for the configured place (no key) and caches it."""
    if not cfg.has_place:
        return 0, {}, ["no place configured"]
    data = weather.fetch_forecast(cfg.place["lat"], cfg.place["lon"])
    conn = connect(cfg.db)
    try:
        weather.save(conn, cfg.place["lat"], cfg.place["lon"], data)
    finally:
        conn.close()
    return len(data["days"]), {"current": data["current"]}, []


class CalendarError(RuntimeError):
    """All calendar feeds failed. The message holds masked labels only, never an ICS address."""


def calendar_job(cfg: Config):
    """Fetches the read-only ICS feeds, expands today + 6 days and caches the result.

    The addresses are secrets: info, warnings and errors carry only ``calendar N (scheme://host/…)``.
    """
    urls = cfg.calendar_ics_urls
    if not urls:
        return 0, {"feeds": 0}, ["no calendar configured ([today] calendar_ics_urls is empty)"]
    data = calendar.collect(urls)
    ok = data["feeds_ok"] > 0
    conn = connect(cfg.db)
    try:
        calendar.save(conn, data, ok)
    finally:
        conn.close()
    if not ok:
        raise CalendarError("; ".join(data["errors"]) or "no feed could be read")
    info = {"feeds": data["feeds"], "feeds_ok": data["feeds_ok"], "days": data["days"],
            "unsupported_rrule": data["unsupported_rrule"]}
    return len(data["events"]), info, data["errors"] + data["warnings"]


REGISTRY: dict[str, Job] = {"weather": weather_job, "calendar": calendar_job, "heartbeat": heartbeat, "brain-check": brain_check, "events-collect": events_collect, "career": career, "ai_usage": ai_usage, "instagram": instagram}


def run(cfg: Config, name: str) -> bool:
    if name not in REGISTRY:
        raise SystemExit(f"unknown job '{name}'. Available: {', '.join(sorted(REGISTRY))}")
    started = store.now_iso()
    conn = connect(cfg.db)
    try:
        try:
            rows, info, warnings = REGISTRY[name](cfg)
            ok = True
        except Exception as exc:  # the failure itself is the record
            rows, info, warnings, ok = None, {"error": repr(exc), "trace": traceback.format_exc()[-800:]}, [], False
        store.record(conn, name, started, ok, rows=rows, info=info, warnings=warnings)
    finally:
        conn.close()
    status = "ok" if ok else "error"
    tail = f", {rows} rows" if rows is not None else ""
    print(f"{name}: {status}{tail}" + (f"; warnings: {'; '.join(warnings)}" if warnings else ""))
    return ok
