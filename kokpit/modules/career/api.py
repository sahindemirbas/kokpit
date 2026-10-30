"""Career module: profile, CV proposal and scoring preview for the web app and the CLI."""
from __future__ import annotations

import shutil

from ...config import Config
from . import cv, profile, scoring


def view(cfg: Config) -> dict:
    """What the Career tab shows. Reads the profile file only."""
    if not cfg.modules.get("jobs", False):
        return {"enabled": False, "profile": None}
    prof = profile.load(cfg)
    return {"enabled": True, "profile": prof, "profile_saved": profile.exists(cfg), "profile_empty": profile.is_empty(prof),
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
