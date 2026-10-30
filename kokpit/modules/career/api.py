"""Career module: profile, CV proposal and scoring preview for the web app and the CLI."""
from __future__ import annotations

import shutil
import sqlite3

from ...config import Config
from ..health import api as health_api
from . import cv, profile, scoring, store


def view(cfg: Config) -> dict:
    """What the Career tab shows. Reads the profile file only."""
    if not cfg.modules.get("jobs", False):
        return {"enabled": False, "profile": None}
    prof = profile.load(cfg)
    return {**_listings(cfg), "freshness": health_api.freshness(cfg, "career"), "enabled": True, "profile": prof, "profile_saved": profile.exists(cfg), "profile_empty": profile.is_empty(prof),
            "pdftotext": bool(shutil.which("pdftotext")),
            "options": {"remote_pref": list(profile.REMOTE_PREFS), "seniority": list(profile.SENIORITY), "levels": list(profile.LEVELS)}}


def save_profile(cfg: Config, data: dict) -> dict:
    if not cfg.modules.get("jobs", False):
        raise PermissionError("career module is off: set jobs = true under [modules]")
    return {"profile": profile.save(cfg, data)}


def cv_proposal(cfg: Config, text: str) -> dict:
    """Skills and languages proposed from pasted text. Nothing is stored."""
    if not cfg.modules.get("jobs", False):
        raise PermissionError("career module is off: set jobs = true under [modules]")
    if not text or not text.strip():
        raise ValueError("paste the CV text first")
    return cv.propose(text[: cv.MAX_CHARS])


def score_preview(cfg: Config, listing: dict) -> dict:
    """Score one pasted listing against the saved profile."""
    if not str(listing.get("title") or "").strip():
        raise ValueError("title is required")
    remote = listing.get("remote")
    if isinstance(remote, str):
        remote = {"yes": True, "true": True, "no": False, "false": False}.get(remote.lower())
    return scoring.score(profile.load(cfg), {**listing, "remote": remote})


def _listings(cfg: Config, limit: int = 300) -> dict:
    """Stored listings (best score first) and counts. Never fetches; reads the table the career job fills."""
    from ...db import connect
    empty = {"listings": [], "counts": {"total": 0, "open": 0, "closed": 0, "by_source": {}}}
    try:
        conn = connect(cfg.db, readonly=True)
    except FileNotFoundError:
        return {**empty, "reason": "no database yet: run `kokpit run-job career`"}
    try:
        return {"listings": store.listings(conn, "open", limit), "counts": store.counts(conn)}
    except sqlite3.OperationalError:
        return empty
    finally:
        conn.close()
