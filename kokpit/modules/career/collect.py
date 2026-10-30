"""Career collect job: enabled sources -> job_listings, then score every row against the profile.

Sources: job adapters named in ``[career] adapters``, only when ``[career] linkedin_guest`` is
true the LinkedIn guest search, and last the optional browser sources in ``[career] browser_sources``
(Indeed, Jooble; Playwright is imported only when one is enabled). A source that fails is a warning, never a crash; a source that returns
0 records is reported as such.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime

from ...config import Config
from . import adapters, browser, lifecycle, linkedin, profile, scoring, store


def remote_flag(value: str | None) -> bool | None:
    return {"remote": True, "onsite": False}.get(value or "")


def scorer(prof: dict):
    def run(row: dict) -> dict:
        return scoring.score(prof, {**row, "remote": remote_flag(row.get("remote"))})
    return run


def collect(cfg: Config, conn: sqlite3.Connection, linkedin_get=None, sleep=None, now: datetime | None = None,
            browser_launcher=None) -> tuple[int, dict, list[str]]:
    """Returns (rows, info, warnings) in the job contract. ``rows`` = records fetched this run."""
    prof = profile.load(cfg)
    warnings: list[str] = []
    per_source: dict[str, int] = {}
    records: list[dict] = []
    if not cfg.modules.get("jobs", False):
        return 0, {}, ["career module is off: set jobs = true under [modules]"]
    plan = adapters.plan(cfg)
    for name in adapters.missing(cfg):
        warnings.append(f"adapter {name}: enabled in kokpit.toml but not found (run `kokpit adapters`)")
    for name, fn in plan:
        try:
            got = fn()
        except Exception as exc:
            reason = str(exc) if isinstance(exc, adapters.AdapterError) else f"{type(exc).__name__}: {str(exc)[:120]}"
            warnings.append(f"{name}: {reason}")
            per_source[name] = 0
            continue
        per_source[name] = len(got)
        if not got:
            warnings.append(f"{name}: 0 records (check the source page)")
        records.extend(got)
    if cfg.career.get("linkedin_guest"):
        kw = {"get": linkedin_get}
        if sleep:
            kw["sleep"] = sleep
        got, warns = linkedin.search(prof, pages=cfg.career.get("linkedin_pages", 3), **kw)
        per_source[linkedin.SOURCE] = len(got)
        warnings.extend(warns)
        records.extend(got)
    now = now or datetime.now()
    for name in cfg.career.get("browser_ignored", []):
        warnings.append(f"browser: unknown source {name!r} in [career] browser_sources ignored (allowed: {', '.join(browser.SOURCES)})")
    if cfg.career.get("browser_sources"):
        got, warns, per = browser.search(cfg, prof, now, launcher=browser_launcher, sleep=sleep)
        per_source.update(per)
        warnings.extend(warns)
        records.extend(got)
    if not plan and not cfg.career.get("linkedin_guest") and not cfg.career.get("browser_sources"):
        warnings.append("no source enabled: set [career] adapters, linkedin_guest or browser_sources in kokpit.toml")
    res = store.upsert(conn, records, now.isoformat(timespec="seconds"))
    live = {n for n, c in per_source.items() if c > 0}
    life, lwarns = lifecycle.apply(conn, now, cfg.career.get("close_after_days", 3), live, check_get=linkedin_get,
                                   check=bool(cfg.career.get("linkedin_guest")), sleep=sleep or lifecycle.time.sleep)
    warnings.extend(lwarns)
    scored = store.set_scores(conn, scorer(prof))
    if profile.is_empty(prof):
        warnings.append("profile is empty: scores cannot judge anything yet (fill in target roles on the Career tab)")
    return len(records), {"sources": per_source, **res, **life, "scored": scored, **store.counts(conn)}, warnings
