"""What the Organiser tab and the morning line show. Reads the proposals table filled by the ``organiser`` job."""
from __future__ import annotations

from ...config import Config
from ...db import connect
from ..health import api as health_api
from . import collect


def _load(cfg: Config) -> tuple[list[dict], str, str]:
    """(rows, summary, reason)."""
    try:
        conn = connect(cfg.db, readonly=True)
    except FileNotFoundError:
        return [], "", "no database yet: run `kokpit run-job organiser`"
    try:
        try:
            return collect.open_proposals(conn), collect.get_summary(conn), ""
        except Exception:  # table not created yet
            return [], "", "no run yet: run `kokpit run-job organiser`"
    finally:
        conn.close()


def morning(cfg: Config) -> str:
    """Short deterministic text for the morning line; empty when the module is off or has not run."""
    if not cfg.modules.get("organiser", False):
        return ""
    rows, summary, reason = _load(cfg)
    return "" if reason else collect.morning_text(rows, summary)


def view(cfg: Config) -> dict:
    out = {"enabled": cfg.modules.get("organiser", False), "freshness": health_api.freshness(cfg, "organiser"),
           "counts": {}, "proposals": [], "text": ""}
    if not out["enabled"]:
        return out
    rows, summary, reason = _load(cfg)
    if reason:
        out["reason"] = reason
        return out
    out.update(counts=collect.counts(rows), proposals=rows[:200], total=len(rows), summary=summary,
               text=collect.morning_text(rows, summary),
               mode="vault report" if cfg.organiser["apply_report_to_vault"] else "data folder report")
    return out
