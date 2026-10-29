"""Today: one screen assembled from the other modules' views. Owns no data."""
from __future__ import annotations

from datetime import datetime

from ...config import Config
from ..brain import api as brain_api
from ..health import api as health_api


def view(cfg: Config, now: datetime | None = None) -> dict:
    now = now or datetime.now()
    out = {"now": now.isoformat(timespec="minutes"), "day": now.strftime("%A, %d %B %Y"), "sections": {}}
    if cfg.modules.get("brain", True):
        b = brain_api.view(cfg)
        out["sections"]["brain"] = {
            "threads": len(b["threads"]),
            "on_you": b["lanes"]["on_you"][:6],
            "upcoming": b["upcoming"][:6],
            "open_tasks": len(b["todo"]),
            "overdue": sum(1 for t in b["todo"] if t["overdue"]),
            "last_session": b["sessions"][0] if b["sessions"] else None,
            "inbox": len(b["inbox"]),
            "vault_exists": b["exists"],
        }
    if cfg.modules.get("health", True):
        h = health_api.view(cfg)
        out["sections"]["health"] = {"available": h["available"], "warnings": h["warnings"],
                                     "jobs": len(h["runs"]), "reason": h.get("reason", "")}
    # weather and calendar are connections, not yet part of the skeleton
    out["sections"]["weather"] = {"status": "planned", "note": "Open-Meteo connection, no key needed"}
    return out
