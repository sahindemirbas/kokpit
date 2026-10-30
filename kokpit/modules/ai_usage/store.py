"""ai_usage_rows and ai_usage_readers tables. A reader run replaces that tool's rows in one transaction,
so a failed reader keeps the previous rows."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .normalize import normalize_model

SCHEMA = (Path(__file__).parent / "schema.sql").read_text(encoding="utf-8")
COLS = ("tool", "model_raw", "model", "session_hash", "place", "started_at", "last_seen",
        "input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens")


def ensure_schema(conn: sqlite3.Connection) -> None:
    try:
        conn.executescript(SCHEMA)
    except sqlite3.OperationalError as e:
        if "readonly" not in str(e):
            raise


def has_tables(conn: sqlite3.Connection) -> bool:
    return conn.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name IN ('ai_usage_rows','ai_usage_readers')").fetchone()[0] == 2


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


def replace_tool(conn: sqlite3.Connection, tool: str, rows: list[dict], source_mtime: str | None, status: str = "ok") -> None:
    ensure_schema(conn)
    with conn:
        conn.execute("DELETE FROM ai_usage_rows WHERE tool=?", (tool,))
        conn.executemany(
            f"INSERT OR REPLACE INTO ai_usage_rows({', '.join(COLS)}) VALUES ({', '.join('?' for _ in COLS)})",
            [tuple({**r, "tool": tool, "model": normalize_model(r["model_raw"])}[c] for c in COLS) for r in rows])
        last = max((r["last_seen"] for r in rows if r.get("last_seen")), default=None)
        conn.execute("INSERT OR REPLACE INTO ai_usage_readers(tool, last_run, status, error, rows, last_data, source_mtime) VALUES (?,?,?,?,?,?,?)",
                     (tool, now_iso(), status, None, len(rows), last, source_mtime))


def set_reader_error(conn: sqlite3.Connection, tool: str, status: str, error: str) -> None:
    """Keep the old rows and their freshness; only record why the run failed."""
    ensure_schema(conn)
    with conn:
        old = conn.execute("SELECT rows, last_data, source_mtime FROM ai_usage_readers WHERE tool=?", (tool,)).fetchone()
        conn.execute("INSERT OR REPLACE INTO ai_usage_readers(tool, last_run, status, error, rows, last_data, source_mtime) VALUES (?,?,?,?,?,?,?)",
                     (tool, now_iso(), status, error[:200], old["rows"] if old else 0, old["last_data"] if old else None, old["source_mtime"] if old else None))


def rows(conn: sqlite3.Connection) -> list[dict]:
    if not has_tables(conn):
        return []
    return [dict(r) for r in conn.execute("SELECT * FROM ai_usage_rows")]


def readers(conn: sqlite3.Connection) -> list[dict]:
    if not has_tables(conn):
        return []
    return [dict(r) for r in conn.execute("SELECT * FROM ai_usage_readers ORDER BY tool")]
