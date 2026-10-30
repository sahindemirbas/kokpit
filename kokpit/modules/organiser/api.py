"""What the Organiser tab and the morning line show. Reads the proposals table filled by the ``organiser`` job."""
from __future__ import annotations

from ...config import Config
from ...db import connect
from ..health import api as health_api
from . import collect


def _load(cfg: Config, statuses: tuple[str, ...] = ("open",)) -> tuple[list[dict], str, str]:
    """(rows, summary, reason)."""
    try:
        conn = connect(cfg.db, readonly=True)
    except FileNotFoundError:
        return [], "", "no database yet: run `kokpit run-job organiser`"
    try:
        try:
            return collect.open_proposals(conn, statuses), collect.get_summary(conn), ""
        except Exception:  # table not created yet
            return [], "", "no run yet: run `kokpit run-job organiser`"
    finally:
        conn.close()


def morning(cfg: Config) -> str:
    """Short deterministic text for the morning line; empty when the module is off or has not run. Open rows only."""
    if not cfg.modules.get("organiser", False):
        return ""
    rows, summary, reason = _load(cfg)
    return "" if reason else collect.morning_text(rows, summary)


def view(cfg: Config, show_dismissed: bool = False) -> dict:
    out = {"enabled": cfg.modules.get("organiser", False), "freshness": health_api.freshness(cfg, "organiser"),
           "counts": {}, "proposals": [], "text": ""}
    if not out["enabled"]:
        return out
    rows, summary, reason = _load(cfg, ("open", "dismissed") if show_dismissed else ("open",))
    if reason:
        out["reason"] = reason
        return out
    open_rows = [r for r in rows if r["status"] == "open"]
    shown = open_rows + [r for r in rows if r["status"] == "dismissed"]
    out.update(counts=collect.counts(open_rows), proposals=shown[:200], total=len(open_rows), summary=summary,
               dismissed=len(rows) - len(open_rows), show_dismissed=show_dismissed,
               text=collect.morning_text(open_rows, summary),
               mode="vault report" if cfg.organiser["apply_report_to_vault"] else "data folder report")
    return out


def decide(cfg: Config, data: dict) -> dict:
    """The user's decision on one proposal (open, dismissed, done). Writes the database only, never a note.

    Raises ValueError (bad input), LookupError (unknown id), PermissionError (module off)."""
    if not cfg.modules.get("organiser", False):
        raise PermissionError("organiser module is off")
    status = data.get("status")
    if not isinstance(status, str) or status not in collect.DECISIONS:
        raise ValueError(f"status must be one of: {', '.join(collect.DECISIONS)}")
    try:
        conn = connect(cfg.db)
    except FileNotFoundError:
        raise LookupError("proposal not found")
    try:
        collect.ensure_schema(conn)
        return collect.decide(conn, data.get("id"), status)
    finally:
        conn.close()
