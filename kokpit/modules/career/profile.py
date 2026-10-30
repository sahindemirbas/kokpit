"""Career profile: a small JSON file next to the database (``[career] profile_path``), never in the vault.

Fields (all optional; the user's target roles are the primary input, the CV history does not cap them):

    skills, experience_summary, experience_years, seniority (any|junior|mid|senior),
    languages [{language, level}], target_roles, locations, remote_pref (any|onsite|hybrid|remote),
    must_have, nice_to_have, exclude
"""
from __future__ import annotations

import json
from pathlib import Path

from ...config import Config
from . import cv

REMOTE_PREFS = ("any", "onsite", "hybrid", "remote")
SENIORITY = ("any", "junior", "mid", "senior")
LIST_FIELDS = ("skills", "target_roles", "locations", "must_have", "nice_to_have", "exclude")
LEVELS = ("A1", "A2", "B1", "B2", "C1", "C2", "native")


def empty() -> dict:
    return {
        "skills": [], "experience_summary": "", "experience_years": None, "seniority": "any",
        "languages": [], "target_roles": [], "locations": [], "remote_pref": "any",
        "must_have": [], "nice_to_have": [], "exclude": [],
    }


def path(cfg: Config) -> Path:
    return cfg.resolve(cfg.career.get("profile_path", "data/career-profile.json"))


def _clean_list(v) -> list[str]:
    if isinstance(v, str):
        v = v.replace("\n", ",").split(",")
    seen, out = set(), []
    for x in v or []:
        s = " ".join(str(x).split())
        if s and s.lower() not in seen:
            seen.add(s.lower())
            out.append(s[:80])
    return out[:200]


def _clean_languages(v) -> list[dict]:
    if isinstance(v, str):
        items = []
        for part in v.replace("\n", ",").split(","):
            name, _, level = part.partition(":")
            items.append({"language": name, "level": level})
        v = items
    out, seen = [], set()
    for x in v or []:
        if not isinstance(x, dict):
            continue
        name = " ".join(str(x.get("language", "")).split()).title()
        level = str(x.get("level", "")).strip()
        level = level.upper() if level.upper() in LEVELS else ("native" if level.lower() == "native" else "")
        if name and name.lower() not in seen:
            seen.add(name.lower())
            out.append({"language": name[:40], "level": level})
    return out


def normalise(data: dict) -> dict:
    """Validate a profile dict from the form or the CLI. Raises ValueError with a short reason."""
    out = empty()
    for f in LIST_FIELDS:
        out[f] = _clean_list(data.get(f))
    out["languages"] = _clean_languages(data.get("languages"))
    out["experience_summary"] = cv.scrub(str(data.get("experience_summary") or ""))[:2000]
    yrs = data.get("experience_years")
    if yrs in (None, ""):
        out["experience_years"] = None
    else:
        try:
            out["experience_years"] = float(yrs)
        except (TypeError, ValueError):
            raise ValueError("experience_years must be a number") from None
        if not 0 <= out["experience_years"] <= 60:
            raise ValueError("experience_years must be between 0 and 60")
        if out["experience_years"] == int(out["experience_years"]):
            out["experience_years"] = int(out["experience_years"])
    rp = str(data.get("remote_pref") or "any").lower()
    if rp not in REMOTE_PREFS:
        raise ValueError(f"remote_pref must be one of {', '.join(REMOTE_PREFS)}")
    out["remote_pref"] = rp
    sn = str(data.get("seniority") or "any").lower()
    if sn not in SENIORITY:
        raise ValueError(f"seniority must be one of {', '.join(SENIORITY)}")
    out["seniority"] = sn
    return out


def load(cfg: Config) -> dict:
    p = path(cfg)
    if not p.is_file():
        return empty()
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
        return normalise(raw if isinstance(raw, dict) else {})
    except (OSError, ValueError):
        return empty()


def save(cfg: Config, data: dict) -> dict:
    prof = normalise(data)
    p = path(cfg)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(prof, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(p)
    return prof


def exists(cfg: Config) -> bool:
    return path(cfg).is_file()


def is_empty(prof: dict) -> bool:
    return not any(prof.get(f) for f in ("target_roles", "skills", "must_have", "nice_to_have"))


def merge_proposal(prof: dict, proposal: dict) -> dict:
    """Add CV-proposed skills and languages to a profile without removing anything the user typed."""
    out = dict(prof)
    out["skills"] = _clean_list(list(prof.get("skills", [])) + list(proposal.get("skills", [])))
    have = {x["language"].lower() for x in prof.get("languages", [])}
    out["languages"] = list(prof.get("languages", [])) + [x for x in proposal.get("languages", []) if x["language"].lower() not in have]
    return out
