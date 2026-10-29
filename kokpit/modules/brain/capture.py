"""Quick capture: the only write the web app performs on the vault.

- a note becomes a new file in ``inbox/`` (never overwrites);
- a task becomes one checkbox line appended under ``## Open`` in ``To-Do.md``.
Nothing is rewritten or deleted.
"""
from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

MAX_CHARS = 2000


def _slug(s: str) -> str:
    s = re.sub(r"[^\w\s-]", "", s, flags=re.U).strip().lower()
    s = re.sub(r"[\s_-]+", "-", s)
    return s[:48] or "note"


def capture_note(vault: Path, text: str, now: datetime | None = None) -> Path:
    text = text.strip()
    if not text or len(text) > MAX_CHARS:
        raise ValueError("note must be 1-2000 characters")
    now = now or datetime.now()
    inbox = Path(vault) / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    first = text.splitlines()[0]
    base = f"{now:%Y-%m-%d-%H%M}-{_slug(first)}"
    p = inbox / f"{base}.md"
    n = 1
    while p.exists():
        n += 1
        p = inbox / f"{base}-{n}.md"
    p.write_text(f"---\ncaptured: {now.isoformat(timespec='seconds')}\nsource: kokpit quick capture\n---\n{text}\n",
                 encoding="utf-8")
    return p


def capture_task(vault: Path, text: str, due: str = "") -> str:
    text = " ".join(text.split())
    if not text or len(text) > 300:
        raise ValueError("task must be 1-300 characters")
    if due and not re.fullmatch(r"20\d\d-\d\d-\d\d", due):
        raise ValueError("due must be YYYY-MM-DD")
    line = f"- [ ] {text}" + (f" (due: {due})" if due else "")
    p = Path(vault) / "To-Do.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    body = p.read_text(encoding="utf-8") if p.exists() else "# To-Do\n\n## Open\n"
    h = re.search(r"^## Open\b.*$", body, re.M)
    if not h:
        body = body.rstrip("\n") + "\n\n## Open\n"
        h = re.search(r"^## Open\b.*$", body, re.M)
    assert h is not None
    head, tail = body[: h.end()], body[h.end():]
    # insert before the next section inside the Open block, else at the end of the file
    m = re.search(r"^## ", tail, re.M)
    if m:
        tail = tail[: m.start()].rstrip("\n") + "\n" + line + "\n\n" + tail[m.start():]
    else:
        tail = tail.rstrip("\n") + "\n" + line + "\n"
    p.write_text(head + tail, encoding="utf-8")
    return line


def complete_task(vault: Path, text: str) -> str:
    """Tick the first open task whose text matches (checkbox flipped in place, nothing else touched)."""
    from . import reader
    p = Path(vault) / "To-Do.md"
    if not p.exists():
        raise ValueError("To-Do.md not found")
    body = p.read_text(encoding="utf-8")
    h = re.search(r"^## Open\b.*$", body, re.M)
    start = h.end() if h else 0
    nxt = re.search(r"^## ", body[start:], re.M)
    end = start + nxt.start() if nxt else len(body)
    pos = start
    for line in body[start:end].splitlines(keepends=True):
        m = reader.TASK.match(line.rstrip("\r\n"))
        if m and m.group(1).lower() != "x":
            clean = reader._clean(reader.DUE.sub("", m.group(2)).replace("()", "").strip(" -"))
            if clean == text:
                i = body.index("[", pos)
                body = body[:i] + "[x" + body[i + 2:]
                p.write_text(body, encoding="utf-8")
                return clean
        pos += len(line)
    raise ValueError("open task not found")
