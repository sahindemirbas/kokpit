"""Meeting notes editor: answer the question lists of a configured note from the dashboard.

A meeting is declared in ``kokpit.toml`` (``[[brain.meetings]]``): a note inside the vault and the headings whose
bullet lines are the questions. Answers and general notes are written back into the SAME note, inside one marked
section at the end::

    <!-- kokpit-meeting:ID -->
    ## Meeting notes (Kokpit)
    ...readable answers...
    ```json
    {"v": 1, "answers": [...], "general": "...", "orphans": [...]}
    ```
    <!-- /kokpit-meeting:ID -->

The JSON block is the canonical data; the bytes outside the markers are never touched. Saves are optimistic
(``revision`` = sha256 of the source bytes, mismatch -> ``Conflict``), serialised by a lock file next to the note,
backed up under ``data/backups/meetings/`` and written atomically (tmp + ``os.replace``).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
from datetime import datetime
from pathlib import Path

VERSION = 1
MAX_ANSWER = 5000
MAX_GENERAL = 20000
MAX_TOTAL = 40000
MAX_QUESTIONS = 100
MAX_SOURCE = 1024 * 1024
MAX_BODY = 250_000
LOCK_WAIT = 5.0
LOCK_STALE = 60.0
BACKUPS_KEPT = 20
_BULLET = re.compile(r"^(?:[-*]|\d+[.)])\s+(.*\S)")


class MeetingError(Exception):
    """Base class; ``code`` is the short machine-readable reason."""
    code = "error"

    def __init__(self, code: str | None = None):
        super().__init__(code or self.code)
        if code:
            self.code = code


class UnknownMeeting(MeetingError):
    code = "meeting not found"


class SourceError(MeetingError):
    code = "meeting source could not be read"


class Invalid(MeetingError):
    code = "invalid"


class TooLarge(Invalid):
    code = "too_large"


class Conflict(MeetingError):
    code = "conflict"

    def __init__(self, current: dict):
        super().__init__("conflict")
        self.current = current


# ---- config helpers ---------------------------------------------------------

def meeting(cfg, mid: str) -> dict:
    for m in cfg.meetings:
        if m["id"] == mid:
            return m
    raise UnknownMeeting()


def source_path(cfg, mid: str) -> Path:
    return cfg.vault / meeting(cfg, mid)["source"]


def read_source(cfg, mid: str) -> bytes:
    p = source_path(cfg, mid)
    try:
        if p.stat().st_size > MAX_SOURCE:
            raise SourceError("source_too_large")
        return p.read_bytes()
    except FileNotFoundError:
        raise SourceError("source_missing") from None
    except OSError:
        raise SourceError() from None


def list_meetings(cfg) -> list[dict]:
    out = []
    for m in cfg.meetings:
        p = cfg.vault / m["source"]
        exists = p.is_file()
        count = 0
        if exists:
            try:
                count = load(cfg, m["id"])["count"]
            except MeetingError:
                count = 0
        out.append({"id": m["id"], "title": m["title"], "source": Path(m["source"]).name, "exists": exists, "count": count})
    return out


# ---- parsing ----------------------------------------------------------------

def _markers(mid: str) -> tuple[str, str]:
    return f"<!-- kokpit-meeting:{mid} -->", f"<!-- /kokpit-meeting:{mid} -->"


def _split(text: str, mid: str) -> tuple[str, str | None, str]:
    """(head, section or None, tail). Markers must be unique and paired."""
    start, end = _markers(mid)
    if text.count(start) > 1 or text.count(end) > 1 or text.count(start) != text.count(end):
        raise SourceError("section_markers")
    i = text.find(start)
    if i < 0:
        return text, None, ""
    j = text.find(end, i)
    if j < 0:
        raise SourceError("section_markers")
    j += len(end)
    return text[:i], text[i:j], text[j:]


def _questions(head: str, m: dict) -> tuple[list[dict], list[str]]:
    """Bullet lines under each configured heading, numbered 1..N across all groups. Only the text before the section."""
    lines = head.split("\n")
    groups, warnings, n = [], [], 0
    for heading in m["sections"]:
        label = heading.lstrip("#").strip()
        try:
            k = next(idx for idx, ln in enumerate(lines) if ln.strip() == heading)
        except StopIteration:
            warnings.append(f"heading not found: {heading}")
            groups.append({"title": label, "questions": []})
            continue
        qs = []
        for ln in lines[k + 1:]:
            if ln.startswith("#"):
                break
            hit = _BULLET.match(ln)
            if hit:
                n += 1
                qs.append({"n": n, "q": hit.group(1).strip()})
        groups.append({"title": label, "questions": qs})
    if n > MAX_QUESTIONS:
        raise SourceError("too_many_questions")
    return groups, warnings


def _norm(q: str) -> str:
    return " ".join(q.split())


def _data(section: str | None) -> dict:
    if section is None:
        return {"v": VERSION, "answers": [], "general": "", "orphans": [], "updated_at": ""}
    hit = re.search(r"^```json\n(.*?)\n^```$", section, re.S | re.M)
    try:
        d = json.loads(hit.group(1))  # type: ignore[union-attr]
        assert d["v"] == VERSION and isinstance(d["answers"], list) and isinstance(d["general"], str)
        for a in d["answers"]:
            assert isinstance(a["q"], str) and isinstance(a["a"], str)
        d.setdefault("orphans", [])
        d.setdefault("updated_at", "")
        return d
    except Exception:
        raise SourceError("data_block_corrupt") from None


def parse(raw: bytes, m: dict) -> dict:
    """Editor view of the source bytes. Keeps no copy of the note."""
    try:
        text = raw.decode("utf-8")
    except UnicodeError:
        raise SourceError("not_utf8") from None
    head, section, _ = _split(text, m["id"])
    groups, warnings = _questions(head, m)
    d = _data(section)
    stored: dict[str, str] = {}
    for a in d["answers"]:
        stored.setdefault(_norm(a["q"]), a["a"])
    current = {_norm(q["q"]) for g in groups for q in g["questions"]}
    count = 0
    for g in groups:
        for q in g["questions"]:
            q["a"] = stored.get(_norm(q["q"]), "")
            count += 1
    raw_orphans = [o for o in d["orphans"] if isinstance(o, dict)]
    raw_orphans += [a for a in d["answers"] if _norm(a["q"]) not in current and a["a"]]
    seen, orphans = set(), []
    for o in raw_orphans:
        key = (_norm(str(o.get("q", ""))), str(o.get("a", "")))
        if key not in seen:
            seen.add(key)
            orphans.append({"q": str(o.get("q", "")), "a": str(o.get("a", ""))})
    return {"id": m["id"], "title": m["title"], "source": Path(m["source"]).name, "groups": groups, "count": count,
            "general": d["general"], "orphans": orphans, "updated_at": d["updated_at"], "warnings": warnings,
            "revision": hashlib.sha256(raw).hexdigest()}


def load(cfg, mid: str) -> dict:
    return parse(read_source(cfg, mid), meeting(cfg, mid))


# ---- validation -------------------------------------------------------------

def _control(s: str) -> bool:
    return any(ord(c) < 32 and c not in "\n\t" for c in s) or "\x7f" in s


def _nl(s: str) -> str:
    return s.replace("\r\n", "\n").replace("\r", "\n")


def validate_save(cfg, body) -> dict:
    """Checks the client body. No silent truncation (TooLarge instead); only line endings are normalised."""
    if not isinstance(body, dict):
        raise Invalid("body")
    mid = body.get("meeting")
    if not isinstance(mid, str):
        raise Invalid("meeting")
    meeting(cfg, mid)  # UnknownMeeting
    rev = body.get("revision")
    if not isinstance(rev, str) or not re.fullmatch(r"[0-9a-f]{64}", rev):
        raise Invalid("revision")
    answers, general = body.get("answers"), body.get("general")
    if not isinstance(answers, list) or len(answers) > MAX_QUESTIONS or not isinstance(general, str):
        raise Invalid("fields")
    out = []
    for a in answers:
        if not isinstance(a, str):
            raise Invalid("answer_type")
        out.append(_nl(a))
    general = _nl(general)
    for s in out + [general]:
        if _control(s) or "<!--" in s:
            raise Invalid("characters")
    if any(len(a) > MAX_ANSWER for a in out) or len(general) > MAX_GENERAL or sum(map(len, out)) + len(general) > MAX_TOTAL:
        raise TooLarge()
    return {"meeting": mid, "revision": rev, "answers": out, "general": general}


# ---- section building -------------------------------------------------------

def _json(d: dict) -> str:
    s = json.dumps(d, ensure_ascii=False, indent=1)
    return s.replace("<", "\\u003c").replace("`", "\\u0060")  # the block can never close the fence or forge a marker


def _quote(s: str) -> str:
    return "\n".join(("> " + ln).rstrip() for ln in s.split("\n")) if s else "> (none)"


def build_section(mid: str, groups: list[dict], answers: list[str], general: str, orphans: list[dict], now: datetime) -> str:
    start, end = _markers(mid)
    out = [start, "## Meeting notes (Kokpit)",
           f"Last saved: {now:%Y-%m-%d %H:%M}. Edited on the Kokpit Brain > Meetings screen; "
           "the JSON block below is the canonical data.",
           "", "### Answers"]
    i = 0
    for g in groups:
        out.append(f"#### {g['title']}")
        for q in g["questions"]:
            a = answers[i]
            i += 1
            out.append(f"{q['n']}. {q['q']}")
            out.append("   Answer: " + (a.replace("\n", "\n   ") if a else "(none)"))
        out.append("")
    out += ["### General notes and decisions", _quote(general), "",
            "```json",
            _json({"v": VERSION, "updated_at": now.isoformat(timespec="seconds"),
                   "answers": [{"n": q["n"], "q": q["q"], "a": answers[q["n"] - 1]} for g in groups for q in g["questions"]],
                   "general": general, "orphans": orphans}),
            "```", end]
    return "\n".join(out)


def apply_save(raw: bytes, clean: dict, m: dict, now: datetime | None = None) -> bytes:
    """Applies a validated body to the source bytes and returns the new bytes. Head and tail stay byte-identical."""
    cur = parse(raw, m)
    if clean["revision"] != cur["revision"]:
        raise Conflict(cur)
    if len(clean["answers"]) != cur["count"]:
        raise Invalid("answer_count")
    text = raw.decode("utf-8")
    head, section, tail = _split(text, m["id"])
    unchanged = (section is not None and clean["general"] == cur["general"]
                 and clean["answers"] == [q["a"] for g in cur["groups"] for q in g["questions"]])
    if unchanged:
        return raw  # idempotent: same data, no new timestamp
    now = now or datetime.now().astimezone()
    sec = build_section(m["id"], cur["groups"], clean["answers"], clean["general"], cur["orphans"], now)
    if section is not None:
        return (head + sec + tail).encode("utf-8")
    return (text + ("" if text.endswith("\n") else "\n") + "\n" + sec + "\n").encode("utf-8")


# ---- writing ----------------------------------------------------------------

class _Lock:
    """``<source>.lock`` created with O_EXCL; waits up to LOCK_WAIT, breaks a lock older than LOCK_STALE."""

    def __init__(self, target: Path, clock=time.time, sleep=time.sleep):
        self.path = target.with_name(target.name + ".lock")
        self.clock, self.sleep = clock, sleep

    def __enter__(self):
        deadline = self.clock() + LOCK_WAIT
        while True:
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                os.write(fd, str(os.getpid()).encode())
                os.close(fd)
                return self
            except FileExistsError:
                try:
                    age = self.clock() - self.path.stat().st_mtime
                except FileNotFoundError:
                    continue
                if age > LOCK_STALE:
                    self.path.unlink(missing_ok=True)
                    continue
                if self.clock() >= deadline:
                    raise Conflict({"code": "locked"})
                self.sleep(0.1)

    def __exit__(self, *exc):
        self.path.unlink(missing_ok=True)


def _backup(cfg, mid: str, raw: bytes, now: datetime) -> Path:
    d = cfg.resolve("data/backups/meetings")
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{mid}-{now:%Y%m%d-%H%M%S}.md"
    n = 1
    while p.exists():
        n += 1
        p = d / f"{mid}-{now:%Y%m%d-%H%M%S}-{n}.md"
    p.write_bytes(raw)
    old = sorted(d.glob(f"{mid}-*.md"), key=lambda x: x.stat().st_mtime)
    for stale in old[:-BACKUPS_KEPT]:
        stale.unlink(missing_ok=True)
    return p


def atomic_write(path: Path, data: bytes) -> None:
    tmp = path.with_name(f".{path.name}.kokpit-tmp")
    with open(tmp, "wb") as fh:
        fh.write(data)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def save(cfg, body, now: datetime | None = None, clock=time.time, sleep=time.sleep) -> dict:
    """validate -> lock -> read -> apply -> backup -> atomic write. Returns the new revision."""
    if not cfg.meetings_write:
        raise PermissionError("meeting notes are read-only (brain.meetings_write = false)")
    clean = validate_save(cfg, body)
    mid = clean["meeting"]
    m = meeting(cfg, mid)
    target = source_path(cfg, mid)
    now = now or datetime.now().astimezone()
    with _Lock(target, clock, sleep):
        raw = read_source(cfg, mid)
        new = apply_save(raw, clean, m, now)
        if new != raw:
            _backup(cfg, mid, raw, now)
            atomic_write(target, new)
            stamp = now.isoformat(timespec="seconds")
        else:
            stamp = parse(raw, m)["updated_at"]  # idempotent save keeps the stored timestamp
    return {"ok": True, "revision": hashlib.sha256(new).hexdigest(), "updated_at": stamp}
