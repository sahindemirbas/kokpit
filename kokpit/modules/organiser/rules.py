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


TASK = re.compile(r"^\s*[-*]\s*\[( |x|X)\]\s*(.+?)\s*$")
DUE = re.compile(r"due:\s*(20\d\d-\d\d-\d\d)", re.I)
ISO_DAY = re.compile(r"\b(20\d\d-\d\d-\d\d)\b")
HEAD = re.compile(r"^#{1,6}\s+(.*)$")


def _task_key(text: str) -> str:
    """Normalised task text for comparison: no due field, no links, no punctuation runs, lower case."""
    t = DUE.sub("", text)
    t = re.sub(r"\[\[([^\]|]+)(?:\|([^\]]+))?\]\]", lambda m: m.group(2) or m.group(1), t)
    t = re.sub(r"[^\w]+", " ", t.lower())
    return re.sub(r"\s+", " ", t).strip()


def _day(s: str) -> date | None:
    try:
        return date.fromisoformat(s)
    except ValueError:
        return None


def open_tasks(text: str) -> list[dict]:
    """Open checkbox lines with their due date and the ISO date found on the line or the nearest heading above.

    Completed lines ([x]) are skipped. Nothing is guessed: a task with no date anywhere has ``seen = None``.
    """
    out, heading_day = [], None
    for line in text.splitlines():
        h = HEAD.match(line)
        if h:
            m = ISO_DAY.search(h.group(1))
            heading_day = _day(m.group(1)) if m else None
            continue
        m = TASK.match(line)
        if not m or m.group(1).lower() == "x":
            continue
        body = m.group(2)
        due = DUE.search(body)
        rest = DUE.sub("", body)
        own = ISO_DAY.search(rest)
        seen = _day(own.group(1)) if own else heading_day
        label = re.sub(r"\s+", " ", rest.replace("()", "")).strip(" -")
        out.append({"text": label, "key": _task_key(body), "due": _day(due.group(1)) if due else None, "seen": seen})
    return out


def todo(todo_text: str, notes: list[dict], today: date, undated_days: int, todo_path: str = "To-Do.md") -> list[dict]:
    """Three To-Do checks, no model: overdue, undated and old, the same open task in more than one place."""
    out = []
    tasks = open_tasks(todo_text)
    for t in tasks:
        if not t["key"]:
            continue
        if t["due"] and t["due"] < today:
            n = (today - t["due"]).days
            out.append(dict(kind="todo", path=todo_path, target="", detail="overdue:" + t["key"][:80],
                            text=f"Task '{t['text'][:120]}' was due {t['due'].isoformat()} ({n} day(s) ago). Mark it done, drop it or give it a new date."))
        elif not t["due"] and t["seen"] and (today - t["seen"]).days >= undated_days:
            n = (today - t["seen"]).days
            out.append(dict(kind="todo", path=todo_path, target="", detail="undated-stale:" + t["key"][:80],
                            text=f"Task '{t['text'][:120]}' has no due date and has been open since {t['seen'].isoformat()} ({n} days). Give it a date or drop it."))
    where: dict[str, list[str]] = {}
    label: dict[str, str] = {}
    for t in tasks:
        if t["key"]:
            where.setdefault(t["key"], []).append(todo_path)
            label.setdefault(t["key"], t["text"])
    for n in notes:
        for t in open_tasks(n["body"]):
            if t["key"]:
                where.setdefault(t["key"], []).append(n["path"])
                label.setdefault(t["key"], t["text"])
    for key in sorted(where):
        places = where[key]
        if len(places) < 2:
            continue
        uniq = sorted(set(places), key=lambda p: (p != todo_path, p))
        others = uniq[1:] or uniq[:1]
        times = f"{len(places)} times" if len(uniq) == 1 else f"in {len(uniq)} places"
        out.append(dict(kind="todo", path=uniq[0], target=others[0], detail="duplicate-task:" + key[:80],
                        text=f"Open task '{label[key][:120]}' appears {times} ({', '.join(uniq)}). Keep one."))
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
