"""JSON view of the health table. Read-only."""
from __future__ import annotations

from ...config import Config
from ...db import connect
from . import store


def view(cfg: Config) -> dict:
    if not cfg.db.exists():
        return {"available": False, "reason": "no database yet; run `kokpit run-job heartbeat`",
                "runs": [], "warnings": [], "failing": []}
    conn = connect(cfg.db, readonly=True)
    try:
        try:
            runs = store.latest_runs(conn)
            warnings = store.today_warnings(conn, cfg.expected_jobs)
            failing = store.failing_jobs(conn)
        except Exception as exc:  # read-only db without schema yet
            return {"available": False, "reason": f"health table not created yet ({exc.__class__.__name__})",
                    "runs": [], "warnings": [], "failing": []}
    finally:
        conn.close()
    return {"available": True, "runs": runs, "warnings": warnings, "failing": failing, "expected": cfg.expected_jobs}
