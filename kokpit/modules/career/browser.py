"""Browser sources: Indeed and Jooble search pages read through a local headless Chromium.

OFF by default (``[career] browser_sources = []``). Playwright is an optional dependency: it is
imported only inside ``_launch``, so with the sources off, or Playwright missing, nothing is imported
and the career run gets a single warning. Nothing is sent to the sites except the search itself
(the profile's target roles and places); no account, no login, no form is filled in.

Politeness and blocks: one page at a time, a pause between pages, a cap on queries and on posting
pages, and ``browser_max_fails`` consecutive errors or blocks stop that source for the run. Kokpit
does not try to get around a block. On Indeed a blocked posting page stops the source (cards already
read from that results page are kept as summaries); on Jooble three blocked posting pages switch the
rest of the run to card summaries.

Records use the job contract (``adapters.normalise``); listings that another source already has
are merged by ``store.upsert`` on its normalised company|title key, never stored twice.
Parsing is pure (``parse_*``, ``trim_description``, ``looks_blocked``, ``parse_age``) and is tested
without a browser; ``search`` takes an injectable ``launcher`` and ``sleep``.
"""
from __future__ import annotations

import importlib.util
import re
import shutil
import time
import urllib.parse
from datetime import date, datetime, timedelta

from ...config import Config
from . import adapters

SOURCES = ("indeed", "jooble")
NAMES = {"indeed": "Indeed", "jooble": "Jooble"}
BROWSERS = ("chromium", "chromium-browser", "google-chrome")
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
MAX_QUERIES = 6            # (role, place) pairs per source
MAX_REMOTE_QUERIES = 2     # extra "<role> remote" queries on Indeed
JOOBLE_DETAIL_BLOCKS = 3   # blocked Jooble posting pages before the run keeps card summaries
TIMEOUT_MS = 45000
SETTLE_MS = {"search": 2500, "detail": 1500}

# Per-country sites. Add a row to support another country.
SITES = {
    "IT": {"indeed": "it.indeed.com", "jooble": "it.jooble.org", "locale": "it-IT", "lang": "it", "name": "Italia"},
    "DE": {"indeed": "de.indeed.com", "jooble": "de.jooble.org", "locale": "de-DE", "lang": "de", "name": "Deutschland"},
    "FR": {"indeed": "fr.indeed.com", "jooble": "fr.jooble.org", "locale": "fr-FR", "lang": "fr", "name": "France"},
    "ES": {"indeed": "es.indeed.com", "jooble": "es.jooble.org", "locale": "es-ES", "lang": "es", "name": "Espana"},
    "NL": {"indeed": "nl.indeed.com", "jooble": "nl.jooble.org", "locale": "nl-NL", "lang": "nl", "name": "Nederland"},
    "GB": {"indeed": "uk.indeed.com", "jooble": "uk.jooble.org", "locale": "en-GB", "lang": "en", "name": "United Kingdom"},
    "US": {"indeed": "www.indeed.com", "jooble": "jooble.org", "locale": "en-US", "lang": "en", "name": "United States"},
    "TR": {"indeed": "tr.indeed.com", "jooble": "tr.jooble.org", "locale": "tr-TR", "lang": "tr", "name": "Turkey"},
}

# Page selectors and extraction scripts: when a site changes its markup, fix it here.
INDEED_CARD = "div.job_seen_beacon"
INDEED_TITLE_LINK = "a.jcs-JobTitle"
INDEED_DESCRIPTION = "#jobDescriptionText"
JOOBLE_CARD = "[data-test-name=_jobCard]"
JOOBLE_LINK = "a.job_card_link"
INDEED_CARDS_JS = """() => [...document.querySelectorAll('%s')].map(c => {
  const a = c.querySelector('%s');
  const t = el => el ? (el.innerText || el.textContent || '').trim() : '';
  const q = s => t(c.querySelector(s));
  const named = a ? a.querySelector('span[title]') : null;
  return {jk: a ? a.dataset.jk : '', title: q('h2.jobTitle') || (named ? named.title : '') || t(a), company: q('[data-testid=company-name]'),
          location: q('[data-testid=text-location]'), salary: q('[data-testid*=salary-snippet]'),
          age: q('[data-testid=myJobsStateDate]'), snippet: q('div.job-snippet') || q('[data-testid=jobsnippet_footer]')};
})""" % (INDEED_CARD, INDEED_TITLE_LINK)
JOOBLE_CARDS_JS = """() => [...document.querySelectorAll('%s')].map(c => {
  const a = c.querySelector('%s');
  return {id: c.id || '', title: a ? a.innerText.trim() : '', href: a ? a.href : '', text: c.innerText || ''};
})""" % (JOOBLE_CARD, JOOBLE_LINK)
INDEED_DESCRIPTION_JS = "() => { const e = document.querySelector('%s'); return e ? (e.innerText || e.textContent || '') : ''; }" % INDEED_DESCRIPTION
BODY_JS = "() => (document.body ? document.body.innerText : '')"

