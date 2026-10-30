"""Rule-based scoring and brain-driven matching. No model needed anywhere in this file.

Topic score: keyword hits in title (2 points each) and feed tags (1 point each), plus freshness
(3 points for today, falling to 0 at ``max_age_days``). Brain matching reads only thread titles and the
first part of their status line; note bodies never reach this module.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone

WORD = re.compile(r"[a-z0-9\u00c0-\u024f#][a-z0-9\u00c0-\u024f\-+.]{2,}", re.I)
STOP = frozenset("""about after again also and are because been before being but can could did does doing done for from
had has have here how into its just more most new not now off one only other our out over per project projects
same should some such than that the their them then there these they this those through too under until
upon use used very was were what when where which while who will with would you your yours work working
started start next last open done todo task tasks note notes thread threads status plan update updates
""".split())


def tokens(text: str) -> set[str]:
    return {w.lower().strip(".-") for w in WORD.findall(text or "") if w.lower().strip(".-") not in STOP and len(w.strip(".-")) >= 4}


def has_keyword(text: str, kw: str) -> bool:
    kw = kw.lower().strip()
    return bool(kw) and re.search(r"(?<![a-z0-9])" + re.escape(kw) + r"(?![a-z0-9])", text.lower()) is not None


def freshness(published: datetime | None, now: datetime, max_age_days: int) -> float:
    if published is None:
        return 1.0
    age = (now - published).total_seconds() / 86400
    if age < 0:
        age = 0
    return max(0.0, 3.0 * (1 - age / max_age_days))


def too_old(published: datetime | None, now: datetime, max_age_days: int) -> bool:
    return published is not None and (now - published).total_seconds() > max_age_days * 86400


def topic_score(item: dict, keywords: list[str], now: datetime, max_age_days: int) -> tuple[float, list[str]]:
    hits, score = [], 0.0
    tags = " ".join(item["tags"])
    for kw in keywords:
        t, g = has_keyword(item["title"], kw), has_keyword(tags, kw)
        if t or g:
            hits.append(kw)
            score += (2 if t else 0) + (1 if g else 0)
    return score + freshness(item["published"], now, max_age_days), hits


def brain_threads(data: dict) -> list[dict]:
    """From ``reader.read_vault`` output: title, project name, a title keyword set and a status keyword set. Nothing else."""
    out = []
    for t in data.get("threads", []):
        title = t.get("title") or ""
        src = (t.get("source") or "").rstrip("/").rsplit("/", 1)[-1]
        out.append({"title": title, "project": src or title,
                    "title_kw": tokens(title), "status_kw": tokens((t.get("status") or "")[:200]) - tokens(title)})
    return out


def project_match(item: dict, threads: list[dict]) -> tuple[str, str] | None:
    """(project, reason) for the first thread that clearly relates to the item, else None."""
    words = tokens(item["title"] + " " + " ".join(item["tags"]))
    for th in threads:
        common = sorted(words & th["title_kw"])
        if common:
            return th["project"], f"matches project {th['project']}: keyword {common[0]} in thread {th['title']}"
        common = sorted(words & th["status_kw"])
        if len(common) >= 2:
            return th["project"], f"matches project {th['project']}: keywords {', '.join(common[:2])} in thread {th['title']}"
    return None


def cut(text: str, n: int = 200) -> str:
    text = (text or "").strip()
    if len(text) <= n:
        return text
    return text[:n].rsplit(" ", 1)[0].rstrip(",;:.") + "..."


def now_utc() -> datetime:
    return datetime.now(timezone.utc)
