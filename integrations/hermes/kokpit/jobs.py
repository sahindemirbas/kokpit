"""Opt-in job listings: profile, CV proposal, LinkedIn guest search, scoring, lifecycle and market view.

A port of the Kokpit app's career module (kokpit/modules/career: linkedin.py, scoring.py, store.py,
lifecycle.py, profile.py, market.py, cv.py), copied and adapted, never imported, so the plugin works when
Hermes installs only this folder.

Off twice by default: the ``jobs`` module and, on top of it, the ``jobs_linkedin`` source switch. The only
source is LinkedIn's public guest job search (logged-out pages, no login). LinkedIn's User Agreement
restricts automated access: use is at the user's own risk, it can break or be blocked at any time, and the
plugin does not work around a block. Politeness as in the app: one request per page, a pause between
requests, the 7-day window, at most MAX_QUERIES queries and MAX_PAGES pages each, and the first HTTP 429 or
999 stops the whole run. No retries, no proxy, no cookie. Every request goes through ``collector.fetch``.

Listings go to ``job_listings`` in the plugin store (collector.db) with the app's column names, so the
same read SQL works on either database. The profile is a JSON file in the plugin data dir. A CV file is
read only to propose skills and languages: its text is never stored, logged, sent to a model or sent
over the network.
"""
from __future__ import annotations

import html
import json
import re
import shutil
import subprocess
import time
import urllib.error
import urllib.parse
from collections import Counter
from datetime import date, datetime, timedelta
from pathlib import Path

from . import collector as core
from . import contract as c

JOB = "career"  # the runs.job name tools.freshness(conn, "career") reads
SOURCE = "linkedin"
SEARCH = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
PAGE_SIZE = 10
MAX_QUERIES = 6
MAX_PAGES = 3  # hard cap per query, so one run makes at most MAX_QUERIES * MAX_PAGES requests
DELAY_S = 3.0
BLOCK_CODES = (429, 999)
CLOSE_AFTER_DAYS = 3
SHORT_WINDOW_DAYS = 28

RISK = ("The job source is LinkedIn's public guest job search: logged-out pages, no login. LinkedIn's User "
        "Agreement restricts automated access, so use is at your own risk; it can break or be blocked at any "
        "time, and the plugin does not work around a block.")
NO_BYPASS = "the plugin does not bypass blocks: no retry, no proxy, no other headers; try again later"

SCHEMA = """
CREATE TABLE IF NOT EXISTS job_listings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,
    external_id TEXT NOT NULL,
    title TEXT NOT NULL,
    company TEXT,
    location TEXT,
    remote TEXT NOT NULL DEFAULT 'unknown',
    url TEXT,
    posted_at TEXT,
    description TEXT,
    language TEXT,
    norm_key TEXT,
    sources TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'open',
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    closed_at TEXT,
    reopened_at TEXT,
    checked_at TEXT,
    score INTEGER,
    reasons TEXT NOT NULL DEFAULT '[]',
    UNIQUE(source, external_id)
);
CREATE INDEX IF NOT EXISTS idx_job_norm ON job_listings(norm_key);
CREATE INDEX IF NOT EXISTS idx_job_status ON job_listings(status);
"""


# ---------------------------------------------------------------- CV text (port of cv.py)

CV_MAX_CHARS = 200_000
CV_TIMEOUT_S = 30
EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
URL = re.compile(r"(?:https?://|www\.)\S+|\b(?:linkedin|github)\.com/\S+", re.I)
PHONE = re.compile(r"(?<![\w.])(?:\+|00)?\d[\d ()./-]{7,}\d(?![\w])")

SKILL_TERMS = [
    "python", "sql", "r", "java", "javascript", "typescript", "c++", "c#", "go", "scala", "bash", "excel", "power bi", "tableau",
    "looker", "dbt", "airflow", "spark", "hadoop", "pandas", "numpy", "scikit-learn", "tensorflow", "pytorch", "machine learning",
    "deep learning", "nlp", "statistics", "forecasting", "time series", "a/b testing", "data visualization", "dashboards", "etl",
    "data modeling", "data warehouse", "postgresql", "mysql", "sqlite", "mongodb", "bigquery", "snowflake", "aws", "azure", "gcp",
    "docker", "kubernetes", "git", "linux", "rest api", "react", "django", "flask", "fastapi", "project management", "agile",
    "scrum", "crm", "sap", "reporting", "data analysis", "data engineering", "mlops", "llm",
]
LANGUAGE_NAMES = ["English", "French", "German", "Spanish", "Italian", "Portuguese", "Dutch", "Turkish", "Arabic", "Russian",
                  "Chinese", "Japanese", "Polish", "Romanian", "Swedish"]
