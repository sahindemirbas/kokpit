"""Connections: read-only view of what Kokpit reads, with what permission, and when it last worked.

Three facts per connection (docs/connections.md): what is read / what may be
written, which permission, last successful update with record count. Values
shown are names and counts only; paths are reduced to a file name, tokens never
appear. Calendar ICS addresses are secrets: only their host is shown.
"""
from __future__ import annotations

import json
from datetime import date

from ...config import Config
from ...db import connect


def _last_run(cfg: Config, job: str) -> dict | None:
    if not cfg.db.exists():
        return None
    conn = connect(cfg.db, readonly=True)
    try:
        try:
            r = conn.execute("SELECT * FROM runs WHERE job=? ORDER BY id DESC LIMIT 1", (job,)).fetchone()
        except Exception:
            return None
    finally:
        conn.close()
    if not r:
        return None
    return {"finished": r["finished"], "ok": bool(r["ok"]), "rows": r["rows"],
            "today": str(r["day"]) == date.today().isoformat(), "info": json.loads(r["info"] or "{}"),
            "warnings": json.loads(r["warnings"] or "[]")}


def view(cfg: Config) -> dict:
    items = []
    vault_ok = cfg.vault.exists()
    items.append({
        "id": "vault", "name": "Notes folder", "module": "brain", "kind": "local path",
        "enabled": cfg.modules.get("brain", True), "reads": "Markdown files", "writes": "append-only quick capture" if cfg.quick_capture else "nothing (quick capture off)",
        "permission": "read" + (" + append" if cfg.quick_capture else ""), "target": cfg.vault.name,
        "status": "ok" if vault_ok else "missing", "last": _last_run(cfg, "brain-check"),
    })
    items.append({
        "id": "db", "name": "Local database", "module": "health", "kind": "local file",
        "enabled": cfg.modules.get("health", True), "reads": "job runs, events", "writes": "jobs only",
        "permission": "read/write", "target": cfg.db.name, "status": "ok" if cfg.db.exists() else "not created yet",
        "last": _last_run(cfg, "heartbeat"),
    })
    place = cfg.place
    items.append({
        "id": "place", "name": "City / place", "module": "events", "kind": "setting",
        "enabled": cfg.has_place, "reads": "geocoded once (OpenStreetMap Nominatim) at init", "writes": "nothing",
        "permission": "none", "target": f"{place.get('city') or '-'} {place.get('country') or ''}".strip(),
        "detail": f"radius {place.get('radius_km')} km, languages {', '.join(place.get('languages') or [])}" if cfg.has_place else "run `kokpit init --city ...`",
        "status": "ok" if cfg.has_place else "not set", "last": None,
    })
    ev_run = _last_run(cfg, "events-collect")
    for src, key, url in (("allevents", "allevents_slug", "allevents.in"), ("eventbrite", "eventbrite_slug", "eventbrite.*")):
        slug = cfg.events.get(key) or ""
        per = (ev_run or {}).get("info", {}).get("sources", {}) if ev_run else {}
        items.append({
            "id": src, "name": src.capitalize(), "module": "events", "kind": "public page", "enabled": bool(slug) and cfg.modules.get("events", False),
            "reads": f"listings on {url}/{slug}" if slug else "nothing (no slug)", "writes": "nothing", "permission": "none (public page)",
            "target": slug or "-", "status": ("ok" if per.get(src) else "0 records") if (slug and ev_run and src in per) else ("not run yet" if slug else "off"),
            "last": {**ev_run, "rows": per.get(src)} if ev_run and src in per else ev_run,
        })
    adapters = cfg.events.get("adapters") or []
    items.append({
        "id": "adapters", "name": "Local event adapters", "module": "events", "kind": "per-city adapter",
        "enabled": bool(adapters), "reads": ", ".join(adapters) if adapters else "none enabled", "writes": "nothing",
        "permission": "none", "target": f"{len(adapters)} enabled", "status": "ok" if adapters else "off", "last": None,
    })
    items.append({
        "id": "weather", "name": "Weather", "module": "today", "kind": "public API (Open-Meteo)", "enabled": cfg.has_place,
        "reads": "forecast for the place", "writes": "nothing", "permission": "none (no key)", "target": place.get("city") or "-",
        "status": "ok" if cfg.has_place else "needs a place", "last": _last_run(cfg, "weather"),
    })
    from ..today.calendar import mask
    urls = cfg.calendar_ics_urls
    cal_last = _last_run(cfg, "calendar")
    items.append({
        "id": "calendar", "name": "Calendar (ICS)", "module": "today", "kind": "ICS URL (secret)", "enabled": bool(urls),
        "reads": "events today and the next 6 days", "writes": "nothing", "permission": "read-only",
        "target": ", ".join(sorted({mask(u) for u in urls})) if urls else "-",
        "status": ("ok" if cal_last and cal_last["ok"] else "error" if cal_last else "not run yet") if urls else "not set",
        "last": cal_last,
    })
    items.append({"id": "google-calendar", "name": "Google Calendar (read-only)", "module": "planned", "kind": "planned",
                  "enabled": False, "reads": "-", "writes": "-", "permission": "-", "target": "-", "status": "planned", "last": None})
    for name in ("CV file", "Job sources", "Instagram", "Telegram bot", "Model API"):
        items.append({"id": name.lower().replace(" ", "-"), "name": name, "module": "planned", "kind": "planned",
                      "enabled": False, "reads": "-", "writes": "-", "permission": "-", "target": "-", "status": "planned", "last": None})
    return {"config": cfg.path.name, "connections": items}
