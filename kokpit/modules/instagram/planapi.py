"""Content calendar for the web app and the guard job. The only writes are rows in ig_plan; publishing goes through publish.py."""
from __future__ import annotations

from datetime import datetime, timezone

from ...config import Config
from ...db import connect
from . import plan, publish, store
from .token import configured


def _on(cfg: Config) -> None:
    if not cfg.modules.get("instagram", False):
        raise PermissionError("the instagram module is off: set instagram = true under [modules]")


def _rw(cfg: Config):
    conn = connect(cfg.db)
    store.ensure_schema(conn)
    return conn


def view(cfg: Config, now: datetime | None = None) -> dict:
    ig = cfg.instagram
    out = {"enabled": cfg.modules.get("instagram", False), "publish": ig["publish"], "configured": configured(cfg), "slots": [],
           "kinds": list(plan.KINDS), "guard_days": ig["plan_guard_days"], "upcoming": 0}
    if not out["enabled"] or not cfg.db.exists():
        return out
    conn = connect(cfg.db, readonly=True)
    try:
        if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='ig_plan'").fetchone():
            out["slots"] = plan.listing(conn)
            out["upcoming"] = plan.upcoming(conn, now or datetime.now(timezone.utc), max(ig["plan_guard_days"], 1))
    finally:
        conn.close()
    return out


def create(cfg: Config, data: dict) -> dict:
    _on(cfg)
    conn = _rw(cfg)
    try:
        return plan.create(conn, data)
    finally:
        conn.close()


def update(cfg: Config, slot_id: int, data: dict) -> dict:
    _on(cfg)
    conn = _rw(cfg)
    try:
        return plan.update(conn, slot_id, data)
    finally:
        conn.close()


def delete(cfg: Config, slot_id: int) -> dict:
    _on(cfg)
    conn = _rw(cfg)
    try:
        plan.delete(conn, slot_id)
        return {"deleted": slot_id}
    finally:
        conn.close()


def approve(cfg: Config, slot_id: int, opener=None, sleep=None) -> dict:
    """The Approve button: publishes this one slot now (if the publish switch is on)."""
    conn = _rw(cfg)
    try:
        kw = {"sleep": sleep} if sleep else {}
        return publish.publish_slot(cfg, conn, slot_id, opener=opener, **kw)
    finally:
        conn.close()


def guard(cfg: Config, conn, now: datetime | None = None) -> tuple[int, dict, list[str]]:
    """Job contract: warn when nothing is planned for the next N days, or when publishing is on without a token."""
    ig = cfg.instagram
    if not cfg.modules.get("instagram", False):
        return 1, {"enabled": False}, []
    now = now or datetime.now(timezone.utc)
    store.ensure_schema(conn)
    warnings: list[str] = []
    days = ig["plan_guard_days"]
    n = plan.upcoming(conn, now, days) if days else None
    if days and n == 0:
        warnings.append(f"instagram: nothing is planned for the next {days} day(s)")
    if ig["publish"] and not configured(cfg):
        warnings.append("instagram: publishing is on but there is no token")
    failed = conn.execute("SELECT COUNT(*) FROM ig_plan WHERE status='failed'").fetchone()[0]
    if failed:
        warnings.append(f"instagram: {failed} planned post(s) failed to publish and wait for a manual retry")
    return 1, {"upcoming": n, "failed": failed, "guard_days": days}, warnings
