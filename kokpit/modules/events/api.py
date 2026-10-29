"""Events module: collect job + read API.

Collect: generic sources (allevents, eventbrite) chosen by the slugs in
``[events]``, filtered to ``radius_km`` around ``[place]``, categorised by the
configured language packs, upserted into the ``events`` table. Each source that
fails is a warning, not a crash; a source that returns 0 records is reported
as such (the silent-empty trap from docs/modules.md).
"""
from __future__ import annotations

import sqlite3

from ...config import Config
from ..health import api as health_api
from . import categories, sources, store


def collect(cfg: Config, conn: sqlite3.Connection, fetchers: dict | None = None) -> tuple[int, dict, list[str]]:
    """Returns (rows, info, warnings) in the job contract."""
    if not cfg.has_place:
        return 0, {}, ["no place configured: run `kokpit init --city \"Your city\"`"]
    fetchers = fetchers or sources.SOURCES
    place, ev = cfg.place, cfg.events
    lang = place["languages"][0]
    per_source: dict[str, int] = {}
    warnings: list[str] = []
    records: list[dict] = []
    plan = []
    if ev.get("allevents_slug"):
        plan.append(("allevents", lambda: fetchers["allevents"](ev["allevents_slug"], lang)))
    if ev.get("eventbrite_slug"):
        plan.append(("eventbrite", lambda: fetchers["eventbrite"](ev["eventbrite_slug"], place.get("country"), lang)))
    if not plan:
        warnings.append("no source enabled: set allevents_slug or eventbrite_slug in kokpit.toml")
    for name, fn in plan:
        try:
            got = fn()
        except Exception as exc:  # one broken source must not hide the others
            warnings.append(f"{name}: {type(exc).__name__}: {str(exc)[:120]}")
            per_source[name] = 0
            continue
        kept = sources.within(got, place["lat"], place["lon"], place["radius_km"])
        per_source[name] = len(kept)
        if not kept:
            warnings.append(f"{name}: 0 records (check the slug on the Connections screen)")
        records.extend(kept)
    for r in records:
        r["category"] = categories.classify(r, place["languages"])
    n = store.upsert(conn, records)
    return n, {"sources": per_source, "radius_km": place["radius_km"]}, warnings


def payload(cfg: Config, conn: sqlite3.Connection, days: int = 14) -> dict:
    """What the Events tab shows. Never fetches; reads the table filled by the job."""
    if not cfg.has_place:
        return {"configured": False, "place": None, "events": [], "counts": {"total": 0, "upcoming": 0, "by_source": {}}}
    evs = store.upcoming(conn, days=days, limit=2000)
    return {
        "configured": True,
        "place": {k: cfg.place.get(k) for k in ("city", "country", "radius_km")},
        "days": days,
        "events": evs,
        "counts": store.counts(conn),
        "categories": categories.CATEGORIES,
    }


def _open(cfg: Config):
    from ...db import connect
    return connect(cfg.db, readonly=True)


def view(cfg: Config, days: int = 14) -> dict:
    """Events payload plus the ``freshness`` of the events-collect job."""
    return {**_view(cfg, days), "freshness": health_api.freshness(cfg, "events-collect")}


def _view(cfg: Config, days: int = 14) -> dict:
    if not cfg.modules.get("events", False):
        return {"enabled": False, "configured": cfg.has_place, "events": [], "counts": {"total": 0, "upcoming": 0, "by_source": {}}}
    try:
        conn = _open(cfg)
    except FileNotFoundError:
        return {"enabled": True, "configured": cfg.has_place, "events": [], "counts": {"total": 0, "upcoming": 0, "by_source": {}},
                "reason": "no database yet: run `kokpit run-job events-collect`"}
    try:
        out = payload(cfg, conn, days=days)
    finally:
        conn.close()
    out["enabled"] = True
    return out


def one(cfg: Config, event_id: int) -> dict | None:
    try:
        conn = _open(cfg)
    except FileNotFoundError:
        return None
    try:
        row = conn.execute("SELECT * FROM events WHERE id=?", (event_id,)).fetchone()
    except sqlite3.OperationalError:
        return None
    finally:
        conn.close()
    return dict(row) if row else None
