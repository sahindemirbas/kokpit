"""Listing lifecycle: open, closed, reopened.

- Adapter listings: closed when the source did not list them for ``close_after_days`` days, but only
  for sources that returned records in this run (a broken or disabled adapter closes nothing).
- LinkedIn listings: the guest search only shows a 7-day window, so "not seen" is never closure. They
  close only when the posting page itself says so. That check runs only when the guest search is
  enabled, looks at a few rows per run, pauses between requests and stops at the first 429.
- Reopened: a closed listing that a source lists again (last_seen after closed_at) is open again and
  carries ``reopened_at``.
"""
from __future__ import annotations

import sqlite3
import time
from datetime import datetime, timedelta

from . import linkedin

CHECK_LIMIT = 15       # posting pages fetched per run
CHECK_AFTER_DAYS = 7   # only rows that left the search window are worth a page check
RECHECK_DAYS = 1


def _ts(now: datetime) -> str:
    return now.isoformat(timespec="seconds")


def reopen(conn: sqlite3.Connection, now: datetime) -> int:
    cur = conn.execute("UPDATE job_listings SET status='open', reopened_at=?, closed_at=NULL "
                       "WHERE status='closed' AND closed_at IS NOT NULL AND last_seen > closed_at", (_ts(now),))
    return cur.rowcount


def close_missing(conn: sqlite3.Connection, now: datetime, days: int, live_sources: set[str]) -> int:
    """Close open adapter rows not seen for ``days`` days. Rows touched by LinkedIn are skipped."""
    cutoff = _ts(now - timedelta(days=days))
    n = 0
    for r in conn.execute("SELECT id, source, sources FROM job_listings WHERE status='open' AND last_seen < ?", (cutoff,)).fetchall():
        srcs = {s for s in (r["sources"] or "").split(",") if s} | {r["source"]}
        if linkedin.SOURCE in srcs or not srcs <= live_sources:
            continue
        conn.execute("UPDATE job_listings SET status='closed', closed_at=? WHERE id=?", (_ts(now), r["id"]))
        n += 1
    return n


def check_linkedin(conn: sqlite3.Connection, now: datetime, get=None, sleep=time.sleep, delay: float = linkedin.DELAY_S,
                   limit: int = CHECK_LIMIT) -> tuple[int, list[str]]:
    """-> (closed, warnings). Stops at the first 429."""
    old = _ts(now - timedelta(days=CHECK_AFTER_DAYS))
    recheck = _ts(now - timedelta(days=RECHECK_DAYS))
    rows = conn.execute("SELECT id, external_id FROM job_listings WHERE source=? AND status='open' AND last_seen < ? "
                        "AND (checked_at IS NULL OR checked_at < ?) ORDER BY checked_at IS NOT NULL, checked_at, id LIMIT ?",
                        (linkedin.SOURCE, old, recheck, limit)).fetchall()
    closed, warns, first = 0, [], True
    for r in rows:
        if not first:
            sleep(delay)
        first = False
        state = linkedin.posting_state(r["external_id"], get)
        if state == "limit":
            warns.append("linkedin: HTTP 429 on a posting page, closure checks stopped for this run")
            break
        if state == "error":
            continue
        conn.execute("UPDATE job_listings SET checked_at=? WHERE id=?", (_ts(now), r["id"]))
        if state == "closed":
            conn.execute("UPDATE job_listings SET status='closed', closed_at=? WHERE id=?", (_ts(now), r["id"]))
            closed += 1
    return closed, warns


def apply(conn: sqlite3.Connection, now: datetime, close_after_days: int, live_sources: set[str],
          check_get=None, check: bool = False, sleep=time.sleep) -> tuple[dict, list[str]]:
    """Run reopen, missing-closure and (when ``check``) the LinkedIn page check. Commits."""
    reopened = reopen(conn, now)
    closed = close_missing(conn, now, close_after_days, live_sources)
    li_closed, warns = (0, [])
    if check:
        li_closed, warns = check_linkedin(conn, now, check_get, sleep)
    conn.commit()
    return {"reopened": reopened, "closed_missing": closed, "closed_linkedin": li_closed}, warns
