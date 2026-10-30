"""Read-only bot view for the Connections tab. Yes/no and counts only; never the token or the chat id."""
from __future__ import annotations

from ...config import Config
from ...db import connect
from . import store
from .token import chat_id, resolve


def view(cfg: Config) -> dict:
    out = {"enabled": cfg.modules.get("bot", False), "token": bool(resolve(cfg)), "chat_id": bool(chat_id(cfg)),
           "last_poll": None, "last_outcome": None, "foreign": 0}
    if not out["enabled"] or not cfg.db.exists():
        return out
    conn = connect(cfg.db, readonly=True)
    try:
        s = store.all_state(conn)
    finally:
        conn.close()
    out.update({"last_poll": s.get("last_poll"), "last_outcome": s.get("last_outcome"), "foreign": int(s.get("foreign") or 0)})
    return out