CV_LEVEL_WORDS = {"native": "native", "mother tongue": "native", "mothertongue": "native", "bilingual": "C2",
                  "proficient": "C1", "fluent": "C1", "advanced": "C1", "upper intermediate": "B2",
                  "intermediate": "B1", "conversational": "B1", "basic": "A2", "beginner": "A1", "elementary": "A2"}
CEFR = re.compile(r"\b([ABC][12])\b")
_SEP = re.compile(r"[,;.\n|/]")


class CVError(RuntimeError):
    """A short reason why text could not be read from the file."""


def scrub(text: str) -> str:
    """Remove obvious e-mail addresses, phone numbers and links."""
    text = EMAIL.sub("", text)
    text = URL.sub("", text)
    return PHONE.sub(lambda m: "" if sum(ch.isdigit() for ch in m.group(0)) >= 9 else m.group(0), text)


def extract_text(path) -> str:
    """Scrubbed text of a .txt/.md file, or of a .pdf through pdftotext when it is installed. Never stored."""
    p = Path(str(path)).expanduser()
    if not p.is_file():
        raise CVError("file not found")
    suffix = p.suffix.lower()
    if suffix in (".txt", ".md", ".text"):
        try:
            return scrub(p.read_text(encoding="utf-8", errors="replace")[:CV_MAX_CHARS])
        except OSError as exc:
            raise CVError(f"cannot read the file: {exc.__class__.__name__}") from None
    if suffix == ".pdf":
        exe = shutil.which("pdftotext")
        if not exe:
            raise CVError("pdftotext is not installed: paste the CV text or pass a .txt/.md file instead")
        try:
            res = subprocess.run([exe, "-layout", str(p), "-"], capture_output=True, timeout=CV_TIMEOUT_S, check=False)
        except subprocess.TimeoutExpired:
            raise CVError(f"pdftotext timed out after {CV_TIMEOUT_S} s") from None
        if res.returncode != 0:
            raise CVError("pdftotext could not read the file")
        return scrub(res.stdout.decode("utf-8", errors="replace")[:CV_MAX_CHARS])
    raise CVError("unsupported file type: use .pdf (needs pdftotext), .txt or .md")


def language_window(text: str, m: re.Match) -> str:
    """Text next to a language name that can hold its level, cut at the nearest separators."""
    before = _SEP.split(text[max(0, m.start() - 25): m.start()])[-1]
    after = _SEP.split(text[m.end(): m.end() + 30])[0]
    return before + " " + m.group(0) + " " + after


def _term(term: str, low: str) -> bool:
    return re.search(r"(?<![\w+#])" + re.escape(term) + r"(?![\w+#])", low) is not None


def propose(text: str) -> dict:
    """Suggest skills and languages from CV text. A proposal only: nothing is saved without apply."""
    text = scrub(text or "")
    low = text.lower()
    langs = []
    for name in LANGUAGE_NAMES:
        for m in re.finditer(r"\b" + name + r"\b", text, re.I):
            window = language_window(text, m)
            cm = CEFR.search(window)
            level = cm.group(1) if cm else next((lv for w, lv in CV_LEVEL_WORDS.items() if w in window.lower()), "")
            langs.append({"language": name, "level": level})
            break
    return {"skills": [t for t in SKILL_TERMS if _term(t, low)], "languages": langs, "chars": len(text)}


# ---------------------------------------------------------------- profile (port of profile.py)

REMOTE_PREFS = ("any", "onsite", "hybrid", "remote")
SENIORITY = ("any", "junior", "mid", "senior")
LIST_FIELDS = ("skills", "target_roles", "locations", "must_have", "nice_to_have", "exclude")
LEVELS = ("A1", "A2", "B1", "B2", "C1", "C2", "native")
FIELDS = ("skills", "experience_summary", "experience_years", "seniority", "languages", "target_roles", "locations",
          "remote_pref", "must_have", "nice_to_have", "exclude")


def empty() -> dict:
    return {"skills": [], "experience_summary": "", "experience_years": None, "seniority": "any",
            "languages": [], "target_roles": [], "locations": [], "remote_pref": "any",
            "must_have": [], "nice_to_have": [], "exclude": []}


LIST_SEPARATORS = ",\n"
LOCATION_SEPARATORS = ";\n"


def _seps(field: str) -> str:
    return LOCATION_SEPARATORS if field == "locations" else LIST_SEPARATORS


