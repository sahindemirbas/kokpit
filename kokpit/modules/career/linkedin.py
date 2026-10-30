"""LinkedIn guest job search: public pages, no login. OFF by default (``[career] linkedin_guest``).

This reads the public guest endpoint that LinkedIn's own logged-out job pages use. LinkedIn's User
Agreement restricts automated access: use is at the user's own risk, it can break or be blocked at any
time, and Kokpit does not try to get around a block. Politeness: one request per page, a pause between
requests, the 7-day window, a hard cap on queries, and the first HTTP 429 (or 999) stops everything.
``fetch.get`` is the only network call; tests replace it.
"""
from __future__ import annotations

import html
import re
import time
import urllib.error
import urllib.parse

from ... import fetch

SEARCH = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
POSTING = "https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/"
SOURCE = "linkedin"
PAGE_SIZE = 25
MAX_QUERIES = 6
DELAY_S = 3.0
BLOCK_CODES = (429, 999)
CLOSED_MARKERS = ("closed-job", "No longer accepting applications")


class Blocked(RuntimeError):
    """LinkedIn answered 429/999: stop and report, never retry or work around."""


def _text(s: str | None) -> str | None:
    if not s:
        return None
    s = html.unescape(re.sub(r"<[^>]+>", " ", s))
    return re.sub(r"\s+", " ", s).strip() or None


def parse_cards(page: str) -> list[dict]:
    """Job records from one page of guest search results (HTML list items)."""
    out = []
    for card in re.split(r"<li\b", page)[1:]:
        m = re.search(r"jobPosting:(\d+)", card)
        title = _text((re.search(r'base-search-card__title[^>]*>(.*?)</h3>', card, re.S) or [None, None])[1])
        if not m or not title:
            continue
        company = _text((re.search(r'base-search-card__subtitle[^>]*>(.*?)</h4>', card, re.S) or [None, None])[1])
        loc = _text((re.search(r'job-search-card__location[^>]*>(.*?)</span>', card, re.S) or [None, None])[1])
        dt = re.search(r'<time[^>]*datetime="(\d{4}-\d{2}-\d{2})', card)
        out.append({"source": SOURCE, "external_id": m.group(1), "title": title, "company": company, "location": loc, "remote": "unknown",
                    "url": f"https://www.linkedin.com/jobs/view/{m.group(1)}", "posted_at": dt.group(1) if dt else None,
                    "description": None, "language": None})
    return out


def queries(profile: dict) -> list[tuple[str, str]]:
    """(keywords, location) pairs from the profile targets, capped at MAX_QUERIES."""
    roles = profile.get("target_roles") or []
    locs = profile.get("locations") or [""]
    pairs = [(r, loc) for r in roles for loc in locs]
    return pairs[:MAX_QUERIES]


def _get(url: str, get):
    try:
        return get(url)
    except urllib.error.HTTPError as exc:
        if exc.code in BLOCK_CODES:
            raise Blocked(f"HTTP {exc.code}") from None
        raise


def search(profile: dict, pages: int = 2, get=None, sleep=time.sleep, delay: float = DELAY_S) -> tuple[list[dict], list[str]]:
    """-> (records, warnings). Never raises on a block: it stops and says so in the warnings."""
    get = get or fetch.get
    pairs = queries(profile)
    if not pairs:
        return [], ["linkedin: no target roles in the profile, nothing to search"]
    records: dict[str, dict] = {}
    warnings: list[str] = []
    first = True
    try:
        for kw, loc in pairs:
            for page in range(pages):
                if not first:
                    sleep(delay)
                first = False
                params = {"keywords": kw, "location": loc, "f_TPR": "r604800", "start": page * PAGE_SIZE}
                if profile.get("remote_pref") == "remote":
                    params["f_WT"] = "2"
                cards = parse_cards(_get(f"{SEARCH}?{urllib.parse.urlencode({k: v for k, v in params.items() if v != ''})}", get))
                for c in cards:
                    if profile.get("remote_pref") == "remote":
                        c["remote"] = "remote"
                    records.setdefault(c["external_id"], c)
                if len(cards) < PAGE_SIZE:
                    break
    except Blocked as exc:
        warnings.append(f"linkedin: {exc}, stopped; try again later (Kokpit does not bypass blocks)")
    except Exception as exc:
        warnings.append(f"linkedin: {type(exc).__name__}: {str(exc)[:120]}")
    if not records and not warnings:
        warnings.append("linkedin: 0 records (no match, or the page layout changed)")
    return list(records.values()), warnings


def posting_state(external_id: str, get=None) -> str:
    """'open' | 'closed' | 'limit' | 'error', read from the guest posting page (never from search absence)."""
    get = get or fetch.get
    try:
        body = get(POSTING + urllib.parse.quote(str(external_id)))
    except urllib.error.HTTPError as exc:
        if exc.code in BLOCK_CODES:
            return "limit"
        return "closed" if exc.code in (404, 410) else "error"
    except Exception:
        return "error"
    return "closed" if any(m in body for m in CLOSED_MARKERS) else "open"
