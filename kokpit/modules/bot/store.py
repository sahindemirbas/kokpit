"""Bot state in kokpit.db: update offset, counters, last poll outcome."""
from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA = (Path(__file__).parent / "schema.sql").read_text(encoding="utf-8")


def ensure_schema(conn: sqlite3.Connection) -> None:
    try:
        conn.executescript(SCHEMA)
    except sqlite3.OperationalError as e:
        if "readonly" not in str(e):
            raise


def get(conn, k: str, default: str = "") -> str:
    try:
        r = conn.execute("SELECT v FROM bot_state WHERE k=?", (k,)).fetchone()
    except sqlite3.OperationalError:
        return default
    return r[0] if r else default


def put(conn, k: str, v) -> None:
    conn.execute("INSERT OR REPLACE INTO bot_state(k, v) VALUES (?, ?)", (k, str(v)))
    conn.commit()


def bump(conn, k: str, n: int = 1) -> None:
    put(conn, k, int(get(conn, k, "0") or 0) + n)


def offset(conn) -> int | None:
    v = get(conn, "offset")
    return int(v) if v.lstrip("-").isdigit() else None


def all_state(conn) -> dict:
    try:
        return {r[0]: r[1] for r in conn.execute("SELECT k, v FROM bot_state")}
    except sqlite3.OperationalError:
        return {}
