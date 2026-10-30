"""Deterministic, rule-based listing score: 0-100 plus the reasons behind it.

A score is never returned without reasons. Components whose input is unknown (a listing without a
location, a profile without must-haves) are left out of the maximum and listed as ``unknown``
reasons; unknown is never counted as zero. If nothing at all can be judged the score is ``None``.

Components (maximum points): role 30, must-have 25, nice-to-have 8, skills 7, language 10,
seniority/experience 8, location 6, remote 6. Exclude keywords cap the score at ``EXCLUDE_CAP``.
"""
from __future__ import annotations

import re

from .cv import language_window

EXCLUDE_CAP = 15
LEVEL_RANK = {"": 0, "A1": 1, "A2": 2, "B1": 3, "B2": 4, "C1": 5, "C2": 6, "native": 6}
LEVEL_WORDS = {"native": 6, "mother tongue": 6, "mothertongue": 6, "bilingual": 6, "fluent": 5, "fluency": 5, "proficient": 5,
               "advanced": 5, "excellent": 5, "professional": 4, "upper intermediate": 4, "good": 4, "intermediate": 3,
               "conversational": 3, "basic": 2}
LANGUAGE_NAMES = ["English", "French", "German", "Spanish", "Italian", "Portuguese", "Dutch", "Turkish", "Arabic", "Russian",
                  "Chinese", "Japanese", "Polish", "Romanian", "Swedish"]
SENIOR = re.compile(r"\b(senior|sr\.?|lead|principal|staff|head of|director|manager|vp|chief)\b", re.I)
JUNIOR = re.compile(r"\b(junior|jr\.?|entry[- ]level|graduate|intern|internship|trainee|apprentice|working student)\b", re.I)
YEARS = [
    re.compile(r"(\d{1,2})\s*(?:-|to)\s*\d{1,2}\s*\+?\s*years", re.I),
    re.compile(r"(?:at least|minimum(?: of)?|min\.?|over)\s*(\d{1,2})\s*\+?\s*years", re.I),
    re.compile(r"(\d{1,2})\s*\+\s*years", re.I),
    re.compile(r"(\d{1,2})\s*years(?:'|\u2019)?\s*(?:of\s*)?(?:relevant\s*|professional\s*|work\s*)?experience", re.I),
]
CEFR = re.compile(r"\b([ABC][12])\b")


def _has(term: str, low: str) -> bool:
    t = term.strip().lower()
    return bool(t) and re.search(r"(?<![\w+#])" + re.escape(t) + r"(?![\w+#])", low) is not None


def _words(s: str) -> list[str]:
    return [w for w in re.findall(r"[a-z0-9+#]+", s.lower()) if len(w) > 1 or w in ("r", "c")]


def seniority_of(title: str) -> str | None:
    if SENIOR.search(title):
        return "senior"
    if JUNIOR.search(title):
        return "junior"
    return None


def years_required(text: str) -> int | None:
    vals = []
    for rx in YEARS:
        vals += [int(m.group(1)) for m in rx.finditer(text) if 1 <= int(m.group(1)) <= 25]
        text = rx.sub(lambda m: " " * len(m.group(0)), text)
    return max(vals) if vals else None


def language_requirements(text: str) -> list[dict]:
    """[{language, rank|None, label}] for languages the listing asks for, with the level when written."""
    out = []
    for name in LANGUAGE_NAMES:
        for m in re.finditer(r"\b" + name + r"\b", text, re.I):
            window = language_window(text, m)
            rank, label = None, ""
            cm = CEFR.search(window)
            if cm:
                rank, label = LEVEL_RANK[cm.group(1)], cm.group(1)
            else:
                wl = window.lower()
                for word, r in LEVEL_WORDS.items():
                    if word in wl:
                        rank, label = r, word
                        break
            out.append({"language": name, "rank": rank, "label": label})
            break
    return out


