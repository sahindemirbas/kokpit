"""Session receipts: a durable, idempotent record of which session changed which files.

A receipt is one Markdown file under ``<vault>/receipts/``, named after the
SHA-256 of its event id, so the same event always lands in the same file:

    ---
    {"kind": "receipt", "event_id": ..., "harness": ..., "refs": [...], "created_at": ..., "session": ...}
    ---
    <summary>

Writing the same event twice is a no-op (``unchanged``). Reusing an event id
with a different summary or different refs is a ``conflict``: the existing file
is left as it is. Receipts are an audit trail for the next agent and for the
user; they never enter the context block (``context.BLOCKED_PARTS``).

The secret check on the summary is best effort: a few obvious key and token
patterns, nothing more. It is not a scanner.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

DIR = "receipts"
MAX_SUMMARY = 4000
PREVIEW = 300
EVENT_ID = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9._-]{0,99}")
HARNESS = re.compile(r"[a-z0-9_-]{1,32}")
SESSION = re.compile(r"[a-zA-Z0-9_-]{1,64}")
SECRET = re.compile(
    r"\bsk-[A-Za-z0-9_-]{6,}|\bghp_[A-Za-z0-9]{6,}|\bxox[abprs]-|\bAKIA[0-9A-Z]{8,}|-----BEGIN"
    r"|\b(?:token|api_key|apikey|api-key)\s*=", re.I)
FRONT = re.compile(r"\A---\n(.*?)\n---\n", re.S)


def receipt_name(event_id: str) -> str:
    return hashlib.sha256(event_id.encode("utf-8")).hexdigest() + ".md"


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _check_refs(vault: Path, refs) -> list[str]:
    if isinstance(refs, str):
        refs = [refs]
    out: list[str] = []
    root = vault.resolve()
    for ref in refs or []:
        ref = str(ref).strip()
        if not ref:
            continue
        if "\\" in ref or ref.startswith("/") or re.match(r"^[A-Za-z]:", ref):
            raise ValueError(f"ref must be a vault-relative posix path: {ref!r}")
        p = PurePosixPath(ref)
        if ".." in p.parts:
            raise ValueError(f"ref must not contain '..': {ref!r}")
        try:
            (vault / p).resolve().relative_to(root)
        except ValueError:
            raise ValueError(f"ref points outside the vault: {ref!r}") from None
        norm = p.as_posix()
        if norm not in out:
            out.append(norm)
    if not out:
        raise ValueError("at least one ref (a file the session touched) is required")
    return out


def _parse(text: str) -> tuple[dict, str] | None:
    m = FRONT.match(text)
    if not m:
        return None
    try:
        meta = json.loads(m.group(1))
    except ValueError:
        return None
    if not isinstance(meta, dict) or meta.get("kind") != "receipt":
        return None
    body = text[m.end():]
    return meta, body[:-1] if body.endswith("\n") else body


def write_receipt(vault, event_id: str, summary: str, refs, harness: str = "manual",
                  session: str | None = None, now: datetime | None = None) -> dict:
    vault = Path(vault)
    event_id = (event_id or "").strip()
    if not EVENT_ID.fullmatch(event_id):
        raise ValueError("event id must match [a-zA-Z0-9][a-zA-Z0-9._-]{0,99}")
    harness = (harness or "manual").strip()
    if not HARNESS.fullmatch(harness):
        raise ValueError("harness must match [a-z0-9_-]{1,32}")
    if session is not None and session != "" and not SESSION.fullmatch(session):
        raise ValueError("session must match [a-zA-Z0-9_-]{1,64}")
    summary = (summary or "").strip()
    if not summary:
        raise ValueError("summary must not be empty")
    if len(summary) > MAX_SUMMARY:
        raise ValueError(f"summary is {len(summary)} characters; the limit is {MAX_SUMMARY}")
    if SECRET.search(summary):
        raise ValueError("summary looks like it contains a secret")
    refs = _check_refs(vault, refs)
    missing = [r for r in refs if not (vault / r).exists()]
    name = receipt_name(event_id)
    rel = f"{DIR}/{name}"
    path = vault / DIR / name
    base = {"path": rel, "event_id": event_id, "missing_refs": missing}

    if path.exists():
        try:
            old = _parse(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError):
            old = None
        if old and old[0].get("event_id") == event_id and old[1] == summary and old[0].get("refs") == refs:
            return {"status": "unchanged", **base}
        return {"status": "conflict", "reason": "event id already used with different content", **base}

    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    meta = {"kind": "receipt", "event_id": event_id, "harness": harness, "refs": refs,
            "created_at": now.astimezone(timezone.utc).isoformat()}
    if session:
        meta["session"] = session
    _atomic_write(path, "---\n" + json.dumps(meta, indent=2, ensure_ascii=False) + "\n---\n" + summary + "\n")
    return {"status": "succeeded", **base}


def scan_receipts(vault, limit: int = 20) -> dict:
    """Newest receipts first plus the number of unreadable files (skipped, never deleted)."""
    folder = Path(vault) / DIR
    items, skipped = [], 0
    for p in sorted(folder.glob("*.md")) if folder.is_dir() else []:
        try:
            parsed = _parse(p.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError):
            parsed = None
        if not parsed:
            skipped += 1
            continue
        meta, body = parsed
        items.append({
            "event_id": str(meta.get("event_id", "")),
            "harness": str(meta.get("harness", "")),
            "created_at": str(meta.get("created_at", "")),
            "session": str(meta.get("session", "") or ""),
            "refs": [str(r) for r in meta.get("refs", []) if isinstance(r, str)],
            "summary": body[:PREVIEW],
            "path": f"{DIR}/{p.name}",
        })
    items.sort(key=lambda r: (r["created_at"], r["event_id"]), reverse=True)
    return {"receipts": items[:max(0, limit)], "skipped": skipped, "total": len(items)}


def read_receipts(vault, limit: int = 20) -> list[dict]:
    return scan_receipts(vault, limit)["receipts"]
