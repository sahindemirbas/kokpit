"""Model-assisted proposals for the night organiser. Opt-in, proposals only, every item checked in code.

Runs after the rule detectors and only when ``[model] enabled`` and ``[model.uses] night`` are both on.
What is sent: the full text (capped) of ``inbox/`` notes and notes changed today, at most ``max_notes`` of
them and ``max_chars`` each, plus a catalogue of the other notes (path, title, aliases, first 200 characters)
so the model can pick a target. Threads.md, Last-Session.md and To-Do.md bodies are never sent.

The model answers with a JSON array; nothing it says is trusted. An item is kept only when its kind is known,
its paths are real notes from this scan (or a configured folder for ``categorise``), its quote appears word for
word in the source note, its reason is on the allowed list and its text is short. Everything else is counted
as rejected and never stored.
"""
from __future__ import annotations

import json
import re
from datetime import date, datetime
from pathlib import PurePosixPath

KINDS = ("categorise", "link", "conditions", "enrich")
FIELDS = {"kind", "path", "target", "detail", "text", "reason", "evidence"}
LINK_REASONS = ("Condition", "Continuation", "Preparation", "Shared goal (interpretation)")
SPLITS = ("split-idea", "split-person")
NEVER_SEND = {"threads.md", "last-session.md", "to-do.md"}
MAX_TEXT = 300
MAX_EVIDENCE = 300
CATALOGUE = 400  # at most this many catalogue rows; each is short

SYSTEM = """You review a personal Markdown notes vault and suggest housekeeping. You never edit anything; a person decides.
Answer with ONE JSON array and nothing else. Each item is an object with exactly these keys:
kind, path, target, detail, text, reason, evidence.
Allowed kinds:
- categorise: path = a note from NOTES; target = one folder from FOLDERS; detail = a suggested file name ending in .md; reason = why.
- link: path = a note from NOTES; target = another note path from NOTES or CATALOGUE that path should link to; reason = exactly one of: Condition, Continuation, Preparation, Shared goal (interpretation); evidence = a sentence copied word for word from the path note.
- conditions: path = an OLDER note that waits for something; target = a NEWER note from NOTES that says it happened; detail = the condition in a few words; evidence = a sentence copied word for word from the target note.
- enrich: path = a note from NOTES; either target = an existing note where this information belongs, or target = "" and detail = split-idea or split-person when a separate note should be made; evidence = a sentence copied word for word from the path note; reason = why.
text is one plain sentence for the person, at most 300 characters. Only use paths that appear in the input. Do not invent facts, dates or times. If there is nothing worth suggesting, answer []."""


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", str(s or "")).strip()


def candidates(notes: list[dict], today: date, max_notes: int) -> list[dict]:
    """inbox/ notes first, then notes whose file changed today; capped and in a stable order."""
    def eligible(n):
        return PurePosixPath(n["path"]).name.lower() not in NEVER_SEND
    inbox = [n for n in notes if eligible(n) and n["path"].split("/")[0].lower() == "inbox"]
    seen = {n["path"] for n in inbox}
    changed = [n for n in notes if eligible(n) and n["path"] not in seen and n.get("mtime")
               and datetime.fromtimestamp(n["mtime"]).date() == today]
    return (sorted(inbox, key=lambda n: n["path"]) + sorted(changed, key=lambda n: n["path"]))[:max_notes]


def build_prompt(sent: list[dict], notes: list[dict], folders: list[str], max_chars: int) -> str:
    chosen = {n["path"] for n in sent}
    parts = ["FOLDERS: " + json.dumps(sorted(set(folders))), "", "NOTES (full text, capped):"]
    for n in sent:
        parts += [f"=== {n['path']} (date {n['date']})", n["body"][:max_chars], ""]
    parts.append("CATALOGUE (other notes, path | title | aliases | start):")
    rows = [n for n in notes if n["path"] not in chosen and PurePosixPath(n["path"]).name.lower() not in NEVER_SEND]
    for n in rows[:CATALOGUE]:
        parts.append(f"{n['path']} | {n['title']} | {', '.join(n['aliases'])} | {_norm(n['body'])[:200]}")
    return "\n".join(parts)


def parse_array(raw: str) -> list:
    """The first JSON array in the answer (a ```json fence is allowed). Raises ValueError otherwise."""
    s = (raw or "").strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", s, re.S)
    if fence:
        s = fence.group(1).strip()
    if not s.startswith("["):
        i, j = s.find("["), s.rfind("]")
        if i < 0 or j <= i:
            raise ValueError("model answer is not a JSON array")
        s = s[i:j + 1]
    data = json.loads(s)
    if not isinstance(data, list):
        raise ValueError("model answer is not a JSON array")
    return data