BLOCK_MARKERS = ("captcha", "verify you are human", "access denied", "just a moment", "security check",
                 "additional verification", "unusual traffic", "non sei un robot", "verifica di sicurezza",
                 "verifica aggiuntiva", "ulteriore verifica", "ci siamo quasi", "ray id")
REMOTE_MARKERS = ("remote", "remoto", "telelavoro", "home office", "homeoffice", "teletravail", "thuiswerk")
HYBRID_MARKERS = ("hybrid", "ibrido", "hybride", "hibrido")
JOOBLE_REPORT = ("Segnala", "Report", "Melden", "Signaler", "Denunciar", "Rapporteren")
JOOBLE_TAGS = {
    "Consigliato": None, "Recommended": None,
    "Tempo pieno": "full time", "Full-time": "full time", "Part-time": "part time", "Tempo parziale": "part time",
    "Impiego permanente": "permanent", "Permanent": "permanent", "Remoto": "remote", "Remote": "remote",
    "Da remoto": "remote", "Stage/Tirocinio": "internship", "Internship": "internship",
    "Orario flessibile": "flexible hours", "Flexible schedule": "flexible hours",
    "Contratto a tempo determinato": "fixed term", "Temporary": "fixed term", "Freelance": "freelance",
}
CURRENCY = ("\u20ac", "\u00a3", "$")
TAIL_MARKERS = ("Offerte di lavoro simili", "Lavori simili", "Similar jobs", "Condividi", "Share", "Segnala", "Report",
                "Candidati ora", "Apply now", "Iscriviti", "Sign up")
APPLY_LINE = re.compile(r"^(Clicca qui per candidarti|Click here to apply)[^\n]*\n", re.M)
NAV_NOISE = re.compile(r"^(Attiva gli avvisi.*|Registrati|Accedi|Qualifica o parola chiave|Luogo o CAP|Come funziona\?|"
                       r"Sei un.azienda\?.*|Cerca|Cerca lavoro|Preferiti|Sign in|Log in|Register|Job title or keyword|"
                       r"Location|Search|Find jobs|Saved jobs|Create alert.*|\d+)$", re.I)

AGE_TODAY = re.compile(r"\b(today|just posted|just now|oggi|appena pubblicato|ora|hours? ago|\d+\s*or[ae] fa)\b")
AGE_YESTERDAY = re.compile(r"\b(yesterday|ieri)\b")
AGE_DAYS = re.compile(r"(\d+)\+?\s*(days?|giorn[oi])\b")
AGE_WEEKS = re.compile(r"(\d+)\+?\s*(weeks?|settiman[ae])\b")
AGE_MONTHS = re.compile(r"(\d+)\+?\s*(months?|mes[ei])\b")


class Blocked(RuntimeError):
    """The site showed a block or verification page: stop and report, never work around."""


class BrowserUnavailable(RuntimeError):
    """Playwright or a Chromium binary is missing."""


# ---------------------------------------------------------------- pure parsing

def _clean(s) -> str | None:
    s = " ".join(str(s or "").replace("\xa0", " ").split())
    return s or None


def parse_age(text: str | None, today: date, lang: str = "en") -> str | None:
    """Relative age ('3 days ago', '30+ days ago', 'oggi', '2 settimane fa') -> YYYY-MM-DD, or None.

    English and Italian phrases are always recognised; ``lang`` is kept for future packs.
    Unknown text gives None: a date is never guessed.
    """
    t = " ".join(str(text or "").lower().split())
    if not t:
        return None
    for pat, mult in ((AGE_DAYS, 1), (AGE_WEEKS, 7), (AGE_MONTHS, 30)):
        m = pat.search(t)
        if m:
            return (today - timedelta(days=mult * int(m.group(1)))).isoformat()
    if AGE_YESTERDAY.search(t):
        return (today - timedelta(days=1)).isoformat()
    if AGE_TODAY.search(t):
        return today.isoformat()
    return None


