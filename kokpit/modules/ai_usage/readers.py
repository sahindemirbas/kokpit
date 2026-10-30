"""Readers: one per tool, all read-only. Each returns normalised rows and never writes to the source.

Row keys: tool, model_raw, session_hash, place, started_at, last_seen, input_tokens, output_tokens,
cache_read_tokens, cache_write_tokens. ``input_tokens`` never includes cache reads.
A reader raises ``ReaderError`` for a missing or unreadable source; the job turns it into a warning.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

from .normalize import session_hash

TOKEN_KEYS = ("input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens")


class ReaderError(Exception):
    pass


def iso(ts) -> str | None:
    """Epoch seconds (int/float) or ISO string -> UTC 'YYYY-MM-DDTHH:MM:SS'."""
    if ts in (None, ""):
        return None
    try:
        if isinstance(ts, (int, float)):
            return datetime.fromtimestamp(float(ts), tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
        d = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        return d.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    except (ValueError, OverflowError, OSError):
        return None


def _n(v) -> int:
    try:
        return max(0, int(v or 0))
    except (TypeError, ValueError):
        return 0


def _merge(acc: dict, key: tuple, base: dict, tokens: dict, started: str | None, last: str | None) -> None:
    r = acc.get(key)
    if r is None:
        r = acc[key] = {**base, "started_at": started, "last_seen": last, **{k: 0 for k in TOKEN_KEYS}}
    for k in TOKEN_KEYS:
        r[k] += tokens.get(k, 0)
    if started and (not r["started_at"] or started < r["started_at"]):
        r["started_at"] = started
    if last and (not r["last_seen"] or last > r["last_seen"]):
        r["last_seen"] = last


def _finish(acc: dict) -> list[dict]:
    out = []
    for r in acc.values():
        if any(r[k] for k in TOKEN_KEYS):
            r["last_seen"] = r["last_seen"] or r["started_at"]
            r["started_at"] = r["started_at"] or r["last_seen"]
            out.append(r)
    return out


def newest_mtime(paths) -> str | None:
    times = []
    for p in paths:
        try:
            times.append(Path(p).stat().st_mtime)
        except OSError:
            pass
    return iso(max(times)) if times else None


# ---- Hermes: state.db, tables sessions + session_model_usage ---------------------------------------------------------

def read_hermes(path: Path) -> tuple[list[dict], dict]:
    path = Path(path)
    if not path.is_file():
        raise ReaderError(f"database not found: {path.name}")
    try:
        conn = sqlite3.connect(f"file:{quote(str(path))}?mode=ro", uri=True, timeout=10)
        conn.row_factory = sqlite3.Row
    except sqlite3.Error as exc:
        raise ReaderError(f"cannot open {path.name}: {exc}") from exc
    try:
        try:
            cur = conn.execute(
                "SELECT u.session_id, u.model, u.input_tokens, u.output_tokens, u.cache_read_tokens, u.cache_write_tokens, "
                "u.first_seen, u.last_seen, s.source, s.started_at, s.last_activity_at "
                "FROM session_model_usage u LEFT JOIN sessions s ON s.id = u.session_id")
            fetched = cur.fetchall()
        except sqlite3.Error as exc:
            raise ReaderError(f"unexpected schema in {path.name}: {exc}") from exc
    finally:
        conn.close()
    acc: dict = {}
    for r in fetched:
        started = iso(r["first_seen"]) or iso(r["started_at"])
        last = iso(r["last_seen"]) or iso(r["last_activity_at"]) or started
        base = {"tool": "hermes", "model_raw": r["model"] or "unknown", "session_hash": session_hash(r["session_id"]), "place": r["source"] or ""}
        tokens = {"input_tokens": _n(r["input_tokens"]), "output_tokens": _n(r["output_tokens"]),
                  "cache_read_tokens": _n(r["cache_read_tokens"]), "cache_write_tokens": _n(r["cache_write_tokens"])}
        _merge(acc, (r["session_id"], base["model_raw"]), base, tokens, started, last)
    return _finish(acc), {"files": 1, "source_mtime": newest_mtime([path])}


# ---- JSONL helpers ---------------------------------------------------------------------------------------------------

def _jsonl(path: Path):
    """Yield (object, bad_line_count_so_far is not needed) for every valid JSON line; skip broken lines."""
    try:
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line.startswith("{"):
                    continue
                try:
                    obj = json.loads(line)
                except ValueError:
                    continue
                if isinstance(obj, dict):
                    yield obj
    except OSError:
        return


def _files(root: Path, label: str) -> list[Path]:
    root = Path(root)
    if not root.is_dir():
        raise ReaderError(f"{label} folder not found: {root.name}")
    return sorted(root.rglob("*.jsonl"))


# ---- Claude Code: ~/.claude/projects/**/*.jsonl ------------------------------------------------------------------------

def read_claude_code(root: Path) -> tuple[list[dict], dict]:
    """Assistant lines carry message.usage and message.model. One API message can be written several times
    (one line per content block, or again in a resumed session file), so lines are deduplicated by message id
    (requestId as fallback) and the largest value of each counter is kept."""
    files = _files(root, "Claude Code projects")
    msgs: dict[str, dict] = {}
    for f in files:
        for o in _jsonl(f):
            if o.get("type") != "assistant":
                continue
            m = o.get("message") or {}
            u = m.get("usage")
            if not isinstance(u, dict):
                continue
            model = str(m.get("model") or "")
            if not model or model.startswith("<"):
                continue  # synthetic placeholder lines
            key = str(m.get("id") or o.get("requestId") or o.get("uuid") or "")
            if not key:
                continue
            tok = {"input_tokens": _n(u.get("input_tokens")), "output_tokens": _n(u.get("output_tokens")),
                   "cache_read_tokens": _n(u.get("cache_read_input_tokens")), "cache_write_tokens": _n(u.get("cache_creation_input_tokens"))}
            ts = iso(o.get("timestamp"))
            cur = msgs.get(key)
            if cur is None:
                msgs[key] = {"model": model, "session": str(o.get("sessionId") or f.stem), "place": str(o.get("entrypoint") or ""), "ts": ts, **tok}
                continue
            for k in TOKEN_KEYS:
                cur[k] = max(cur[k], tok[k])
            if ts and (not cur["ts"] or ts < cur["ts"]):
                cur["ts"] = ts
    acc: dict = {}
    for m in msgs.values():
        base = {"tool": "claude_code", "model_raw": m["model"], "session_hash": session_hash(m["session"]), "place": m["place"]}
        _merge(acc, (m["session"], m["model"]), base, {k: m[k] for k in TOKEN_KEYS}, m["ts"], m["ts"])
    return _finish(acc), {"files": len(files), "messages": len(msgs), "source_mtime": newest_mtime(files)}


# ---- Codex: ~/.codex/sessions/**/*.jsonl -----------------------------------------------------------------------------

def read_codex(root: Path) -> tuple[list[dict], dict]:
    """token_count events carry ``info.total_token_usage``, a running total for the session. Only the growth
    since the previous event is counted, so repeated events add nothing; a total that drops (counter reset)
    counts as new usage. Field notes (checked against real token_count events: total_tokens == input_tokens + output_tokens and
    cached_input_tokens <= input_tokens): cached input is a subset of input_tokens, so it is subtracted from input and stored as
    cache reads; reasoning_output_tokens is part of output_tokens and is not added again. OpenAI has no cache write charge, so
    cache_write is always 0 (the log carries a cache_write_input_tokens field, always 0 in the samples; it is deliberately ignored)."""
    files = _files(root, "Codex sessions")
    acc: dict = {}
    for f in files:
        sid, model, place, prev = f.stem, "unknown", "", dict.fromkeys(("input", "cached", "output"), 0)
        for o in _jsonl(f):
            p = o.get("payload") if isinstance(o.get("payload"), dict) else {}
            kind = o.get("type")
            if kind == "session_meta":
                sid = str(p.get("id") or p.get("session_id") or sid)
                place = str(p.get("source") or "")
            elif kind == "turn_context" and p.get("model"):
                model = str(p["model"])
            elif kind == "event_msg" and p.get("type") == "token_count":
                tot = ((p.get("info") or {}).get("total_token_usage")) or None
                if not isinstance(tot, dict):
                    continue
                cur = {"input": _n(tot.get("input_tokens")), "cached": _n(tot.get("cached_input_tokens")),
                       "output": _n(tot.get("output_tokens"))}
                delta = {k: (cur[k] - prev[k]) if cur[k] >= prev[k] else cur[k] for k in cur}
                prev = cur
                if not any(delta.values()):
                    continue
                tokens = {"input_tokens": max(0, delta["input"] - delta["cached"]), "output_tokens": delta["output"],
                          "cache_read_tokens": delta["cached"], "cache_write_tokens": 0}
                ts = iso(o.get("timestamp"))
                base = {"tool": "codex", "model_raw": model, "session_hash": session_hash(sid), "place": place}
                _merge(acc, (sid, model), base, tokens, ts, ts)
    return _finish(acc), {"files": len(files), "source_mtime": newest_mtime(files)}


READERS = {"hermes": ("hermes_db", read_hermes), "claude_code": ("claude_dir", read_claude_code), "codex": ("codex_dir", read_codex)}
