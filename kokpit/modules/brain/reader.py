"""Read the brain vault (Markdown) into plain dicts. Never writes.

File conventions are in docs/brain-protocol.md. Parsing is deliberately
forgiving: a heading that does not match is skipped, not an error.

Threads.md
    ## Active Threads
    ### <title>
    Where we left off (<date>): <status text>
    Open: on you: <item>; on me: <item>; on <name>: <item>
    Source: [[notes/...]]

To-Do.md
    ## Open
    - [ ] task text (due: 2026-11-03)

Last-Session.md
    ## <timestamp> · <title> · <session id>
    <body>
"""
from __future__ import annotations

import re
from datetime import date
from pathlib import Path

LIMITS = {"Last-Session.md": 3000, "Threads.md": 8000}  # shared with the hook; change both

WIKILINK = re.compile(r"\[\[([^\]|]+)(?:\|([^\]]+))?\]\]")
ISO_DATE = re.compile(r"\b(20\d\d-\d\d-\d\d)\b")
DUE = re.compile(r"due:\s*(20\d\d-\d\d-\d\d)", re.I)
TASK = re.compile(r"^\s*[-*]\s*\[( |x|X)\]\s*(.+?)\s*$")
# "on you:" / "on me:" / "on Ada:"; Turkish reference vault uses Sende/Bende/Başkası
OWNER = re.compile(r"^\s*(?:on\s+(you|me|[\w .'-]+?)|(Sende|Bende|Başkası))\b[^:]*:\s*", re.I)
WAIT_WORDS = re.compile(r"\b(approve|approval|decide|decision|waiting on you|onay|karar)\b", re.I)

ACTIVE_HEADINGS = ("## Active Threads", "## Active threads", "## Aktif")
CLOSED_HEADINGS = ("## Closed Threads", "## Closed threads", "## Kapanan")
OPEN_MARKERS = ("Open:", "Açık:")
STATUS_MARKERS = ("Where we left off", "Nerede kaldık")
SOURCE_MARKERS = ("Source:", "Kaynak:")


