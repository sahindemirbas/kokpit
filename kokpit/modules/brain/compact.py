"""Companion compact: keep Last-Session.md and Threads.md under their size limits.

When a file is over its limit (``reader.LIMITS``), whole entries move to an
archive file in the same vault and the live file keeps a pointer line:

    <vault>/archive/<stem>-<YYYY-MM>.md      (appended; one file per month)
    Archived text: `archive/<stem>-<YYYY-MM>.md`

Last-Session.md: cards are ``## <at> · <title> · <session>`` blocks. Text left
under ``## Earlier sessions`` (other than pointer lines) moves first, then
cards from the oldest on, until the file fits. The newest card always stays.
Threads.md: only ``### `` blocks under ``## Closed Threads`` move; the active
part is never touched.

Text is moved whole, never cut, summarised or deleted. If the file cannot fit
without touching the newest card or the active threads, nothing is written and
the status is ``needs_rewrite``: the agent has to shorten it by hand. The
archive is written before the live file, so a crash can leave a duplicate but
never loses text.
"""
from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

from . import reader
from .receipt import _atomic_write

ARCHIVE_DIR = "archive"
EARLIER = "## Earlier sessions"
MARKER = "Archived text: "
CLOSED = re.compile(r"^## closed threads[ \t]*$", re.I | re.M)
STAMP = re.compile(r"(20\d\d-\d\d-\d\d)(?:[ T](\d\d:\d\d))?")


def _blocks(text: str, prefix: str) -> tuple[str, list[str]]:
    """Split text into (lead, [blocks]); each block starts with a line beginning with prefix.
    ''.join([lead] + blocks) == text."""
    starts = [m.start() for m in re.finditer(rf"^{re.escape(prefix)}", text, re.M)]
    if not starts:
        return text, []
    lead = text[:starts[0]]
    ends = starts[1:] + [len(text)]
    return lead, [text[s:e] for s, e in zip(starts, ends)]


def _key(block: str, index: int, newer_on_top: bool):
    head = block.split("\n", 1)[0]
    m = STAMP.search(head) or STAMP.search(block)
    pos = -index if newer_on_top else index
    return (m.group(1) if m else "", (m.group(2) or "") if m else "", pos)


def _sep(text: str) -> str:
    if not text:
        return ""
    return text if text.endswith("\n\n") else text + ("\n" if text.endswith("\n") else "\n\n")


def _plan_sessions(text: str, limit: int, marker: str):
    """Return (new_text, moved_texts) or None when the file cannot be made to fit."""
    hits = [m.start() for h in reader.EARLIER_HEADINGS for m in [re.search(rf"^{re.escape(h)}[ \t]*$", text, re.M)] if m]
    cut = min(hits) if hits else len(text)
    head, tail = text[:cut], text[cut:]
    lead, cards = _blocks(head, "## ")
    heading_line, earlier_body = (tail.split("\n", 1) + [""])[:2] if tail else (EARLIER, "")
    markers = [ln for ln in earlier_body.splitlines() if ln.startswith(MARKER)]
    loose = "\n".join(ln for ln in earlier_body.splitlines() if not ln.startswith(MARKER))
    candidates = []
    if loose.strip():
        candidates.append(("loose", loose.strip("\n") + "\n"))
    if cards:
        order = sorted(range(len(cards)), key=lambda i: _key(cards[i], i, True))
        newest = order[-1]
        candidates += [("card", i) for i in order if i != newest]
    if marker not in markers:
        markers = markers + [marker]

    def build(moved_cards: set, loose_kept: bool) -> str:
        body = "".join(c for i, c in enumerate(cards) if i not in moved_cards)
        lines = ([loose.strip("\n")] if loose_kept and loose.strip() else []) + markers
        return _sep(lead + body) + heading_line + "\n" + "\n".join(lines) + "\n"

    moved_cards: set = set()
    loose_kept = True
    moved: list[str] = []
    new = build(moved_cards, loose_kept)
    for kind, item in candidates:
        if len(new) <= limit and moved:
            break
        if kind == "loose":
            loose_kept = False
            moved.append(item)
        else:
            moved_cards.add(item)
            moved.append(cards[item])
        new = build(moved_cards, loose_kept)
    if not moved or len(new) > limit:
        return None
    return new, moved


