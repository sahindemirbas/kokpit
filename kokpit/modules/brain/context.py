"""Build the compact context block an agent receives on every message. Never writes.

This is Kokpit's own implementation of the context-hook contract described in
docs/brain-protocol.md (contract and conventions: Avenox Beyin, MIT; see
THIRD_PARTY_NOTICES.md). No code is copied from Beyin.

The block has four parts, each optional and each capped so the total stays
under ``max_chars``:

1. Active threads: title, where we left off, open items with their owner.
2. Last session card (newest only).
3. Upcoming dates (``due:`` tags and dated open items; logs are not scanned).
4. Notes under ``notes/`` whose title, aliases or heading words overlap with the
   user's message. Plain word overlap, no model, no network.

A one-line close-out reminder is appended when the message sounds like a piece
of work just finished (``done``, ``finished``, ``bitti``, ``tamam`` ...). The
agent decides what to do with it; the hook itself never writes.
"""
from __future__ import annotations

import re
from datetime import date
from pathlib import Path

from .reader import read_vault

DEFAULT_MAX_CHARS = 3500
NOTE_LIMIT = 3
NOTE_EXCERPT = 420
MIN_WORD = 4
STOP = {
    "this", "that", "with", "from", "have", "what", "when", "where", "which", "about", "into",
    "your", "then", "than", "them", "they", "will", "would", "could", "should", "there", "here",
    "bunu", "şunu", "için", "gibi", "daha", "sonra", "önce", "nasıl", "neden", "şimdi", "bir",
}
DONE_WORDS = re.compile(
    r"\b(done|finished|shipped|merged|deployed|it works|all green|bitti|tamam(dır|landı)?|"
    r"çalışıyor|yayınlandı|push(ed|landı)?)\b", re.I)
FRONTMATTER = re.compile(r"\A---\n(.*?)\n---\n", re.S)
WORD = re.compile(r"[\w'’-]+", re.U)


def _words(text: str) -> set[str]:
    out = set()
    for w in WORD.findall(text.lower()):
        w = w.strip("'’-")
        if len(w) >= MIN_WORD and w not in STOP and not w.isdigit():
            out.add(w)
    return out


def _note_meta(path: Path, vault: Path) -> dict | None:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    fm = FRONTMATTER.match(text)
    body = text[fm.end():] if fm else text
    aliases = ""
    if fm:
        m = re.search(r"^aliases:\s*\[?(.*?)\]?\s*$", fm.group(1), re.M)
        aliases = m.group(1) if m else ""
    title_m = re.search(r"^#\s+(.+)$", body, re.M)
    title = title_m.group(1).strip() if title_m else path.stem
    headings = " ".join(re.findall(r"^#{2,3}\s+(.+)$", body, re.M))
    key = _words(f"{path.stem} {title} {aliases}")
    weak = _words(headings)
    excerpt = re.sub(r"<!--.*?-->", "", body, flags=re.S)
    excerpt = re.sub(r"^#\s+.+$", "", excerpt, count=1, flags=re.M)
    excerpt = re.sub(r"\s+", " ", excerpt).strip()
    return {"rel": path.relative_to(vault).as_posix(), "title": title, "key": key,
            "weak": weak, "excerpt": excerpt[:NOTE_EXCERPT] + ("…" if len(excerpt) > NOTE_EXCERPT else "")}


def match_notes(vault: Path, message: str, limit: int = NOTE_LIMIT) -> list[dict]:
    """Notes whose name/title/aliases share words with the message. Title hits weigh 3, heading hits 1."""
    words = _words(message)
    if not words:
        return []
    notes_dir = Path(vault) / "notes"
    scored = []
    for p in sorted(notes_dir.rglob("*.md")) if notes_dir.is_dir() else []:
        meta = _note_meta(p, Path(vault))
        if not meta:
            continue
        score = 3 * len(words & meta["key"]) + len(words & meta["weak"])
        if score:
            scored.append((score, meta))
    scored.sort(key=lambda s: (-s[0], s[1]["rel"]))
    return [{"path": m["rel"], "title": m["title"], "score": s, "excerpt": m["excerpt"]} for s, m in scored[:limit]]


def _fit(lines: list[str], budget: int) -> list[str]:
    out, used = [], 0
    for line in lines:
        if used + len(line) + 1 > budget:
            break
        out.append(line)
        used += len(line) + 1
    return out


def build_context(vault: Path, message: str = "", max_chars: int = DEFAULT_MAX_CHARS,
                  today: date | None = None, label: str = "Kokpit brain") -> str:
    """Return the context block as text, or '' when the vault has nothing to say."""
    vault = Path(vault)
    data = read_vault(vault, today)
    parts: list[str] = []

    threads = data.get("threads", [])
    if threads:
        lines = ["## Active threads"]
        for t in threads:
            lines.append(f"- {t['title']}: {t['status']}" if t["status"] else f"- {t['title']}")
            for o in t["open"]:
                who = {"you": "on you", "me": "on me"}.get(o["owner"], f"on {o['owner']}" if o["owner"] else "open")
                due = f" (due {o['due']})" if o["due"] and o["due"] not in o["text"] else ""
                lines.append(f"  - {who}: {o['text']}{due}")
        parts.append("\n".join(_fit(lines, max_chars // 2)))

    sessions = data.get("sessions", [])
    if sessions:
        s = sessions[0]
        parts.append(f"## Last session\n{s['at']} · {s['title']}\n{s['text']}")

    up = data.get("upcoming", [])
    if up:
        lines = ["## Upcoming"]
        for u in up[:6]:
            lines.append(f"- {u['date']} ({u['days_left']}d): {u['text']} [{u['from']}]")
        parts.append("\n".join(lines))

    notes = match_notes(vault, message) if message else []
    if notes:
        lines = ["## Related notes"]
        for n in notes:
            lines.append(f"- {n['path']} · {n['title']}\n  {n['excerpt']}")
        parts.append("\n".join(lines))

    if not parts:
        return ""
    if message and DONE_WORDS.search(message):
        parts.append("Reminder: if a piece of work just finished, update Last-Session.md and the thread "
                     "(see docs/brain-protocol.md). The hook writes nothing itself.")

    text = f"[{label} · read-only context]\n" + "\n\n".join(parts)
    if len(text) > max_chars:
        text = text[: max_chars - 1].rstrip() + "…"
    return text