def _clean_list(v, seps: str = LIST_SEPARATORS) -> list[str]:
    """A cleaned, de-duplicated list. A string is split on any character in seps; a list is kept entry by
    entry (never re-split). Locations use LOCATION_SEPARATORS, so 'Milan, Italy' stays one entry."""
    if isinstance(v, str):
        v = re.split("[" + re.escape(seps) + "]", v)
    seen, out = set(), []
    for x in v or []:
        s = " ".join(str(x).split())
        if s and s.lower() not in seen:
            seen.add(s.lower())
            out.append(s[:80])
    return out[:200]


def _level(level) -> str:
    s = str(level or "").strip()
    return s.upper() if s.upper() in LEVELS else ("native" if s.lower() == "native" else "")


def _clean_languages(v) -> list[dict]:
    if isinstance(v, str):
        v = [{"language": n, "level": lv} for n, _, lv in (p.partition(":") for p in v.replace("\n", ",").split(","))]
    out, seen = [], set()
    for x in v or []:
        if not isinstance(x, dict):
            continue
        name = " ".join(str(x.get("language", "")).split()).title()
        if name and name.lower() not in seen:
            seen.add(name.lower())
            out.append({"language": name[:40], "level": _level(x.get("level"))})
    return out


def normalise(data: dict) -> dict:
    """Validate a profile dict. Raises ValueError with a short reason."""
    data = data if isinstance(data, dict) else {}
    out = empty()
    for f in LIST_FIELDS:
        out[f] = _clean_list(data.get(f), _seps(f))
    out["languages"] = _clean_languages(data.get("languages"))
    out["experience_summary"] = scrub(str(data.get("experience_summary") or ""))[:2000]
    yrs = data.get("experience_years")
    if yrs not in (None, ""):
        try:
            y = float(yrs)
        except (TypeError, ValueError):
            raise ValueError("experience_years must be a number") from None
        if not 0 <= y <= 60:
            raise ValueError("experience_years must be between 0 and 60")
        out["experience_years"] = int(y) if y == int(y) else y
    rp = str(data.get("remote_pref") or "any").lower()
    if rp not in REMOTE_PREFS:
        raise ValueError(f"remote_pref must be one of {', '.join(REMOTE_PREFS)}")
    out["remote_pref"] = rp
    sn = str(data.get("seniority") or "any").lower()
    if sn not in SENIORITY:
        raise ValueError(f"seniority must be one of {', '.join(SENIORITY)}")
    out["seniority"] = sn
    return out