def _plan_threads(text: str, limit: int, marker: str):
    m = CLOSED.search(text)
    if not m:
        return None
    before = text[:m.start()]
    rest = text[m.end():]
    nxt = re.search(r"^## ", rest, re.M)
    closed, after = (rest[:nxt.start()], rest[nxt.start():]) if nxt else (rest, "")
    lead, blocks = _blocks(closed, "### ")
    if not blocks:
        return None
    lead_lines = [ln for ln in lead.split("\n") if ln.strip()]
    if marker not in lead_lines:
        lead_lines.append(marker)
    order = sorted(range(len(blocks)), key=lambda i: _key(blocks[i], i, False))
    heading = text[m.start():m.end()]

    def build(moved_idx: set) -> str:
        kept = "".join(b for i, b in enumerate(blocks) if i not in moved_idx)
        section = heading + "\n" + "\n".join(lead_lines) + "\n" + ("\n" + kept if kept else "")
        if after:
            section = _sep(section)
        return before + section + after

    moved_idx: set = set()
    new = build(moved_idx)
    for i in order:
        if len(new) <= limit and moved_idx:
            break
        moved_idx.add(i)
        new = build(moved_idx)
    if len(new) > limit:
        return None
    return new, [blocks[i] for i in sorted(moved_idx)]


def compact(vault, dry_run: bool = False, now: datetime | None = None, limits: dict | None = None) -> dict:
    vault = Path(vault)  # the companion files live at the vault root, as reader.read_vault reads them
    limits = limits or reader.LIMITS
    now = now or datetime.now()
    report = {}
    for name, limit in limits.items():
        path = vault / name
        stem = Path(name).stem
        arch_rel = f"{ARCHIVE_DIR}/{stem}-{now:%Y-%m}.md"
        entry = {"status": "", "chars": 0, "limit": limit, "chars_after": 0, "moved_entries": 0,
                 "moved_chars": 0, "archive": ""}
        report[name] = entry
        if not path.is_file():
            entry["status"] = "missing"
            continue
        try:
            text = path.read_bytes().decode("utf-8")
        except UnicodeDecodeError:
            entry["status"] = "needs_attention"
            entry["reason"] = "not valid UTF-8; left untouched"
            continue
        except OSError as exc:
            entry["status"] = "needs_attention"
            entry["reason"] = f"unreadable ({exc.__class__.__name__})"
            continue
        entry["chars"] = entry["chars_after"] = len(text)
        if len(text) <= limit:
            entry["status"] = "within_limit"
            continue
        marker = f"{MARKER}`{arch_rel}`"
        plan = _plan_threads(text, limit, marker) if name == "Threads.md" else _plan_sessions(text, limit, marker)
        if plan is None:
            entry["status"] = "needs_rewrite"
            entry["reason"] = ("the newest card or the active part alone is over the limit; "
                               "shorten it by hand (nothing was moved)")
            continue
        new, moved = plan
        entry.update(chars_after=len(new), moved_chars=sum(len(b) for b in moved), archive=arch_rel)
        if dry_run:
            entry.update(status="would_compact", would_move_entries=len(moved))
            continue
        arch = vault / arch_rel
        old = arch.read_text(encoding="utf-8") if arch.exists() else f"# Archive: {name}\n"
        chunk = f"\n<!-- moved from {name} on {now:%Y-%m-%d %H:%M} -->\n" + "".join(
            b if b.endswith("\n") else b + "\n" for b in moved)
        _atomic_write(arch, old.rstrip("\n") + "\n" + chunk)  # archive first
        _atomic_write(path, new)  # live file second
        entry.update(status="compacted", moved_entries=len(moved))
    return report
