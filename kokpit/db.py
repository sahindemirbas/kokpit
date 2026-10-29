"""SQLite access. Jobs write, the web app reads.

Writers must not overlap; every connection carries a 30 s busy timeout so a
late job waits instead of failing.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

BUSY_TIMEOUT_S = 30


def connect(path: Path, readonly: bool = False) -> sqlite3.Connection:
    path = Path(path)
    if readonly:
        if not path.exists():
            raise FileNotFoundError(path)
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=BUSY_TIMEOUT_S)
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(path, timeout=BUSY_TIMEOUT_S)
    conn.row_factory = sqlite3.Row
    return conn