def load_profile(path) -> dict:
    p = Path(path) if path is not None else None
    if p is None or not p.is_file():
        return empty()
    try:
        return normalise(json.loads(p.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return empty()


def save_profile(path, data: dict) -> dict:
    prof = normalise(data)
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(prof, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(p)
    return prof


def is_empty(prof: dict) -> bool:
    return not any(prof.get(f) for f in ("target_roles", "skills", "must_have", "nice_to_have"))


def set_field(prof: dict, field: str, value: str) -> dict:
    """The profile with one field replaced. value: comma separated for lists, semicolon separated for locations
    ('Milan, Italy; Turin'), 'English:C1, Italian:B1' for languages, empty to clear. Raises ValueError for an
    unknown field or a value outside an enumeration."""
    field = (field or "").strip().lower()
    if field not in FIELDS:
        raise ValueError(f"unknown field {c.clean(field, 30)}; fields: {', '.join(FIELDS)}")
    value = (value or "").strip()
    out = dict(prof)
    if field == "languages":
        for part in (p for p in value.replace("\n", ",").split(",") if p.strip()):
            lv = part.partition(":")[2].strip()
            if lv and not _level(lv):
                raise ValueError(f"language level must be one of {', '.join(LEVELS)} (for example English:C1)")
        out[field] = _clean_languages(value)
    elif field in LIST_FIELDS:
        out[field] = _clean_list(value, _seps(field))
    elif field == "experience_years":
        out[field] = value or None
    elif field in ("seniority", "remote_pref"):
        allowed = SENIORITY if field == "seniority" else REMOTE_PREFS
        if (value or "any").lower() not in allowed:
            raise ValueError(f"{field} must be one of {', '.join(allowed)}")
        out[field] = value or "any"
    else:
        out[field] = value
    return normalise(out)


def merge_proposal(prof: dict, proposal: dict) -> dict:
    """Add CV-proposed skills and languages without removing anything the user typed."""
    out = dict(prof)
    out["skills"] = _clean_list(list(prof.get("skills", [])) + list(proposal.get("skills", [])))
    have = {x["language"].lower() for x in prof.get("languages", [])}
    out["languages"] = list(prof.get("languages", [])) + [x for x in proposal.get("languages", [])
                                                          if x["language"].lower() not in have]
    return normalise(out)


# ---------------------------------------------------------------- LinkedIn guest search (port of linkedin.py)

def pause(seconds: float) -> None:
    """The wait between two requests (module level, so tests replace it and never sleep for real)."""
    time.sleep(seconds)


class Blocked(RuntimeError):
    """LinkedIn answered 429 or 999: stop and report, never retry or work around."""


def _text(s: str | None) -> str | None:
    if not s:
        return None
    s = html.unescape(re.sub(r"<[^>]+>", " ", s))
    return re.sub(r"\s+", " ", s).strip() or None


def _grab(rx: str, card: str) -> str | None:
    m = re.search(rx, card, re.S)
    return _text(m.group(1)) if m else None


def parse_cards(page: str) -> list[dict]:
    """Job records from one page of guest search results (HTML list items). Markup as the app parser expects."""
    out = []
    for card in re.split(r"<li\b", page or "")[1:]:
        m = re.search(r"jobPosting:(\d+)", card)
        title = _grab(r'base-search-card__title[^>]*>(.*?)</h3>', card)
        if not m or not title:
            continue
        dt = re.search(r'<time[^>]*datetime="(\d{4}-\d{2}-\d{2})', card)
        out.append({"source": SOURCE, "external_id": m.group(1), "title": title[:200],
                    "company": (_grab(r'base-search-card__subtitle[^>]*>(.*?)</h4>', card) or "")[:120] or None,
                    "location": (_grab(r'job-search-card__location[^>]*>(.*?)</span>', card) or "")[:120] or None,
                    "remote": "unknown", "url": f"https://www.linkedin.com/jobs/view/{m.group(1)}",
                    "posted_at": dt.group(1) if dt else None, "description": None, "language": None})
    return out


def queries(profile: dict) -> list[tuple[str, str]]:
    """(keywords, location) pairs from the profile targets, capped at MAX_QUERIES."""
    roles = profile.get("target_roles") or []
    locs = profile.get("locations") or [""]
    return [(r, loc) for r in roles for loc in locs][:MAX_QUERIES]


def search(profile: dict, get=None, sleep=None, delay: float = DELAY_S, pages: int = MAX_PAGES):
    """-> (records, warnings, meta). Never raises on a block: it stops at once and says so."""
    get = get or core.fetch
    sleep = sleep or pause
    pages = max(1, min(MAX_PAGES, int(pages)))
    meta = {"requests": 0, "blocked": None, "error": None}
    pairs = queries(profile)
    if not pairs:
        return [], ["linkedin: no target roles in the profile, nothing to search"], meta
    records: dict[str, dict] = {}
    warnings: list[str] = []
    try:
        for kw, loc in pairs:
            for page in range(pages):
                if meta["requests"]:
                    sleep(delay)
                params = {"keywords": kw, "location": loc, "f_TPR": "r604800", "start": page * PAGE_SIZE}
                if profile.get("remote_pref") == "remote":
                    params["f_WT"] = "2"
                url = f"{SEARCH}?{urllib.parse.urlencode({k: v for k, v in params.items() if v != ''})}"
                meta["requests"] += 1
                try:
                    body = get(url)
                except urllib.error.HTTPError as exc:
                    if exc.code in BLOCK_CODES:
                        raise Blocked(f"HTTP {exc.code}") from None
                    raise
                cards = parse_cards(body)
                for card in cards:
                    if profile.get("remote_pref") == "remote":
                        card["remote"] = "remote"
                    records.setdefault(card["external_id"], card)
                if len(cards) < PAGE_SIZE:
                    break
    except Blocked as exc:
        meta["blocked"] = str(exc)
        warnings.append(f"linkedin: blocked with {exc}, run stopped; {NO_BYPASS}")
    except Exception as exc:
        meta["error"] = f"{type(exc).__name__}: {str(exc)[:120]}"
        warnings.append(f"linkedin: {meta['error']}, run stopped")
    if not records and not warnings:
        warnings.append("linkedin: 0 records (no match, or the page layout changed)")
    return list(records.values()), warnings, meta


# ---------------------------------------------------------------- scoring (port of scoring.py)

EXCLUDE_CAP = 15
FULL_SCALE = 100  # 30 role, 25 must-have, 8 nice-to-have, 7 skills, 10 language, 8 seniority, 6 location, 6 remote
COVERAGE_MIN = 50
LOW_COVERAGE_CAP = 60
LEVEL_RANK = {"": 0, "A1": 1, "A2": 2, "B1": 3, "B2": 4, "C1": 5, "C2": 6, "native": 6}
LEVEL_WORDS = {"native": 6, "mother tongue": 6, "mothertongue": 6, "bilingual": 6, "fluent": 5, "fluency": 5,
               "proficient": 5, "advanced": 5, "excellent": 5, "professional": 4, "upper intermediate": 4, "good": 4,
               "intermediate": 3, "conversational": 3, "basic": 2}
SENIOR = re.compile(r"\b(senior|sr\.?|lead|principal|staff|head of|director|manager|vp|chief)\b", re.I)
JUNIOR = re.compile(r"\b(junior|jr\.?|entry[- ]level|graduate|intern|internship|trainee|apprentice|working student)\b", re.I)
YEARS = [
    re.compile(r"(\d{1,2})\s*(?:-|to)\s*\d{1,2}\s*\+?\s*years", re.I),
    re.compile(r"(?:at least|minimum(?: of)?|min\.?|over)\s*(\d{1,2})\s*\+?\s*years", re.I),
    re.compile(r"(\d{1,2})\s*\+\s*years", re.I),
    re.compile(r"(\d{1,2})\s*years(?:'|\u2019)?\s*(?:of\s*)?(?:relevant\s*|professional\s*|work\s*)?experience", re.I),
]


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
    out = []
    for name in LANGUAGE_NAMES:
        for m in re.finditer(r"\b" + name + r"\b", text, re.I):
            window = language_window(text, m)
            rank, label = None, ""
            cm = CEFR.search(window)
            if cm:
                rank, label = LEVEL_RANK[cm.group(1)], cm.group(1)
            else:
                for word, r in LEVEL_WORDS.items():
                    if word in window.lower():
                        rank, label = r, word
                        break
            out.append({"language": name, "rank": rank, "label": label})
            break
    return out


def _role_fit(title: str, roles: list[str]) -> tuple[float, str]:
    tl, tw = title.lower(), set(_words(title))
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


def _name(rank: int) -> str:
    return next((k for k, v in LEVEL_RANK.items() if v == rank and k), "unknown")


def score(profile: dict, listing: dict) -> dict:
    """-> {score: int|None, reasons: [{kind, effect, text}], max_known, unknowns}. Unknown is never zero:
    components without input are left out of the maximum; nothing judgeable gives score None.

    Coverage scaling: when fewer than COVERAGE_MIN of the FULL_SCALE points could be judged, the score is scaled
    to LOW_COVERAGE_CAP percent of itself (100 -> 60, 83 -> 50), so the order between listings is kept, and a
    'coverage' reason names the unscaled value. The exclude cap still wins, and an exclude hit shows no
    coverage reason.

    Location rule: a profile location matches when its first comma part (the city, or the whole string when
    there is no comma) is found in the listing location as a whole word, so 'Milan, Italy' matches
    'Milan, Lombardy, Italy'. Limit: same-named cities in different countries are not told apart."""
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

    roles = profile.get("target_roles") or []
    if not roles:
        add("role", "unknown", "no target roles in your profile: role fit not judged")
    elif not title:
        add("role", "unknown", "listing has no title: role fit not judged")
    else:
        fit, why = _role_fit(title, roles)
        comp(30 * fit, 30)
        add("role", "plus" if fit >= 0.5 else "minus", why if why else "title matches none of your target roles")

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
        add("nice_to_have", "plus" if hit else "neutral",
            ("nice-to-have found: " + ", ".join(hit)) if hit else "no nice-to-have keyword found")
    skills = [s for s in (profile.get("skills") or []) if s.lower() not in {m.lower() for m in must + nice}]
    if skills and desc:
        hit = [k for k in skills if _has(k, low)]
        comp(min(7.0, 7 * len(hit) / min(len(skills), 5)), 7)
        add("skills", "plus" if hit else "neutral",
            ("profile skills mentioned: " + ", ".join(hit[:8])) if hit else "none of your profile skills are mentioned")
    elif skills:
        add("skills", "unknown", "listing has no description: skills not compared")

    reqs = language_requirements(text)
    mine = {x["language"].lower(): LEVEL_RANK.get(x.get("level") or "", 0) for x in profile.get("languages") or []}
    if not reqs:
        add("language", "unknown", "listing does not state a language requirement")
    elif not mine:
        add("language", "unknown", "no languages in your profile: " + ", ".join(r["language"] for r in reqs) + " asked for")
    else:
        pts = 10.0
        for r in reqs:
            have, need = mine.get(r["language"].lower()), r["rank"]
            if have is None:
                if r["language"] == "English" and need is None:
                    continue
                pts -= 5
                add("language", "minus", f"{r['language']} is asked for but not in your profile")
            elif need is None:
                add("language", "plus" if have >= 3 else "minus",
                    f"{r['language']} mentioned, level not stated (your level: {have and _name(have) or 'not set'})")
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

    want = profile.get("seniority") or "any"
    have_years = profile.get("experience_years")
    lvl, yrs = seniority_of(title), years_required(text)
    pts, touched = 8.0, False
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
        comp(max(0.0, pts), 8)

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
        elif any(_has(loc.split(",")[0], place.lower()) for loc in locs):
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
    else:
        comp(6, 6)
        add("remote", "plus", "remote or on-site both fit your hybrid preference" if remote
            else "on-site role fits your hybrid preference")

    hits = [k for k in profile.get("exclude") or [] if _has(k, low)]
    if hits:
        add("exclude", "minus", "exclude keyword found: " + ", ".join(hits))
    unknowns = sum(r["effect"] == "unknown" for r in reasons)
    if known <= 0:
        return {"score": None, "reasons": reasons, "max_known": 0, "unknowns": unknowns}
    value = round(100 * earned / known)
    if known < COVERAGE_MIN:
        raw = value
        value = round(raw * LOW_COVERAGE_CAP / 100)
        if not hits:
            add("coverage", "neutral", f"scaled to {LOW_COVERAGE_CAP} percent: only {int(known)} of {FULL_SCALE} "
                f"points could be judged (unscaled {raw})")
    if hits:
        value = min(value, EXCLUDE_CAP)
    return {"score": int(value), "reasons": reasons, "max_known": int(known), "unknowns": unknowns}


def remote_flag(value: str | None) -> bool | None:
    return {"remote": True, "onsite": False}.get(value or "")


# ---------------------------------------------------------------- store and lifecycle (port of store.py, lifecycle.py)

COMPANY_SUFFIX = re.compile(r"\b(inc|ltd|llc|gmbh|spa|s\.p\.a|srl|s\.r\.l|corp|corporation|co|company|plc|ag|sa|bv|group)\b\.?", re.I)
TITLE_NOISE = re.compile(r"\((?:[mfwdxi/ ]{3,9}|all genders?)\)|\b[mfwd]\s*/\s*[mfwd](?:\s*/\s*[mfwdx])?\b", re.I)
ROW_FIELDS = ("source", "external_id", "title", "company", "location", "remote", "url", "posted_at", "description",
              "language")


def norm_key(company: str | None, title: str) -> str | None:
    """company|title in lower case without legal suffixes, gender tags and punctuation; None without a company."""
    if not company or not company.strip():
        return None
    co = " ".join(re.sub(r"[^\w\s]", " ", COMPANY_SUFFIX.sub(" ", company.lower())).split())
    t = " ".join(re.sub(r"[^\w\s+#]", " ", TITLE_NOISE.sub(" ", title.lower())).split())
    return f"{co}|{t}" if co and t else None


def upsert(conn, records: list[dict], now: str) -> dict:
    """-> {new, updated, duplicates}: one row per (source, external_id); the same company + title merges."""
    new = updated = dups = 0
    cols, marks = ", ".join(ROW_FIELDS), ", ".join("?" for _ in ROW_FIELDS)
    for r in records:
        if not r.get("title") or not r.get("source") or not r.get("external_id"):
            continue
        row = conn.execute("SELECT id FROM job_listings WHERE source=? AND external_id=?",
                           (r["source"], r["external_id"])).fetchone()
        key = norm_key(r.get("company"), r["title"])
        if row:
            conn.execute("UPDATE job_listings SET title=?, company=?, location=?, remote=?, url=?, "
                         "posted_at=COALESCE(?, posted_at), description=COALESCE(?, description), language=?, "
                         "norm_key=?, last_seen=? WHERE id=?",
                         (r["title"], r.get("company"), r.get("location"), r.get("remote") or "unknown", r.get("url"),
                          r.get("posted_at"), r.get("description"), r.get("language"), key, now, row["id"]))
            updated += 1
            continue
        twin = conn.execute("SELECT id, sources FROM job_listings WHERE norm_key=? ORDER BY id LIMIT 1",
                            (key,)).fetchone() if key else None
        if twin:
            srcs = [s for s in (twin["sources"] or "").split(",") if s]
            if r["source"] not in srcs:
                srcs.append(r["source"])
            conn.execute("UPDATE job_listings SET last_seen=?, sources=? WHERE id=?", (now, ",".join(srcs), twin["id"]))
            dups += 1
            continue
        conn.execute(f"INSERT INTO job_listings({cols}, norm_key, sources, first_seen, last_seen) "
                     f"VALUES ({marks}, ?, ?, ?, ?)",
                     tuple(r.get(f) if f != "remote" else (r.get("remote") or "unknown") for f in ROW_FIELDS)
                     + (key, r["source"], now, now))
        new += 1
    return {"new": new, "updated": updated, "duplicates": dups}


def set_scores(conn, prof: dict) -> int:
    n = 0
    for r in conn.execute("SELECT id, title, description, location, remote FROM job_listings").fetchall():
        res = score(prof, {**dict(r), "remote": remote_flag(r["remote"])})
        conn.execute("UPDATE job_listings SET score=?, reasons=? WHERE id=?",
                     (res["score"], json.dumps({"reasons": res["reasons"], "max_known": res["max_known"],
                                                "unknowns": res["unknowns"]}, ensure_ascii=False), r["id"]))
        n += 1
    return n


def lifecycle(conn, now: datetime, days: int, live_sources: set) -> dict:
    """Reopen rows a source lists again; close open rows not seen for `days` days, but only when every
    source of the row returned results in this run (a blocked, failing or empty source closes nothing)."""
    ts = now.isoformat(timespec="seconds")
    reopened = conn.execute("UPDATE job_listings SET status='open', reopened_at=?, closed_at=NULL "
                            "WHERE status='closed' AND closed_at IS NOT NULL AND last_seen > closed_at", (ts,)).rowcount
    cutoff = (now - timedelta(days=days)).isoformat(timespec="seconds")
    closed = 0
    for r in conn.execute("SELECT id, source, sources FROM job_listings WHERE status='open' AND last_seen < ?",
                          (cutoff,)).fetchall():
        srcs = {s for s in (r["sources"] or "").split(",") if s} | {r["source"]}
        if live_sources and srcs <= live_sources:
            conn.execute("UPDATE job_listings SET status='closed', closed_at=? WHERE id=?", (ts, r["id"]))
            closed += 1
    return {"reopened": reopened, "closed": closed}


def counts(conn) -> dict:
    total = conn.execute("SELECT COUNT(*) FROM job_listings").fetchone()[0]
    by = {r[0]: r[1] for r in conn.execute("SELECT status, COUNT(*) FROM job_listings GROUP BY status")}
    return {"total": total, "open": by.get("open", 0), "closed": by.get("closed", 0)}


# ---------------------------------------------------------------- collect

def _ever_ran(path) -> bool:
    conn = c.open_db(path)
    if conn is None:
        return False
    try:
        return not c.table_problem(conn, "runs") and \
            conn.execute("SELECT 1 FROM runs WHERE job=? LIMIT 1", (JOB,)).fetchone() is not None
    finally:
        conn.close()


def collect(state: dict, now: datetime | None = None, get=None, sleep=None) -> dict:
    """One career collection into the plugin store, recorded as one runs row (job career)."""
    if state.get("mode") == "demo":
        raise c.NotReady("demo mode never fetches", "demo shows seeded listings; configure your own vault or "
                         "kokpit_config to collect")
    js = state.get("jobs") or {}
    if not js.get("linkedin"):
        raise c.NotReady("no job source enabled",
                         f"{RISK} If you accept that, turn it on: /kokpit jobs linkedin on "
                         "(or hermes config set plugins.entries.kokpit.settings.jobs_linkedin true)")
    store = state.get("store") or {}
    if store.get("path") is None:
        raise c.NotReady("no place to store job listings", "check the plugin data dir")
    prof = load_profile(js.get("profile_path"))
    if not prof["target_roles"]:
        raise c.NotReady("no target roles in the profile, nothing was fetched",
                         "add them: /kokpit jobs profile set target_roles Data Analyst, BI Developer")
    first = not _ever_ran(store["path"])
    days = int(js.get("close_after_days") or CLOSE_AFTER_DAYS)
    meta: dict = {}

    def job(conn):
        conn.executescript(SCHEMA)
        records, warnings, m = search(prof, get=get or core.fetch, sleep=sleep or pause)
        meta.update(m)
        at = now or datetime.now()
        res = upsert(conn, records, at.isoformat(timespec="seconds"))
        live = {SOURCE} if records and not m["blocked"] and not m["error"] else set()
        life = lifecycle(conn, at, days, live)
        scored = set_scores(conn, prof)
        conn.commit()
        info = {"sources": {SOURCE: len(records)}, "requests": m["requests"], **res, **life, "scored": scored,
                **counts(conn)}
        if m["blocked"]:
            info["blocked"] = m["blocked"]
        if m["error"] and not records:
            info["error"] = m["error"]
        return len(records), info, warnings

    r = core.run_job(store["path"], JOB, job)
    info = r["info"]
    out = {"status": "blocked" if info.get("blocked") else ("ok" if r["ok"] else "failed"), "source": SOURCE,
           "fetched": r["rows"], "requests": info.get("requests"),
           **{k: info.get(k) for k in ("new", "updated", "duplicates", "closed", "reopened", "open", "total")},
           "warnings": r["warnings"], "store": core.store_info(store)}
    if info.get("blocked"):
        out["blocked"] = info["blocked"]
        out["note"] = f"LinkedIn answered {info['blocked']}: the run stopped there; {NO_BYPASS}"
    if info.get("error"):
        out["error"] = info["error"]
    if first:
        out["notice"] = RISK
    return out


# ---------------------------------------------------------------- reading (app database first, then the plugin store)

def has_rows(conn) -> bool:
    return conn is not None and not c.table_problem(conn, "job_listings") and \
        conn.execute("SELECT 1 FROM job_listings LIMIT 1").fetchone() is not None


def _day(s) -> date | None:
    try:
        return datetime.fromisoformat(str(s)[:10]).date() if s else None
    except ValueError:
        return None


def _skill_re(term: str):
    return re.compile(r"(?<![\w+#&])" + re.escape(term) + r"(?![\w+#&])", re.I)


def view(conn, top: int = 15) -> dict:
    """Market view: totals, open count, open listings per company, weekly trend, top skills, honest notes.
    A listing is dated by posted_at, else first_seen; undated rows from a source's first run day are the
    bulk load of already open jobs and are left out of the trend. Reads only."""
    have = {r[1] for r in conn.execute('PRAGMA table_info("job_listings")')}
    cols = ", ".join(x if x in have else f"NULL AS {x}"
                     for x in ("source", "company", "title", "description", "status", "posted_at", "first_seen"))
    rows = conn.execute(f"SELECT {cols} FROM job_listings").fetchall()
    if not rows:
        return {"status": "no listings yet", "companies": [], "weekly": [], "skills": [], "notes": ["no listings yet"]}
    first_day: dict = {}
    for r in rows:
        d = _day(r["first_seen"])
        if d and (r["source"] not in first_day or d < first_day[r["source"]]):
            first_day[r["source"]] = d
    open_rows = [r for r in rows if r["status"] == "open"]
    companies = Counter((r["company"] or "company unknown") for r in open_rows)
    weekly: Counter = Counter()
    skipped, dated = 0, []
    for r in rows:
        d = _day(r["posted_at"])
        if d is None:
            d = _day(r["first_seen"])
            if d is not None and d == first_day.get(r["source"]):
                skipped += 1
                continue
        if d is not None:
            weekly[(d - timedelta(days=d.weekday())).isoformat()] += 1
            dated.append(d)
    skills: Counter = Counter()
    pats = [(t, _skill_re(t)) for t in SKILL_TERMS]
    described = 0
    for r in open_rows:
        text = f"{r['title'] or ''} {r['description'] or ''}"
        described += 1 if r["description"] else 0
        for t, p in pats:
            if p.search(text):
                skills[t] += 1
    window = (max(dated) - min(dated)).days + 1 if dated else 0
    notes = []
    if not dated:
        notes.append("no dated listings yet: the weekly trend needs at least one posting date or a second run day")
    elif window < SHORT_WINDOW_DAYS:
        notes.append(f"short window: only {window} day(s) of data, the weekly numbers show collection, not a market trend")
    if skipped:
        notes.append(f"{skipped} undated listing(s) from a source's first run day are left out of the trend "
                     "(bulk load of already open jobs)")
    if len(open_rows) - described:
        notes.append(f"skills are counted from titles and descriptions; {len(open_rows) - described} open "
                     "listing(s) have no description")
    return {"status": "ok", "total": len(rows), "open": len(open_rows), "companies_n": len(companies),
            "window_days": window, "companies": [{"company": k, "open": n} for k, n in companies.most_common(top)],
            "weekly": [{"week": w, "listings": n} for w, n in sorted(weekly.items())],
            "skills": [{"skill": s, "open": n} for s, n in skills.most_common(top)], "notes": notes}