def looks_blocked(title: str | None, body: str | None) -> bool:
    low = f"{title or ''} {(body or '')[:600]}".lower()
    return any(x in low for x in BLOCK_MARKERS)


def remote_of(*texts: str | None) -> str:
    low = " ".join(t or "" for t in texts).lower()
    if any(m in low for m in HYBRID_MARKERS):
        return "hybrid"
    if any(m in low for m in REMOTE_MARKERS):
        return "remote"
    return "unknown"


def parse_indeed_cards(cards: list[dict], site: dict, today: date) -> list[dict]:
    """Records from the Indeed card script output; cards without a job key or a title are dropped."""
    out = []
    for c in cards or []:
        jk, title = _clean(c.get("jk")), _clean(c.get("title"))
        if not jk or not title:
            continue
        loc = _clean(c.get("location"))
        out.append({"source": "indeed", "external_id": jk, "title": title, "company": _clean(c.get("company")),
                    "location": loc, "remote": remote_of(loc), "url": f"https://{site['indeed']}/viewjob?jk={urllib.parse.quote(jk)}",
                    "posted_at": parse_age(c.get("age"), today, site.get("lang", "en")), "salary": _clean(c.get("salary")),
                    "description": _clean(c.get("snippet")), "language": None})
    return out


def parse_jooble_card(text: str | None, title: str | None, today: date, lang: str = "en") -> dict:
    """Fields from the visible text of one Jooble card.

    Layout: title, [salary], summary, tags, a report link, company, place, age. When the report line is
    missing the company, place and age are unknown (None), never guessed.
    """
    lines = [l for l in (_clean(x) for x in str(text or "").split("\n")) if l]
    title = _clean(title)
    out = {"title": title, "company": None, "location": None, "posted_at": None, "salary": None, "snippet": "", "tags": [], "remote": "unknown"}
    cut = next((i for i, l in enumerate(lines) if l in JOOBLE_REPORT), None)
    head = lines
    if cut is not None:
        tail = lines[cut + 1:]
        out["company"] = tail[0] if len(tail) > 0 else None
        out["location"] = tail[1] if len(tail) > 1 else None
        out["posted_at"] = parse_age(tail[2], today, lang) if len(tail) > 2 else None
        head = lines[:cut]
    out["tags"] = [JOOBLE_TAGS[l] for l in head if JOOBLE_TAGS.get(l)]
    out["salary"] = next((l for l in head if any(s in l for s in CURRENCY) and len(l) < 40), None)
    body = [l for l in head if l != title and l not in JOOBLE_TAGS and l != out["salary"]]
    out["snippet"] = " ".join(body)
    if out["tags"]:
        out["snippet"] = (out["snippet"] + "\nType: " + ", ".join(out["tags"])).strip()
    out["remote"] = "remote" if "remote" in out["tags"] else remote_of(out["location"])
    return out


def trim_description(text: str | None, title: str | None) -> str:
    """Posting page text without the site menu before the title, the tail after it and known nav lines."""
    t = str(text or "").replace("\xa0", " ")
    m = APPLY_LINE.search(t)
    if m:
        t = t[m.end():]
    if title:
        i = t.find(title)
        if 0 <= i < 1500:
            t = t[i:]
    for marker in TAIL_MARKERS:
        k = t.find("\n" + marker)
        if k > 200:
            t = t[:k]
    t = "\n".join(l for l in t.split("\n") if not NAV_NOISE.match(l.strip()))
    return re.sub(r"\n{3,}", "\n\n", t).strip()


# ---------------------------------------------------------------- queries and urls

def site_for(cfg: Config) -> tuple[dict | None, str | None]:
    """-> (site, warning). Unknown or empty country: no site and one warning."""
    cc = (cfg.place.get("country") or "").upper()
    site = SITES.get(cc)
    if site:
        return site, None
    return None, f"browser: no Indeed/Jooble site known for country {cc or '(not set)'}; add it to SITES"


