"""Weekly market summary: counts per week from stored listings and, only when the model use is on, a short note.

The numeric trend always works and needs no model: listings, companies and good-fit listings per week for the
last ``WEEKS`` weeks, dated like the market view (posted date, else first seen; undated listings from a source's
first run day are a bulk load, not new postings, and are left out). The note compares this week with the one
before; the reader profile in the prompt is built from the ``[career]`` profile (target roles, locations,
seniority, skills), never from fixed text. The note is written by the ``career`` job, at most once per ISO week,
and only with ``[model.uses] career_weekly = true``. The web app only reads.
"""
from __future__ import annotations

import json
import re
import sqlite3
from collections import Counter
from datetime import date, datetime, timedelta

from ...config import Config
from . import cv, market, skills, store

WEEKS = 8
PURPOSE = "career_weekly"
MAX_OUT = 500     # answer length cap passed to the model
SYSTEM = ("You write a short weekly job-market note for one reader. Use only the numbers given; never invent a number. "
          "Do not present a change of fewer than 5 listings as a trend. No emoji, no em dash. Address the reader as 'you'; "
          "do not use any name. Answer with JSON only.")
PROMPT = """Reader: {reader}
Skills the reader has: {have}.

Below are job listing statistics for this week ({start} to {end}) and the differences from the week before.
Write a very short weekly note for the reader (80 words at most). The dashboard already shows the numbers and tables;
do not repeat them, say only what changed and what to do about it.
Output: ONLY a valid JSON object:
{{"title": "<6-10 words, the main message of the week>", "summary": "<1 short sentence, 20 words at most>",
"points": ["<exactly 3 points, 15 words at most each, the 3 most important things, with numbers>"],
"advice": "<1 sentence, 25 words at most: what to do this week. Be concrete>"}}
Data:
{data}"""


def iso_week(d: date) -> str:
    return d.strftime("%G-W%V")


def has_table(conn: sqlite3.Connection) -> bool:
    return skills.has_table(conn, "career_weekly")


def _dated(conn: sqlite3.Connection) -> list[tuple[date, sqlite3.Row]]:
    """(day, row) for every listing the trend can count; same dating rule as the market view."""
    rows = conn.execute("SELECT id, source, title, company, description, status, score, posted_at, first_seen, closed_at FROM job_listings").fetchall()
    first_day: dict[str, date] = {}
    for r in rows:
        d = market._day(r["first_seen"])
        if d and (r["source"] not in first_day or d < first_day[r["source"]]):
            first_day[r["source"]] = d
    out = []
    for r in rows:
        d = market._day(r["posted_at"])
        if d is None:
            d = market._day(r["first_seen"])
            if d is not None and d == first_day.get(r["source"]):
                continue
        if d is not None:
            out.append((d, r))
    return out


def _good(r) -> bool:
    return (r["score"] or 0) >= skills.GOOD_SCORE


def trend(conn: sqlite3.Connection, today: date | None = None, weeks: int = WEEKS) -> list[dict]:
    """Last ``weeks`` Monday-to-Sunday weeks, oldest first: {week, start, end, listings, companies, good}."""
    today = today or date.today()
    monday = today - timedelta(days=today.weekday())
    starts = [monday - timedelta(weeks=i) for i in range(weeks - 1, -1, -1)]
    buckets = {s: [] for s in starts}
    if store.has_table(conn):
        for d, r in _dated(conn):
            s = d - timedelta(days=d.weekday())
            if s in buckets:
                buckets[s].append(r)
    return [{"week": iso_week(s), "start": s.isoformat(), "end": (s + timedelta(days=6)).isoformat(), "listings": len(rs),
             "companies": len({(r["company"] or "").strip().lower() for r in rs if (r["company"] or "").strip()}),
             "good": sum(_good(r) for r in rs)} for s, rs in buckets.items()]


def _window(rows: list[tuple[date, sqlite3.Row]], start: date, end: date, have: set[str]) -> dict:
    w = [r for d, r in rows if start <= d <= end]
    pats = [(t, market._skill_re(t)) for t in cv.SKILL_TERMS]
    sk: Counter = Counter()
    for r in w:
        text = f"{r['title']} {r['description'] or ''}"
        sk.update(t for t, p in pats if p.search(text))
    comp = Counter((r["company"] or "company unknown") for r in w)
    fit = sorted((r for r in w if _good(r)), key=lambda r: -(r["score"] or 0))
    return {"listings": len(w), "companies": len(comp), "top_companies": [[c, n] for c, n in comp.most_common(8)],
            "skills": dict(sk.most_common(15)), "skills_not_had": {k: v for k, v in sk.most_common(25) if k not in have},
            "good": len(fit), "good_examples": [f"{r['title']} / {r['company'] or 'company unknown'}" for r in fit[:5]]}


