"""Skills plan: skills that open listings ask for and the profile lacks, with the user's mark per skill.

Marks are ``learning``, ``known`` or ``skip`` and live in the ``career_skills`` table. ``known`` counts as a
skill the user has (next to the profile's ``skills`` list) everywhere the plan and the weekly note look; ``known``
and ``skip`` move a skill out of the open list into ``hidden``. Counts come from titles and descriptions of stored
listings, matched against the same skill terms the CV reader uses. No model, no network.
"""
from __future__ import annotations

import sqlite3
from collections import Counter
from datetime import date, datetime, timedelta

from . import cv, market, store

STATUSES = ("learning", "known", "skip")
HIDDEN = ("known", "skip")
MAX_NOTE = 500
MAX_ITEMS = 40
GOOD_SCORE = 55     # a listing at or above this score counts as a good fit
WINDOW_DAYS = 90
HALF_DAYS = 30      # trend: last 30 days against the 30 before


def has_table(conn: sqlite3.Connection, name: str = "career_skills") -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


def load(conn: sqlite3.Connection) -> dict[str, dict]:
    """-> {skill: {status, note, updated_at}}; empty when the table does not exist yet."""
    if not has_table(conn):
        return {}
    return {r["skill"]: {"status": r["status"], "note": r["note"], "updated_at": r["updated_at"]}
            for r in conn.execute("SELECT skill, status, note, updated_at FROM career_skills")}


def known(conn: sqlite3.Connection) -> set[str]:
    return {s for s, v in load(conn).items() if v["status"] == "known"}


def have(prof: dict, conn: sqlite3.Connection) -> set[str]:
    """Skills the user has: the profile's skills list plus skills marked known."""
    return {str(s).strip().lower() for s in prof.get("skills", []) if str(s).strip()} | known(conn)


def save(conn: sqlite3.Connection, skill: str, status: str | None = None, note: str | None = None, now: str | None = None) -> dict:
    """Set a mark and/or a note. ``status=""`` removes the mark. Raises ValueError with a short reason."""
    skill = " ".join(str(skill or "").split()).lower()[:80]
    if not skill:
        raise ValueError("skill is required")
    if status not in (None, "") and status not in STATUSES:
        raise ValueError(f"status must be one of {', '.join(STATUSES)} (or empty to clear)")
    store.ensure_schema(conn)
    if status == "":
        conn.execute("DELETE FROM career_skills WHERE skill=?", (skill,))
        conn.commit()
        return {"skill": skill, "deleted": True}
    cur = conn.execute("SELECT status, note FROM career_skills WHERE skill=?", (skill,)).fetchone()
    if status is None and cur is None:
        raise ValueError("choose a status first")
    new_status = status if status is not None else cur["status"]
    new_note = (str(note) if note is not None else (cur["note"] if cur else ""))[:MAX_NOTE]
    now = now or datetime.now().isoformat(timespec="seconds")
    conn.execute("INSERT INTO career_skills(skill, status, note, updated_at) VALUES (?, ?, ?, ?) "
                 "ON CONFLICT(skill) DO UPDATE SET status=excluded.status, note=excluded.note, updated_at=excluded.updated_at",
                 (skill, new_status, new_note, now))
    conn.commit()
    return {"skill": skill, "status": new_status, "note": new_note, "updated_at": now}


def _listing_day(r) -> date | None:
    return market._day(r["posted_at"]) or market._day(r["first_seen"])


def plan(conn: sqlite3.Connection, prof: dict, today: date | None = None) -> dict:
    """-> {items, hidden, summary, good_n, window_days}. Items: skills asked for and not had, marked or not."""
    today = today or date.today()
    saved = load(conn)
    summary = {s: 0 for s in STATUSES}
    for v in saved.values():
        if v["status"] in summary:
            summary[v["status"]] += 1
    out = {"items": [], "hidden": [], "summary": {**summary, "open": 0}, "good_n": 0, "window_days": WINDOW_DAYS,
           "good_score": GOOD_SCORE, "statuses": list(STATUSES)}
    if not store.has_table(conn):
        return out
    since = today - timedelta(days=WINDOW_DAYS - 1)
    half = today - timedelta(days=HALF_DAYS - 1)
    prev = half - timedelta(days=HALF_DAYS)
    rows = [r for r in conn.execute("SELECT id, title, company, description, score, posted_at, first_seen FROM job_listings")
            if (d := _listing_day(r)) and since <= d <= today]
    owned = {s.lower() for s in prof.get("skills", [])}
    pats = [(t, market._skill_re(t)) for t in cv.SKILL_TERMS]
    n_all: Counter = Counter()
    n_good: Counter = Counter()
    recent: Counter = Counter()
    before: Counter = Counter()
    examples: dict[str, list] = {}
    good_rows = 0
    for r in sorted(rows, key=lambda r: -(r["score"] or 0)):
        text = f"{r['title']} {r['description'] or ''}"
        good = (r["score"] or 0) >= GOOD_SCORE
        good_rows += good
        d = _listing_day(r) or today
        for t, p in pats:
            if not p.search(text):
                continue
            n_all[t] += 1
            n_good[t] += good
            if d >= half:
                recent[t] += 1
            elif d >= prev:
                before[t] += 1
            if len(examples.setdefault(t, [])) < 3:
                examples[t].append({"id": r["id"], "company": r["company"], "title": r["title"]})
    out["good_n"] = good_rows
    candidates = (set(n_all) - owned) | set(saved)
    for skill in candidates:
        mark = saved.get(skill, {})
        item = {"skill": skill, "status": mark.get("status"), "note": mark.get("note", ""),
                "n_all": n_all[skill], "n_good": n_good[skill], "trend": recent[skill] - before[skill],
                "share_good": round(n_good[skill] / good_rows, 3) if good_rows else None,
                "examples": examples.get(skill, [])}
        if item["status"] in HIDDEN:
            out["hidden"].append(item)
        else:
            out["items"].append(item)
    out["items"].sort(key=lambda i: (i["status"] != "learning", -i["n_good"], -i["n_all"], i["skill"]))
    out["hidden"].sort(key=lambda i: (i["status"], i["skill"]))
    out["summary"]["open"] = sum(1 for i in out["items"] if i["status"] is None)
    out["items"] = out["items"][:MAX_ITEMS]
    return out