def queries(cfg: Config, prof: dict, site: dict, name: str) -> list[tuple[str, str]]:
    roles = prof.get("target_roles") or []
    places = prof.get("locations") or [cfg.place.get("city") or site["name"]]
    pairs = [(r, p) for r in roles for p in places][:MAX_QUERIES]
    if name == "indeed" and prof.get("remote_pref", "any") in ("remote", "any"):
        pairs += [(f"{r} remote", site["name"]) for r in roles[:MAX_REMOTE_QUERIES]]
    return pairs


def search_url(name: str, site: dict, kw: str, place: str, page_no: int) -> str:
    if name == "indeed":
        q = {"q": kw, "l": place, "sort": "date", "start": page_no * 10}
        return f"https://{site['indeed']}/jobs?{urllib.parse.urlencode(q)}"
    q: dict = {"ukw": kw, "rgns": place}
    if page_no:
        q["p"] = page_no + 1
    return f"https://{site['jooble']}/SearchResult?{urllib.parse.urlencode(q)}"


# ---------------------------------------------------------------- browser layer

def playwright_installed() -> bool:
    """True when the playwright package can be found; it is not imported."""
    try:
        return importlib.util.find_spec("playwright") is not None
    except (ImportError, ValueError):
        return False


def executable(cfg: Config) -> str | None:
    """The configured browser path, or the first of BROWSERS on PATH; None when nothing is found."""
    want = (cfg.career.get("browser_executable") or "").strip()
    if want:
        return shutil.which(want)
    for name in BROWSERS:
        found = shutil.which(name)
        if found:
            return found
    return None


def status(cfg: Config) -> str:
    """off | playwright missing | browser missing | on (installation only, no network)."""
    if not cfg.career.get("browser_sources"):
        return "off"
    if not playwright_installed():
        return "playwright missing"
    if not executable(cfg):
        return "browser missing"
    return "on"