def build_stats(conn: sqlite3.Connection, prof: dict, today: date | None = None) -> dict:
    """This week (the 7 days ending today) against the 7 days before. Numbers only."""
    end = today or date.today()
    start = end - timedelta(days=6)
    pstart, pend = start - timedelta(days=7), start - timedelta(days=1)
    have = skills.have(prof, conn)
    rows = _dated(conn) if store.has_table(conn) else []
    cur, prev = _window(rows, start, end, have), _window(rows, pstart, pend, have)
    delta = {k: cur["skills"].get(k, 0) - prev["skills"].get(k, 0) for k in set(cur["skills"]) | set(prev["skills"])}
    closed = 0
    if store.has_table(conn):
        closed = conn.execute("SELECT COUNT(*) FROM job_listings WHERE closed_at IS NOT NULL AND substr(closed_at, 1, 10) >= ?",
                              (start.isoformat(),)).fetchone()[0]
    return {"week": iso_week(end), "start": start.isoformat(), "end": end.isoformat(), "this_week": cur,
            "previous_week": {"listings": prev["listings"], "companies": prev["companies"], "good": prev["good"]},
            "skill_change": dict(sorted(delta.items(), key=lambda x: (-abs(x[1]), x[0]))[:8]), "closed": closed}


def reader(prof: dict) -> str:
    """Reader line for the prompt, from the profile only. Never a name, contact or free-text summary."""
    parts = []
    if prof.get("seniority") and prof["seniority"] != "any":
        parts.append(f"{prof['seniority']} level")
    if prof.get("target_roles"):
        parts.append("looking for " + ", ".join(prof["target_roles"][:6]))
    if prof.get("locations"):
        parts.append("in " + ", ".join(prof["locations"][:4]))
    if prof.get("remote_pref") and prof["remote_pref"] != "any":
        parts.append(f"prefers {prof['remote_pref']} work")
    return ("A job seeker " + "; ".join(parts) + ".") if parts else "A job seeker; no target roles set yet."


def prompt(prof: dict, stats: dict, have: set[str]) -> str:
    return PROMPT.format(reader=reader(prof), have=", ".join(sorted(have)) or "none listed", start=stats["start"], end=stats["end"],
                         data=json.dumps(stats, ensure_ascii=False, indent=1))


def parse_note(text: str) -> dict | None:
    m = re.search(r"\{.*\}", text or "", re.S)
    try:
        note = json.loads(m.group(0)) if m else None
    except ValueError:
        return None
    if not isinstance(note, dict) or not str(note.get("title") or "").strip():
        return None
    points = note.get("points") if isinstance(note.get("points"), list) else []
    return {"title": str(note["title"])[:120], "summary": str(note.get("summary") or "")[:240],
            "points": [str(p)[:200] for p in points[:3]], "advice": str(note.get("advice") or "")[:300]}


def write(cfg: Config, conn: sqlite3.Connection, prof: dict, today: date | None = None, complete=None) -> tuple[dict | None, list[str]]:
    """Write this week's note once. -> (note or None, warnings). Without the model use it does nothing."""
    from ..model import client
    if not client.use_enabled(cfg, PURPOSE):
        return None, []
    store.ensure_schema(conn)
    today = today or date.today()
    if conn.execute("SELECT 1 FROM career_weekly WHERE week=?", (iso_week(today),)).fetchone():
        return None, []
    stats = build_stats(conn, prof, today)
    try:
        text = (complete or client.complete)(cfg, PURPOSE, SYSTEM, prompt(prof, stats, skills.have(prof, conn)), MAX_OUT)
    except (client.ModelDisabled, client.ModelError) as exc:
        return None, [f"weekly note not written: {exc}"]
    note = parse_note(text)
    if not note:
        return None, ["weekly note not written: the model answer was not the expected JSON"]
    conn.execute("INSERT OR REPLACE INTO career_weekly(week, start, end, created, model, stats, text) VALUES (?, ?, ?, ?, ?, ?, ?)",
                 (stats["week"], stats["start"], stats["end"], datetime.now().isoformat(timespec="seconds"), cfg.model.get("model", ""),
                  json.dumps(stats, ensure_ascii=False), json.dumps(note, ensure_ascii=False)))
    conn.commit()
    return note, []


def latest(conn: sqlite3.Connection) -> dict | None:
    if not has_table(conn):
        return None
    r = conn.execute("SELECT week, start, end, created, model, text FROM career_weekly ORDER BY week DESC LIMIT 1").fetchone()
    if not r:
        return None
    try:
        note = json.loads(r["text"] or "{}")
    except ValueError:
        return None
    return {"week": r["week"], "start": r["start"], "end": r["end"], "created": r["created"], "model": r["model"], "note": note}


def view(cfg: Config, conn: sqlite3.Connection, today: date | None = None) -> dict:
    """What the Career tab shows: the numeric trend always; the stored note only while the model use is on."""
    from ..model import client
    on = client.use_enabled(cfg, PURPOSE)
    out = {"weeks": trend(conn, today), "model_on": on, "note": latest(conn) if on else None, "good_score": skills.GOOD_SCORE}
    if not on:
        out["reason"] = f"model use '{PURPOSE}' is off: numbers only, no text"
    return out
