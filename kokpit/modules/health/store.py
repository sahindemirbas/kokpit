"""Health: every job records one row per run; the dashboard shows what is wrong.

Rules (see docs/modules.md, System health):
- a job that must finish before the morning is listed in ``expected``;
- a run with 0 rows is shown as "0 records", never hidden;
- warnings are short strings written by the job itself.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime
from pathlib import Path

SCHEMA = (Path(__file__).parent / "schema.sql").read_text(encoding="utf-8")


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)


def record(conn: sqlite3.Connection, job: str, started: str, ok: bool, rows: int | None = None,
           info: dict | None = None, warnings: list[str] | tuple[str, ...] = (), day: str | None = None) -> None:
    ensure_schema(conn)
    conn.execute(
        "INSERT INTO runs(day, job, started, finished, ok, rows, info, warnings) VALUES (?,?,?,?,?,?,?,?)",
        (day or date.today().isoformat(), job, started, now_iso(), int(bool(ok)), rows,
         json.dumps(info or {}, ensure_ascii=False), json.dumps(list(warnings), ensure_ascii=False)),
    )
    conn.commit()


def latest_runs(conn: sqlite3.Connection) -> list[dict]:
    """Last run of every job that ever ran."""
    ensure_schema(conn)
    rows = conn.execute(
        "SELECT r.* FROM runs r JOIN (SELECT job, MAX(id) AS id FROM runs GROUP BY job) m ON r.id = m.id "
        "ORDER BY r.job"
    ).fetchall()
    return [_row(r) for r in rows]


def today_warnings(conn: sqlite3.Connection, expected: list[str] | tuple[str, ...] = (),
                   today: str | None = None) -> list[str]:
    ensure_schema(conn)
    today = today or date.today().isoformat()
    rows = conn.execute("SELECT job, ok, rows, warnings FROM runs WHERE day=? ORDER BY id", (today,)).fetchall()
    last = {r["job"]: r for r in rows}  # the last run of the day wins
    out = []
    for job in expected:
        if job not in last:
            out.append(f"{job}: did not run today")
    for job, r in last.items():
        ws = json.loads(r["warnings"] or "[]")
        if not r["ok"] and not ws:
            out.append(f"{job}: finished with an error")
        if r["ok"] and r["rows"] == 0:
            out.append(f"{job}: last successful update: 0 records")
        out.extend(f"{job}: {w}" for w in ws)
    return out


def failing_jobs(conn: sqlite3.Connection, min_streak: int = 2) -> list[dict]:
    """Jobs whose most recent ``min_streak`` (or more) runs all failed, newest streak first.

    Each item: ``{"job", "streak", "since", "error"}`` where ``since`` is the start of the first
    failed run in the current streak and ``error`` the last recorded error text (may be empty).
    """
    ensure_schema(conn)
    out = []
    for (job,) in conn.execute("SELECT DISTINCT job FROM runs ORDER BY job").fetchall():
        streak, since, error = 0, "", ""
        for r in conn.execute("SELECT ok, started, finished, info FROM runs WHERE job=? ORDER BY id DESC", (job,)):
            if r["ok"]:
                break
            if not streak:
                error = str((json.loads(r["info"] or "{}") or {}).get("error", ""))[:200]
            streak += 1
            since = r["started"] or r["finished"] or ""
        if streak >= min_streak:
            out.append({"job": job, "streak": streak, "since": since, "error": error})
    out.sort(key=lambda x: (-x["streak"], x["job"]))
    return out


def _row(r: sqlite3.Row) -> dict:
    d = dict(r)
    d["ok"] = bool(d["ok"])
    d["info"] = json.loads(d.get("info") or "{}")
    d["warnings"] = json.loads(d.get("warnings") or "[]")
    return d