def validate(items: list, notes: list[dict], sent: list[dict], folders: list[str]) -> tuple[list[dict], list[dict]]:
    """(accepted proposals, rejected [{reason, raw_kind, raw_path}])."""
    by_path = {n["path"]: n for n in notes}
    sent_paths = {n["path"] for n in sent}
    folder_set = {f.strip("/") for f in folders if f}
    ok: list[dict] = []
    bad: list[dict] = []

    def reject(why, it):
        kind = it.get("kind") if isinstance(it, dict) else None
        path = it.get("path") if isinstance(it, dict) else None
        bad.append({"reason": why, "raw_kind": str(kind or "")[:40], "raw_path": str(path or "")[:200]})

    for it in items:
        if not isinstance(it, dict):
            reject("not-an-object", it)
            continue
        if set(it) - FIELDS:
            reject("unknown-field", it)
            continue
        if any(v is not None and not isinstance(v, str) for v in it.values()):
            reject("non-text-field", it)
            continue
        f = {k: (it.get(k) or "").strip() for k in FIELDS}
        kind, path, target, detail = f["kind"], f["path"], f["target"], f["detail"]
        text, reason, evidence = _norm(f["text"]), _norm(f["reason"]), _norm(f["evidence"])
        if kind not in KINDS:
            reject("unknown-kind", it)
            continue
        if not text or len(text) > MAX_TEXT:
            reject("text-length", it)
            continue
        if len(evidence) > MAX_EVIDENCE:
            reject("evidence-length", it)
            continue
        if path not in by_path:
            reject("unknown-path", it)
            continue
        if kind == "categorise":
            name = PurePosixPath(detail).name
            if target not in folder_set:
                reject("unknown-folder", it)
                continue
            if not detail or name != detail or not detail.endswith(".md") or detail.startswith("."):
                reject("bad-file-name", it)
                continue
            if path not in sent_paths or not reason:
                reject("missing-reason" if path in sent_paths else "not-sent", it)
                continue
            if evidence and evidence not in _norm(by_path[path]["body"]):
                reject("evidence-not-found", it)
                continue
            if path.rsplit("/", 1)[0] == target and PurePosixPath(path).name == detail:
                reject("no-change", it)
                continue
            detail = name
        elif kind == "link":
            if path not in sent_paths:
                reject("not-sent", it)
                continue
            if target not in by_path or target == path:
                reject("unknown-target" if target != path else "self-link", it)
                continue
            if reason not in LINK_REASONS:
                reject("bad-reason", it)
                continue
            if not evidence or evidence not in _norm(by_path[path]["body"]):
                reject("evidence-not-found", it)
                continue
            if by_path[target]["links_key"] & by_path[path]["links"]:
                reject("already-linked", it)
                continue
            detail = "semantic"
        elif kind == "conditions":
            if target not in sent_paths or target not in by_path or target == path:
                reject("unknown-target", it)
                continue
            old, new = by_path[path], by_path[target]
            if not (old["date"] and new["date"] and new["date"] > old["date"]):
                reject("reverse-direction", it)
                continue
            if not detail:
                reject("missing-condition", it)
                continue
            if not evidence or evidence not in _norm(new["body"]):
                reject("evidence-not-found", it)
                continue
            detail = _norm(detail).lower()[:60]
        else:  # enrich
            if path not in sent_paths:
                reject("not-sent", it)
                continue
            if target:
                if target not in by_path or target == path:
                    reject("unknown-target", it)
                    continue
                detail = "fold"
            elif detail not in SPLITS:
                reject("bad-detail", it)
                continue
            if not evidence or evidence not in _norm(by_path[path]["body"]):
                reject("evidence-not-found", it)
                continue
        ok.append({"kind": kind, "path": path, "target": target, "detail": detail, "text": text,
                   "source": "model", "reason": reason[:120], "evidence": evidence})
    return ok, bad


def propose(cfg, notes: list[dict], today: date, complete) -> tuple[list[dict], list[dict], list[dict]]:
    """(accepted, rejected, sent notes). ``complete`` is the model call (client.complete); errors propagate."""
    o = cfg.organiser
    sent = candidates(notes, today, o["max_notes"])
    if not sent:
        return [], [], []
    folders = sorted(set(o["folders"].values()) | set(o["types"]))
    user = build_prompt(sent, notes, folders, o["max_chars"])
    raw = complete(cfg, "night", SYSTEM, user, max_tokens=1500)
    ok, bad = validate(parse_array(raw), notes, sent, folders)
    return ok, bad, sent
