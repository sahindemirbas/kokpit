"""Instagram snapshot tables. Collect writes, views read."""
from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA = (Path(__file__).parent / "schema.sql").read_text(encoding="utf-8")
TABLES = ("ig_account", "ig_media", "ig_media_snap", "ig_errors", "ig_meta")
ACCOUNT_COLS = ("followers", "media_count", "reach", "views", "profile_views", "accounts_engaged", "interactions")
SNAP_COLS = ("views", "reach", "saved", "shares", "likes", "comments", "interactions", "replies", "avg_watch_ms")


def ensure_schema(conn: sqlite3.Connection) -> None:
    try:
        conn.executescript(SCHEMA)
    except sqlite3.OperationalError as e:
        if "readonly" not in str(e):
            raise


def has_tables(conn: sqlite3.Connection) -> bool:
    n = conn.execute(f"SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name IN ({','.join('?' for _ in TABLES)})", TABLES).fetchone()[0]
    return n == len(TABLES)


def save_account(conn, day: str, vals: dict, at: str) -> None:
    cols = ", ".join(ACCOUNT_COLS)
    ph = ", ".join("?" for _ in ACCOUNT_COLS)
    upd = ", ".join(f"{c}=COALESCE(excluded.{c}, {c})" for c in ACCOUNT_COLS)
    conn.execute(f"INSERT INTO ig_account(day, {cols}, collected_at) VALUES (?, {ph}, ?) ON CONFLICT(day) DO UPDATE SET {upd}, collected_at=excluded.collected_at",
                 (day, *(vals.get(c) for c in ACCOUNT_COLS), at))


def save_media(conn, day: str, m: dict, product: str, vals: dict, at: str) -> None:
    conn.execute("INSERT INTO ig_media(media_id, media_type, product, posted_at, permalink) VALUES (?,?,?,?,?) "
                 "ON CONFLICT(media_id) DO UPDATE SET media_type=excluded.media_type, product=excluded.product, permalink=excluded.permalink",
                 (m["id"], m.get("media_type"), product, m.get("timestamp"), m.get("permalink")))
    cols = ", ".join(SNAP_COLS)
    ph = ", ".join("?" for _ in SNAP_COLS)
    upd = ", ".join(f"{c}=COALESCE(excluded.{c}, {c})" for c in SNAP_COLS)
    conn.execute(f"INSERT INTO ig_media_snap(day, media_id, {cols}, collected_at) VALUES (?, ?, {ph}, ?) ON CONFLICT(day, media_id) DO UPDATE SET {upd}, collected_at=excluded.collected_at",
                 (day, m["id"], *(vals.get(c) for c in SNAP_COLS), at))


def replace_errors(conn, errors: list[tuple[str, str, str]], at: str) -> None:
    conn.execute("DELETE FROM ig_errors WHERE 1=1")
    conn.executemany("INSERT INTO ig_errors(scope, name, message, at) VALUES (?,?,?,?)", [(s, n, m, at) for s, n, m in errors])


def set_meta(conn, k: str, v: str) -> None:
    conn.execute("INSERT OR REPLACE INTO ig_meta(k, v) VALUES (?, ?)", (k, v))


def meta(conn) -> dict:
    return {r["k"]: r["v"] for r in conn.execute("SELECT k, v FROM ig_meta")} if has_tables(conn) else {}
