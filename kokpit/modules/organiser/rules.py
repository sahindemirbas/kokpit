"""Rule-based detectors for the night organiser. Pure functions over parsed notes; nothing here writes.

A note is a dict: path, title, aliases, fm (frontmatter dict), body, date (YYYY-MM-DD), links (set of lowercase targets).
A proposal is a dict: kind, path, target, detail, text.
"""
from __future__ import annotations

import re
from datetime import date, datetime

WAIT = re.compile(r"\b(?:waiting\s+(?:for|on)|in\s+attesa\s+di|aspetto|aspettando)\s+(.{3,80})", re.I)
DONE_WORDS = re.compile(r"\b(arrived|received|delivered|installed|approved|confirmed|completed|resolved|finished|"
                        r"arrivato|arrivata|ricevuto|ricevuta|installato|confermato|fatto|finito)\b", re.I)
DONE_STATUS = {"done", "resolved", "closed", "completed", "fatto"}
STOP = {"the", "and", "for", "from", "with", "that", "this", "will", "have", "della", "dello", "alla", "per", "con", "che", "una", "uno"}


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-zà-ÿ0-9]{4,}", text.lower()) if w not in STOP}


def categorise(notes: list[dict], types: list[str], folders: dict[str, str]) -> list[dict]:
    out = []
    for n in notes:
        t = str(n["fm"].get("type", "")).strip().lower()
        if not t:
            out.append(dict(kind="categorise", path=n["path"], target="", detail="no-type",
                            text=f"'{n['title']}' has no type. Add a type ({', '.join(types)})."))
            continue
        if t not in types:
            out.append(dict(kind="categorise", path=n["path"], target="", detail="unknown-type",
                            text=f"'{n['title']}' has type '{t}', which is not one of: {', '.join(types)}."))
            continue
        want = folders.get(t)
        top = n["path"].split("/")[0] if "/" in n["path"] else ""
        if want and top.lower() != want.lower():
            out.append(dict(kind="categorise", path=n["path"], target=want, detail="folder",
                            text=f"'{n['title']}' is a {t} but sits in '{top or 'the vault root'}'; the {t} folder is '{want}'."))
    return out


def link(notes: list[dict], max_per_note: int = 5) -> list[dict]:
    out = []
    names = []
    for n in notes:
        for nm in [n["title"], *n["aliases"]]:
            if len(nm) >= 4:
                names.append((nm, n))
    for a in notes:
        found = 0
        seen = set()
        for nm, b in names:
            if b["path"] == a["path"] or b["path"] in seen or found >= max_per_note:
                continue
            if b["links_key"] & a["links"]:
                continue
            if re.search(r"(?<!\w)" + re.escape(nm) + r"(?!\w)", a["body"], re.I):
                seen.add(b["path"])
                found += 1
                out.append(dict(kind="link", path=a["path"], target=b["path"], detail="mention",
                                text=f"'{a['title']}' mentions '{nm}' but does not link to [[{b['title']}]]."))
    return out


def conditions(notes: list[dict]) -> list[dict]:
    out = []
    for a in notes:
        for line in a["body"].splitlines():
            m = WAIT.search(line)
            if not m:
                continue
            need = _words(m.group(1))
            if not need:
                continue
            for b in notes:
                if b["path"] == a["path"] or not b["date"] or not a["date"] or b["date"] <= a["date"]:
                    continue
                have = _words(b["title"] + " " + b["body"])
                hit = len(need & have)
                if hit < min(2, len(need)):
                    continue
                if DONE_WORDS.search(b["body"]) or str(b["fm"].get("status", "")).lower() in DONE_STATUS:
                    cond = m.group(1).strip().rstrip(".;")[:60]
                    out.append(dict(kind="conditions", path=a["path"], target=b["path"], detail=cond.lower(),
                                    text=f"'{a['title']}' waits for '{cond}'; the later note '{b['title']}' ({b['date']}) may say it happened. Check, then add a dated status."))
    return out


def enrich(notes: list[dict], required: list[str], short: int) -> list[dict]:
    out = []
    for n in notes:
        if not n["has_fm"]:
            out.append(dict(kind="enrich", path=n["path"], target="", detail="no-frontmatter",
                            text=f"'{n['title']}' has no frontmatter block."))
            continue
        for f in required:
            v = str(n["fm"].get(f, "")).strip()
            if f == "summary" and v and len(v) < short:
                out.append(dict(kind="enrich", path=n["path"], target="", detail="short-summary",
                                text=f"'{n['title']}' has a very short summary ({len(v)} characters)."))
            elif not v:
                out.append(dict(kind="enrich", path=n["path"], target="", detail=f"missing-{f}",
                                text=f"'{n['title']}' has no '{f}' in its frontmatter."))
    return out


def sync(notes: list[dict], threads: list[dict], today: date, stale_days: int) -> list[dict]:
    out = []
    by: dict[str, list[dict]] = {}
    for n in notes:
        by.setdefault(n["title"].casefold(), []).append(n)
    for group in by.values():
        if len(group) > 1:
            paths = sorted(g["path"] for g in group)
            for p in paths:
                others = [x for x in paths if x != p]
                out.append(dict(kind="sync", path=p, target=others[0], detail="duplicate-title",
                                text=f"'{group[0]['title']}' is also the title of {', '.join(others)}."))
    for t in threads:
        try:
            d = datetime.strptime(t["date"], "%Y-%m-%d").date()
        except (ValueError, TypeError):
            continue
        age = (today - d).days
        if age >= stale_days:
            out.append(dict(kind="sync", path="Threads.md", target=t["title"], detail="stale-thread",
                            text=f"Active thread '{t['title']}' has not changed for {age} days (limit {stale_days}). Close it or update it."))
    return out
