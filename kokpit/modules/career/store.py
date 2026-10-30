"""Job listings table: upsert by (source, external_id), merge the same job seen in two sources.

Duplicates: a record whose normalised company + title matches an existing row (any source) does not
create a second row; the existing row gets a fresh ``last_seen`` and the new source in ``sources``.
"""
from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime
from pathlib import Path

SCHEMA = (Path(__file__).parent / "schema.sql").read_text(encoding="utf-8")
FIELDS = ("source", "external_id", "title", "company", "location", "remote", "url", "posted_at", "description", "language")
COMPANY_SUFFIX = re.compile(r"\b(inc|ltd|llc|gmbh|spa|s\.p\.a|srl|s\.r\.l|corp|corporation|co|company|plc|ag|sa|bv|group)\b\.?", re.I)
TITLE_NOISE = re.compile(r"\((?:[mfwdxi/ ]{3,9}|all genders?)\)|\b[mfwd]\s*/\s*[mfwd](?:\s*/\s*[mfwdx])?\b", re.I)


def ensure_schema(conn: sqlite3.Connection) -> None:
    """Create the table on a writable connection; a read-only one (the web app) may simply lack it."""
    try:
        conn.executescript(SCHEMA)
    except sqlite3.OperationalError as e:
        if "readonly" not in str(e):
            raise


def has_table(conn: sqlite3.Connection) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='job_listings'").fetchone() is not None


def norm_key(company: str | None, title: str) -> str | None:
    """company|title in lower case without legal suffixes, gender tags and punctuation; None without a company."""
    if not company or not company.strip():
        return None
    c = " ".join(re.sub(r"[^\w\s]", " ", COMPANY_SUFFIX.sub(" ", company.lower())).split())
    t = " ".join(re.sub(r"[^\w\s+#]", " ", TITLE_NOISE.sub(" ", title.lower())).split())
    return f"{c}|{t}" if c and t else None


def upsert(conn: sqlite3.Connection, records: list[dict], now: str | None = None) -> dict:
    """-> {new, updated, duplicates}. Records are job-contract dicts with ``source`` and ``external_id`` set."""
    ensure_schema(conn)
    now = now or datetime.now().isoformat(timespec="seconds")
    new = updated = dups = 0
    cols = ", ".join(FIELDS)
    marks = ", ".join("?" for _ in FIELDS)
    for r in records:
        if not r.get("title") or not r.get("source") or not r.get("external_id"):
            continue
        row = conn.execute("SELECT id FROM job_listings WHERE source=? AND external_id=?", (r["source"], r["external_id"])).fetchone()
        key = norm_key(r.get("company"), r["title"])
        if row:
            conn.execute(
                "UPDATE job_listings SET title=?, company=?, location=?, remote=?, url=?, posted_at=COALESCE(?, posted_at), "
                "description=COALESCE(?, description), language=?, norm_key=?, last_seen=? WHERE id=?",
                (r["title"], r.get("company"), r.get("location"), r.get("remote") or "unknown", r.get("url"), r.get("posted_at"),
                 r.get("description"), r.get("language"), key, now, row["id"]))
            updated += 1
            continue
        twin = conn.execute("SELECT id, sources FROM job_listings WHERE norm_key=? ORDER BY id LIMIT 1", (key,)).fetchone() if key else None
        if twin:
            srcs = [s for s in (twin["sources"] or "").split(",") if s]
            if r["source"] not in srcs:
                srcs.append(r["source"])
            conn.execute("UPDATE job_listings SET last_seen=?, sources=? WHERE id=?", (now, ",".join(srcs), twin["id"]))
            dups += 1
            continue
        conn.execute(
            f"INSERT INTO job_listings({cols}, norm_key, sources, first_seen, last_seen) VALUES ({marks}, ?, ?, ?, ?)",
            tuple(r.get(f) if f != "remote" else (r.get("remote") or "unknown") for f in FIELDS) + (key, r["source"], now, now))
        new += 1
    conn.commit()
    return {"new": new, "updated": updated, "duplicates": dups}


def set_scores(conn: sqlite3.Connection, scorer) -> int:
    """Score every row with ``scorer(listing) -> {score, reasons}`` and store the result."""
    ensure_schema(conn)
    n = 0
    for r in conn.execute("SELECT id, title, description, location, remote FROM job_listings").fetchall():
        res = scorer(dict(r))
        conn.execute("UPDATE job_listings SET score=?, reasons=? WHERE id=?",
                     (res["score"], json.dumps({"reasons": res["reasons"], "max_known": res.get("max_known"), "unknowns": res.get("unknowns")},
                                               ensure_ascii=False), r["id"]))
        n += 1
    conn.commit()
    return n


def _row(r: sqlite3.Row) -> dict:
    d = dict(r)
    d.pop("description", None)
    try:
        d["reasons"] = json.loads(d.get("reasons") or "{}")
    except ValueError:
        d["reasons"] = {}
    d["sources"] = [s for s in (d.get("sources") or "").split(",") if s]
    return d


def listings(conn: sqlite3.Connection, status: str | None = "open", limit: int = 300) -> list[dict]:
    """Rows without the description, best score first; unscored rows last."""
    ensure_schema(conn)
    if not has_table(conn):
        return []
    where, args = ("WHERE status=?", [status]) if status else ("", [])
    rows = conn.execute(f"SELECT * FROM job_listings {where} ORDER BY score IS NULL, score DESC, first_seen DESC, id DESC LIMIT ?", (*args, limit)).fetchall()
    return [_row(r) for r in rows]


def counts(conn: sqlite3.Connection) -> dict:
    ensure_schema(conn)
    if not has_table(conn):
        return {"total": 0, "open": 0, "closed": 0, "by_source": {}}
    total = conn.execute("SELECT COUNT(*) FROM job_listings").fetchone()[0]
    by = {r[0]: r[1] for r in conn.execute("SELECT status, COUNT(*) FROM job_listings GROUP BY status")}
    src = {r[0]: r[1] for r in conn.execute("SELECT source, COUNT(*) FROM job_listings WHERE status='open' GROUP BY source")}
    return {"total": total, "open": by.get("open", 0), "closed": by.get("closed", 0), "by_source": src}
