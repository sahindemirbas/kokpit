"""Events table: upsert by (source, source_id); the web app only reads."""
from __future__ import annotations

import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path

SCHEMA = (Path(__file__).parent / "schema.sql").read_text(encoding="utf-8")
FIELDS = ("source", "source_id", "title", "description", "start", "end", "venue", "address", "city", "lat", "lon",
          "url", "image", "price", "category", "distance_km", "language")


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)


def upsert(conn: sqlite3.Connection, records: list[dict], now: str | None = None) -> int:
    ensure_schema(conn)
    now = now or datetime.now().isoformat(timespec="seconds")
    cols = ", ".join(FIELDS)
    marks = ", ".join("?" for _ in FIELDS)
    updates = ", ".join(f"{f}=excluded.{f}" for f in FIELDS if f not in ("source", "source_id"))
    n = 0
    for r in records:
        if not r.get("title") or not r.get("start"):
            continue
        conn.execute(
            f"INSERT INTO events({cols}, first_seen, last_seen) VALUES ({marks}, ?, ?) "
            f"ON CONFLICT(source, source_id) DO UPDATE SET {updates}, last_seen=excluded.last_seen",
            tuple(r.get(f) for f in FIELDS) + (now, now),
        )
        n += 1
    conn.commit()
    return n


def upcoming(conn: sqlite3.Connection, days: int = 14, today: str | None = None, limit: int = 200) -> list[dict]:
    """Events whose start (or end, for multi-day ones) falls in [today, today+days]."""
    ensure_schema(conn)
    t0 = today or date.today().isoformat()
    t1 = (date.fromisoformat(t0) + timedelta(days=days)).isoformat()
    rows = conn.execute(
        "SELECT * FROM events WHERE substr(COALESCE(end, start),1,10) >= ? AND substr(start,1,10) <= ? "
        "ORDER BY substr(start,1,10), COALESCE(distance_km, 9999), title LIMIT ?",
        (t0, t1, limit),
    ).fetchall()
    return [dict(r) for r in rows]


def counts(conn: sqlite3.Connection, today: str | None = None) -> dict:
    ensure_schema(conn)
    t0 = today or date.today().isoformat()
    total = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    future = conn.execute("SELECT COUNT(*) FROM events WHERE substr(COALESCE(end, start),1,10) >= ?", (t0,)).fetchone()[0]
    by_source = {r[0]: r[1] for r in conn.execute(
        "SELECT source, COUNT(*) FROM events WHERE substr(COALESCE(end, start),1,10) >= ? GROUP BY source", (t0,))}
    return {"total": total, "upcoming": future, "by_source": by_source}


def ics(ev: dict) -> str:
    """One VEVENT; all-day when the source gave only a date."""
    def esc(s):
        return str(s or "").replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")
    start, end = ev["start"], ev.get("end")
    uid = f"{ev['source']}-{ev['id']}@kokpit"
    if len(start) == 10:
        d0 = date.fromisoformat(start)
        d1 = date.fromisoformat(end[:10]) if end else d0
        dt = f"DTSTART;VALUE=DATE:{d0.strftime('%Y%m%d')}\nDTEND;VALUE=DATE:{(d1 + timedelta(days=1)).strftime('%Y%m%d')}"
    else:
        def fmt(s):
            return datetime.fromisoformat(s.replace("Z", "+00:00")).strftime("%Y%m%dT%H%M%S")
        dt = f"DTSTART:{fmt(start)}"
        if end and len(end) > 10:
            dt += f"\nDTEND:{fmt(end)}"
    loc = ", ".join(x for x in (ev.get("venue"), ev.get("address"), ev.get("city")) if x)
    return "\n".join([
        "BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Kokpit//events//EN", "BEGIN:VEVENT", f"UID:{uid}",
        f"SUMMARY:{esc(ev['title'])}", dt, f"LOCATION:{esc(loc)}", f"URL:{ev.get('url') or ''}",
        f"DESCRIPTION:{esc((ev.get('description') or '')[:500])}", "END:VEVENT", "END:VCALENDAR", "",
    ])