def _role_fit(title: str, roles: list[str]) -> tuple[float, str]:
    tl = title.lower()
    tw = set(_words(title))
    best, why = 0.0, ""
    for role in roles:
        rw = _words(role)
        if not rw:
            continue
        if _has(role, tl):
            return 1.0, f"title matches target role '{role}'"
        share = sum(1 for w in rw if w in tw) / len(rw)
        if share > best:
            best, why = share, f"title partly matches target role '{role}' ({int(share * 100)}% of its words)"
    if best >= 0.5:
        return 0.5 + (best - 0.5) * 0.5, why
    return 0.0, ""


def score(profile: dict, listing: dict) -> dict:
    """-> {score: int|None, reasons: [{kind, effect, text}], max_known, unknowns}.

    ``listing`` keys used: title, description, location, remote (True/False/None).
    ``effect`` is plus, minus, neutral or unknown.
    """
    title = str(listing.get("title") or "")
    desc = str(listing.get("description") or "")
    text = f"{title}\n{desc}"
    low = text.lower()
    reasons: list[dict] = []
    earned = known = 0.0

    def add(kind, effect, msg):
        reasons.append({"kind": kind, "effect": effect, "text": msg})

    def comp(points, maximum):
        nonlocal earned, known
        earned += points
        known += maximum

    # role
    roles = profile.get("target_roles") or []
    if not roles:
        add("role", "unknown", "no target roles in your profile: role fit not judged")
    elif not title:
        add("role", "unknown", "listing has no title: role fit not judged")
    else:
        fit, why = _role_fit(title, roles)
        comp(30 * fit, 30)
        add("role", "plus" if fit >= 0.5 else "minus", why if why else "title matches none of your target roles")

    # must-have, nice-to-have, skills
    must = profile.get("must_have") or []
    if not must:
        add("must_have", "unknown", "no must-have keywords in your profile")
    else:
        hit = [k for k in must if _has(k, low)]
        miss = [k for k in must if k not in hit]
        comp(25 * len(hit) / len(must), 25)
        if hit:
            add("must_have", "plus", "must-have found: " + ", ".join(hit))
        if miss:
            add("must_have", "minus", "must-have missing: " + ", ".join(miss))
    nice = profile.get("nice_to_have") or []
    if not nice:
        add("nice_to_have", "unknown", "no nice-to-have keywords in your profile")
    else:
        hit = [k for k in nice if _has(k, low)]
        comp(8 * len(hit) / len(nice), 8)
        add("nice_to_have", "plus" if hit else "neutral", ("nice-to-have found: " + ", ".join(hit)) if hit else "no nice-to-have keyword found")
    skills = [s for s in (profile.get("skills") or []) if s.lower() not in {m.lower() for m in must + nice}]
    if skills and desc:
        hit = [k for k in skills if _has(k, low)]
        comp(min(7.0, 7 * len(hit) / min(len(skills), 5)), 7)
        if hit:
            add("skills", "plus", "profile skills mentioned: " + ", ".join(hit[:8]))
        else:
            add("skills", "neutral", "none of your profile skills are mentioned")
    elif skills:
        add("skills", "unknown", "listing has no description: skills not compared")

    # language requirement vs profile
    reqs = language_requirements(text)
    mine = {x["language"].lower(): LEVEL_RANK.get(x.get("level") or "", 0) for x in profile.get("languages") or []}
    if not reqs:
        add("language", "unknown", "listing does not state a language requirement")
    elif not mine:
        add("language", "unknown", "no languages in your profile: " + ", ".join(r["language"] for r in reqs) + " asked for")
    else:
        pts = 10.0
        for r in reqs:
            have = mine.get(r["language"].lower())
            need = r["rank"]
            if have is None:
                if r["language"] == "English" and need is None:
                    continue
                pts -= 5
                add("language", "minus", f"{r['language']} is asked for but not in your profile")
            elif need is None:
                add("language", "plus" if have >= 3 else "minus", f"{r['language']} mentioned, level not stated (your level: {have and _name(have) or 'not set'})")
                if have and have < 3:
                    pts -= 3
            elif have == 0:
                add("language", "unknown", f"{r['language']} {r['label']} asked for; your level is not set")
            elif have >= need:
                add("language", "plus", f"{r['language']} {r['label']} asked for; your level is enough")
            else:
                pts -= 5
                add("language", "minus", f"{r['language']} {r['label']} asked for; your level is {_name(have)}")
        comp(max(0.0, pts), 10)

    # seniority and experience
    want = profile.get("seniority") or "any"
    have_years = profile.get("experience_years")
    lvl = seniority_of(title)
    yrs = years_required(text)
    pts, maximum, touched = 8.0, 8, False
    if want != "any" and lvl:
        touched = True
        if (lvl == "senior" and want in ("junior", "mid")) or (lvl == "junior" and want == "senior"):
            pts -= 8
            add("seniority", "minus", f"title says {lvl}; your target seniority is {want}")
        else:
            add("seniority", "plus", f"title seniority ({lvl}) fits your target ({want})")
    elif want != "any":
        add("seniority", "unknown", "title does not state a seniority")
    if yrs is not None and have_years is not None:
        touched = True
        gap = yrs - have_years
        if gap > 0:
            pts -= min(8, 3 + 2 * gap)
            add("seniority", "minus", f"{yrs}+ years asked for; your profile has {have_years}")
        else:
            add("seniority", "plus", f"{yrs}+ years asked for; your profile has {have_years}")
    elif yrs is not None:
        add("seniority", "unknown", f"{yrs}+ years asked for; years of experience not set in your profile")
    if touched:
        comp(max(0.0, pts), maximum)

    # location and remote
    locs = profile.get("locations") or []
    pref = profile.get("remote_pref") or "any"
    remote = listing.get("remote")
    place = str(listing.get("location") or "")
    if locs:
        if remote is True and pref in ("remote", "any", "hybrid"):
            comp(6, 6)
            add("location", "plus", "remote role: location is not a constraint")
        elif not place:
            add("location", "unknown", "listing has no location")
        elif any(_has(l, place.lower()) for l in locs):
            comp(6, 6)
            add("location", "plus", f"location '{place}' is in your list")
        else:
            comp(0, 6)
            add("location", "minus", f"location '{place}' is not in your list")
    else:
        add("location", "unknown", "no locations in your profile")
    if pref == "any":
        add("remote", "unknown", "remote preference is 'any'")
    elif remote is None:
        add("remote", "unknown", "listing does not say whether it is remote")
    elif pref == "remote":
        comp(6 if remote else 0, 6)
        add("remote", "plus" if remote else "minus", "remote role, as you prefer" if remote else "not remote; you prefer remote")
    elif pref == "onsite":
        comp(2 if remote else 6, 6)
        add("remote", "minus" if remote else "plus", "remote role; you prefer on-site" if remote else "on-site role, as you prefer")
    else:  # hybrid
        comp(6, 6)
        add("remote", "plus", "remote or on-site both fit your hybrid preference" if remote else "on-site role fits your hybrid preference")

    # exclude keywords
    hits = [k for k in profile.get("exclude") or [] if _has(k, low)]
    capped = False
    if hits:
        capped = True
        add("exclude", "minus", "exclude keyword found: " + ", ".join(hits))

    if known <= 0:
        if not reasons:
            add("profile", "unknown", "nothing to compare")
        return {"score": None, "reasons": reasons, "max_known": 0, "unknowns": sum(r["effect"] == "unknown" for r in reasons)}
    value = round(100 * earned / known)
    if capped:
        value = min(value, EXCLUDE_CAP)
    return {"score": int(value), "reasons": reasons, "max_known": int(known),
            "unknowns": sum(r["effect"] == "unknown" for r in reasons)}


def _name(rank: int) -> str:
    for k, v in LEVEL_RANK.items():
        if v == rank and k:
            return k
    return "unknown"