def _read(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ""


def _clean(s: str, n: int | None = None) -> str:
    s = WIKILINK.sub(lambda m: m.group(2) or m.group(1).split("/")[-1], s)
    s = re.sub(r"\s+", " ", s).strip()
    if n and len(s) > n:
        s = s[: n - 1].rstrip() + "…"
    return s


def _split_section(text: str, start_markers, end_markers) -> str:
    for m in start_markers:
        if m in text:
            text = text.split(m, 1)[1]
            break
    for m in end_markers:
        if m in text:
            text = text.split(m, 1)[0]
    return text


def _cut_at(text: str, markers) -> tuple[str, str]:
    """Return (before, after) at the first marker found; after is '' if none."""
    best = None
    for m in markers:
        i = text.find(m)
        if i >= 0 and (best is None or i < best[0]):
            best = (i, m)
    if best is None:
        return text, ""
    i, m = best
    return text[:i], text[i + len(m):]


def parse_threads(text: str) -> list[dict]:
    active = _split_section(text, ACTIVE_HEADINGS, CLOSED_HEADINGS)
    out = []
    for block in re.split(r"^### ", active, flags=re.M)[1:]:
        title, _, body = block.partition("\n")
        body = body.strip()
        before_open, open_part = _cut_at(body, OPEN_MARKERS)
        open_part, _ = _cut_at(open_part, SOURCE_MARKERS)
        status, _ = _cut_at(before_open, SOURCE_MARKERS)
        for m in STATUS_MARKERS:
            status = status.replace(m, "", 1)
        status = re.sub(r"^\s*\(?([^):]*)\)?:\s*", "", status.strip(), count=1) if status.lstrip().startswith("(") else status
        status = status.strip().lstrip(":").strip()
        d = ISO_DATE.search(body)
        src = re.search(r"\[\[([^\]|]+)", body.split("Source:")[-1] if "Source:" in body else body.split("Kaynak:")[-1])
        opens = []
        for item in re.split(r";|\n", open_part):
            item = item.strip().rstrip(".")
            if len(item) < 3:
                continue
            who = OWNER.match(item)
            txt = item[who.end():] if who else item
            owner = ""
            if who:
                g = (who.group(1) or who.group(2) or "").strip().lower()
                owner = {"sende": "you", "bende": "me", "başkası": "someone"}.get(g, g)
            you = owner == "you" or (not owner and bool(WAIT_WORDS.search(item)))
            due = ISO_DATE.search(txt)
            opens.append({"text": _clean(txt, 260), "owner": owner, "on_you": you,
                          "due": due.group(1) if due else ""})
        out.append({
            "title": _clean(title),
            "date": d.group(1) if d else "",
            "status": _clean(status, 520),
            "open": opens,
            "source": src.group(1) if src and "[[" in body else "",
        })
    return out


def parse_todo(text: str, today: date | None = None) -> list[dict]:
    today = today or date.today()
    m = re.search(r"^## Open\b.*$", text, re.M)  # heading at line start, not a mention in prose
    section = text[m.end():] if m else text
    section = re.split(r"^## ", section, maxsplit=1, flags=re.M)[0]
    out = []
    for line in section.splitlines():
        m = TASK.match(line)
        if not m or m.group(1).lower() == "x":
            continue
        txt = m.group(2)
        due = DUE.search(txt)
        due_s = due.group(1) if due else ""
        clean = _clean(DUE.sub("", txt).replace("()", "").strip(" -"))
        days = None
        if due_s:
            try:
                days = (date.fromisoformat(due_s) - today).days
            except ValueError:
                due_s = ""
        out.append({"text": clean, "due": due_s, "days_left": days, "overdue": days is not None and days < 0})
    out.sort(key=lambda t: (t["due"] == "", t["due"]))
    return out


def parse_sessions(text: str, limit: int = 8) -> list[dict]:
    text = text.split("## Earlier sessions", 1)[0].split("## Önceki oturumlar", 1)[0]
    out = []
    for block in re.split(r"^## ", text, flags=re.M)[1:]:
        head, _, body = block.partition("\n")
        parts = [p.strip() for p in head.split("·")]
        if len(parts) < 2:
            continue
        out.append({"at": parts[0], "title": _clean(parts[1]), "session": parts[2] if len(parts) > 2 else "",
                    "text": _clean(body, 300)})
    return out[:limit]


def upcoming(threads: list[dict], todo: list[dict], today: date | None = None, horizon: int = 120) -> list[dict]:
    """Dates that are plans: due tags on tasks and ISO dates inside open items. Logs are not scanned."""
    today = today or date.today()
    hits = []
    for t in todo:
        if t["due"]:
            hits.append({"date": t["due"], "text": t["text"], "from": "To-Do"})
    for th in threads:
        for o in th["open"]:
            if o["due"]:
                hits.append({"date": o["due"], "text": o["text"], "from": th["title"]})
    out = []
    for h in hits:
        try:
            days = (date.fromisoformat(h["date"]) - today).days
        except ValueError:
            continue
        if -1 <= days <= horizon:
            out.append({**h, "days_left": days})
    out.sort(key=lambda h: h["date"])
    return out


def file_health(vault: Path) -> list[dict]:
    out = []
    for name, limit in LIMITS.items():
        p = vault / name
        size = len(_read(p))
        out.append({"file": name, "chars": size, "limit": limit, "exists": p.exists(), "over": size > limit})
    return out


def read_vault(vault: Path, today: date | None = None) -> dict:
    vault = Path(vault)
    threads = parse_threads(_read(vault / "Threads.md"))
    todo = parse_todo(_read(vault / "To-Do.md"), today)
    sessions = parse_sessions(_read(vault / "Last-Session.md"))
    on_you = [{"thread": t["title"], **o} for t in threads for o in t["open"] if o["on_you"]]
    on_me = [{"thread": t["title"], **o} for t in threads for o in t["open"] if o["owner"] == "me"]
    others = [{"thread": t["title"], **o} for t in threads for o in t["open"]
              if not o["on_you"] and o["owner"] not in ("", "me")]
    inbox = sorted(p.name for p in (vault / "inbox").glob("*.md")) if (vault / "inbox").exists() else []
    return {
        "vault": str(vault),
        "exists": vault.exists(),
        "threads": threads,
        "todo": todo,
        "sessions": sessions,
        "lanes": {"on_you": on_you, "on_me": on_me, "others": others},
        "upcoming": upcoming(threads, todo, today),
        "inbox": inbox,
        "files": file_health(vault),
    }