def _launch(cfg: Config, locale: str):
    """-> (page, close). The only place that imports Playwright."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise BrowserUnavailable("playwright is not installed: pip install playwright") from None
    exe = executable(cfg)
    if not exe:
        raise BrowserUnavailable("no Chromium found on PATH (chromium, chromium-browser, google-chrome); "
                                 "install one or set [career] browser_executable")
    pw = sync_playwright().start()
    try:
        browser = pw.chromium.launch(executable_path=exe, headless=True, args=["--no-sandbox"])
        ctx = browser.new_context(user_agent=UA, locale=locale, viewport={"width": 1366, "height": 900})
        page = ctx.new_page()
    except Exception:
        pw.stop()
        raise

    def close():
        try:
            browser.close()
        finally:
            pw.stop()
    return page, close


def _open(page, url: str, settle: str) -> None:
    page.goto(url, timeout=TIMEOUT_MS, wait_until="domcontentloaded")
    wait = getattr(page, "wait_for_timeout", None)
    if wait:
        wait(SETTLE_MS[settle])


def _search_page(page, name: str, url: str, site: dict, today: date) -> list[dict]:
    _open(page, url, "search")
    if name == "indeed":
        cards = page.evaluate(INDEED_CARDS_JS) or []
        if not cards and looks_blocked(page.title(), page.evaluate(BODY_JS)):
            raise Blocked(page.title() or "verification page")
        return parse_indeed_cards(cards, site, today)
    cards = page.evaluate(JOOBLE_CARDS_JS) or []
    if not cards and looks_blocked(page.title(), page.evaluate(BODY_JS)):
        raise Blocked(page.title() or "verification page")
    out = []
    for c in cards:
        f = parse_jooble_card(c.get("text"), c.get("title"), today, site.get("lang", "en"))
        ext = _clean(c.get("id"))
        if not ext or not f["title"]:
            continue
        out.append({"source": "jooble", "external_id": ext, "title": f["title"], "company": f["company"],
                    "location": f["location"], "remote": f["remote"], "url": _clean(c.get("href")), "posted_at": f["posted_at"],
                    "salary": f["salary"], "description": f["snippet"] or None, "language": None})
    return out


def _detail(page, name: str, rec: dict) -> str:
    """Full posting text; raises Blocked on a verification page, '' when nothing is found."""
    _open(page, rec["url"], "detail")
    if name == "indeed":
        text = page.evaluate(INDEED_DESCRIPTION_JS) or ""
        if not text.strip():
            if looks_blocked(page.title(), page.evaluate(BODY_JS)):
                raise Blocked(page.title() or "verification page")
            return ""
        return re.sub(r"\n{3,}", "\n\n", text).strip()
    body = page.evaluate(BODY_JS) or ""
    if looks_blocked(page.title(), body):
        raise Blocked(page.title() or "verification page")
    return trim_description(body, rec["title"])


def _run_source(cfg: Config, prof: dict, page, name: str, site: dict, today: date, sleep) -> tuple[list[dict], list[str]]:
    c = cfg.career
    pages, delay, max_fails = c.get("browser_pages", 2), c.get("browser_delay_s", 2.5), c.get("browser_max_fails", 2)
    detail_on, detail_cap = bool(c.get("browser_detail", True)), c.get("browser_max_detail", 20)
    found: dict[str, dict] = {}
    warnings: list[str] = []
    fails, detail_blocks, details, first = 0, 0, 0, True
    stopped, last_error = False, ""


    def pause(mult=1.0):
        nonlocal first
        if not first:
            sleep(delay * mult)
        first = False

    for kw, place in queries(cfg, prof, site, name):
        for page_no in range(pages):
            pause()
            try:
                cards = _search_page(page, name, search_url(name, site, kw, place, page_no), site, today)
                fails = 0
            except Exception as exc:
                fails += 1
                last_error = str(exc)[:80]
                if fails >= max_fails:
                    warnings.append(f"{name}: stopped after {fails} consecutive errors ({str(exc)[:80]})")
                    stopped = True
                    break
                sleep(delay * 2)
                continue
            for rec in cards:
                if rec["external_id"] in found:
                    continue
                if not stopped and detail_on and details < detail_cap and rec.get("url") and not (name == "jooble" and detail_blocks >= JOOBLE_DETAIL_BLOCKS):
                    pause()
                    details += 1
                    try:
                        full = _detail(page, name, rec)
                        if len(full) > len(rec.get("description") or ""):
                            rec["description"] = full
                    except Blocked as exc:
                        if name == "indeed":
                            warnings.append(f"indeed: stopped, posting page blocked ({str(exc)[:80]})")
                            stopped = True
                        else:
                            detail_blocks += 1
                            if detail_blocks == JOOBLE_DETAIL_BLOCKS:
                                warnings.append(f"jooble: posting pages blocked {detail_blocks} times, card summaries only for the rest of this run")
                    except Exception:
                        pass
                found[rec["external_id"]] = rec  # after a stop the cards already on this page are kept as summaries
            if stopped or not cards:
                break
        if stopped:
            break
    records = [n for n in (adapters.normalise(r, name) for r in found.values()) if n]
    if not records and not warnings:
        why = f"last error: {last_error}" if last_error else "no match, or the page layout changed"
        warnings.append(f"{name}: 0 records ({why})")
    return records, warnings


def search(cfg: Config, prof: dict, now: datetime | None = None, launcher=None, sleep=None) -> tuple[list[dict], list[str], dict]:
    """-> (records, warnings, per_source). Never raises: every failure becomes a warning."""
    names = [n for n in (cfg.career.get("browser_sources") or []) if n in SOURCES]
    warnings: list[str] = []
    if not names:
        return [], warnings, {}
    per_source = {n: 0 for n in names}
    site, why = site_for(cfg)
    if not site:
        return [], warnings + [why or "browser: no site for this country"], per_source
    if not prof.get("target_roles"):
        return [], warnings + ["browser: profile has no target roles, nothing to search"], per_source
    today = (now or datetime.now()).date()
    sleep = sleep or time.sleep
    try:
        page, close = (launcher or _launch)(cfg, site["locale"])
    except (BrowserUnavailable, ImportError) as exc:
        msg = str(exc) if isinstance(exc, BrowserUnavailable) else "playwright is not installed: pip install playwright"
        return [], warnings + [f"browser: {msg}; {', '.join(names)} skipped"], per_source
    except Exception as exc:
        return [], warnings + [f"browser: could not start ({type(exc).__name__}: {str(exc)[:120]})"], per_source
    records: list[dict] = []
    try:
        for name in names:
            try:
                got, warns = _run_source(cfg, prof, page, name, site, today, sleep)
            except Exception as exc:
                got, warns = [], [f"{name}: {type(exc).__name__}: {str(exc)[:120]}"]
            per_source[name] = len(got)
            records.extend(got)
            warnings.extend(warns)
    finally:
        try:
            close()
        except Exception:
            pass
    return records, warnings, per_source
