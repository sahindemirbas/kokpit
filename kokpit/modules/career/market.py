"""Market view over stored listings: per company, weekly trend, top requested skills, honest labels.

A listing is dated by ``posted_at`` when the source gave one, else by ``first_seen``. Undated listings
first seen on a source's first run day are the bulk load of whatever was already open, not new
postings, so they are left out of the trend.
"""
from __future__ import annotations

import csv
import io
import json
import re
import sqlite3
from collections import Counter
from datetime import date, datetime, timedelta

from . import cv, store

SHORT_WINDOW_DAYS = 28
CSV_COLUMNS = ["score", "status", "title", "company", "location", "remote", "source", "posted_at", "first_seen", "last_seen", "url", "reasons"]


def _day(s: str | None) -> date | None:
    try:
        return datetime.fromisoformat(s[:10]).date() if s else None
    except ValueError:
        return None


def _week(d: date) -> str:
    return (d - timedelta(days=d.weekday())).isoformat()


def _skill_re(term: str) -> re.Pattern:
    return re.compile(r"(?<![\w+#&])" + re.escape(term) + r"(?![\w+#&])", re.I)


def view(conn: sqlite3.Connection, top: int = 15) -> dict:
    store.ensure_schema(conn)
    if not store.has_table(conn):
        return {"total": 0, "open": 0, "companies": [], "weekly": [], "skills": [], "window_days": 0, "notes": ["no listings yet"]}
    rows = conn.execute("SELECT source, company, title, description, status, posted_at, first_seen FROM job_listings").fetchall()
    if not rows:
        return {"total": 0, "open": 0, "companies": [], "weekly": [], "skills": [], "window_days": 0, "notes": ["no listings yet"]}
    first_day = {}
    for r in rows:
        d = _day(r["first_seen"])
        if d and (r["source"] not in first_day or d < first_day[r["source"]]):
            first_day[r["source"]] = d
    open_rows = [r for r in rows if r["status"] == "open"]
    companies = Counter((r["company"] or "company unknown") for r in open_rows)
    weekly: Counter = Counter()
    skipped = 0
    dated = []
    for r in rows:
        d = _day(r["posted_at"])
        if d is None:
            d = _day(r["first_seen"])
            if d is not None and d == first_day.get(r["source"]):
                skipped += 1
                continue
        if d is not None:
            weekly[_week(d)] += 1
            dated.append(d)
    skills: Counter = Counter()
    pats = [(t, _skill_re(t)) for t in cv.SKILL_TERMS]
    described = 0
    for r in open_rows:
        text = f"{r['title']} {r['description'] or ''}"
        if r["description"]:
            described += 1
        for t, p in pats:
            if p.search(text):
                skills[t] += 1
    first, last = (min(dated), max(dated)) if dated else (None, None)
    window = (last - first).days + 1 if first else 0
    notes = []
    if not dated:
        notes.append("no dated listings yet: the weekly trend needs at least one posting date or a second run day")
    elif window < SHORT_WINDOW_DAYS:
        notes.append(f"short window: only {window} day(s) of data, the weekly numbers show collection, not a market trend")
    if skipped:
        notes.append(f"{skipped} undated listing(s) from a source's first run day are left out of the trend (bulk load of already open jobs)")
    if len(open_rows) - described:
        notes.append(f"skills are counted from titles and descriptions; {len(open_rows) - described} open listing(s) have no description")
    return {"total": len(rows), "open": len(open_rows), "window_days": window,
            "companies": [{"company": c, "open": n} for c, n in companies.most_common(top)],
            "weekly": [{"week": w, "listings": n} for w, n in sorted(weekly.items())],
            "skills": [{"skill": s, "open": n} for s, n in skills.most_common(top)], "notes": notes}


def _cell(v) -> str:
    s = "" if v is None else str(v)
    return "'" + s if s[:1] in ("=", "+", "-", "@", "\t", "\r") else s


def export_csv(conn: sqlite3.Connection, status: str | None = None) -> str:
    """All listings (or one status) with score and reasons; an unknown score is the word unknown."""
    out = io.StringIO()
    w = csv.writer(out, lineterminator="\n")
    w.writerow(CSV_COLUMNS)
    for r in store.listings(conn, status, limit=100000):
        reasons = " | ".join(f"{x.get('effect', '')} {x.get('kind', '')}: {x.get('text', '')}" for x in (r["reasons"].get("reasons") or []))
        row = {**r, "score": "unknown" if r["score"] is None else r["score"], "source": "+".join(r["sources"]), "reasons": reasons}
        w.writerow([_cell(row.get(c)) for c in CSV_COLUMNS])
    return out.getvalue()
