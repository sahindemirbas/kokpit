"""Hooks: brain context before a turn; a session card and a receipt only when the session had a result.

A result is a vault capture, a file written or edited by a tool, an accepted night proposal, or an
explicit close (/kokpit done, kokpit_capture kind receipt). A chat-only session with lookups writes
nothing. No model calls and no network: the card is built from rules (what was captured or written,
what the Threads file still lists as open). No user text is kept beyond the first message (card title).
The one exception is opt-in: with collect_auto on, session start runs the enabled collectors in a
background thread (network only through collector.fetch); off by default.
"""
from __future__ import annotations

import hashlib
import os
import re
import threading
from collections import Counter
from datetime import datetime
from pathlib import Path

from . import contract as c
from . import collector as _collector
from . import night as _night

WRITE_TOOLS = {"write_file", "patch"}
PATH_KEYS = ("path", "file_path", "filename", "target_file")
MAX_SESSIONS = 64
RECEIPT_MAX = 300   # characters of receipt text; details stay in the notes it refers to
REFS_MAX = 8
# key-like strings: provider key prefixes, private key blocks, "password: ..." style assignments
SECRET = re.compile(r"\bs[k]-[A-Za-z0-9_-]{6,}|\bgh[p]_[A-Za-z0-9]{6,}|\bxox[abprs]-|\bAKIA[0-9A-Z]{8,}|-----BEGIN"
                    r"|\b(?:tok[e]n|api[_-]?k[e]y|secret|passw(?:or)?d)\s*[=:]", re.I)
DONE_USAGE = "/kokpit done <short summary of the result>"


def _short_path(p: str) -> str:
    home = os.path.expanduser("~")
    return "~" + p[len(home):] if home and p.startswith(home + os.sep) else p


def _on(value) -> bool:
    return str(value).strip().lower() not in ("false", "0", "off", "no", "")


def _text(message) -> str:
    if isinstance(message, dict):
        message = message.get("content", "")
    if isinstance(message, list):
        message = " ".join(str(x.get("text", "")) for x in message if isinstance(x, dict))
    return message if isinstance(message, str) else str(message or "")


def _ok(status) -> bool:
    return status in ("", "ok", "success", None)


class Hooks:
    def __init__(self, env: c.Env):
        self.env = env
        self._lock = threading.Lock()
        self._s: dict[str, dict] = {}
        self._night_running = False
        self.last_auto: threading.Thread | None = None
        self._collect_running = False
        self.last_collect: threading.Thread | None = None
        self.now = datetime.now  # clock for collect_auto; tests replace it

    # ------------------------------------------------------------ per-session state

    def _session(self, sid) -> dict:
        sid = c.safe_session(sid) or "unknown"
        with self._lock:
            s = self._s.get(sid)
            if s is None:
                if len(self._s) >= MAX_SESSIONS:
                    self._s.pop(next(iter(self._s)))
                s = self._s[sid] = {"turns": 0, "tools": Counter(), "files": [], "captured": [], "accepted": 0,
                                    "done": [], "covered": 0, "first": "", "full_next": True,
                                    "started": datetime.now()}
            return s

    @staticmethod
    def _results(s: dict) -> int:
        return len(s["files"]) + len(s["captured"]) + s["accepted"]

    def meaningful(self, s: dict | None) -> bool:
        return bool(s) and (self._results(s) > 0 or bool(s["done"]))

    def record(self, session_id, written: str = "", accepted: bool = False) -> None:
        """A slash command (/kokpit note, todo, j, add) wrote to the vault in this session."""
        try:
            s = self._session(session_id)
            with self._lock:
                if written and written not in s["captured"] and len(s["captured"]) < 200:
                    s["captured"].append(written)
                if accepted:
                    s["accepted"] += 1
        except Exception:
            pass

    def _budget(self) -> int:
        try:
            return int(self.env.setting("context_max_chars", 1200))
        except (TypeError, ValueError):
            return 1200

    # ------------------------------------------------------------ pre_llm_call

    def pre_llm_call(self, session_id=None, user_message=None, is_first_turn=False, **_kw):
        """Full block on the first turn and after a compaction, related notes only on other turns."""
        try:
            s = self._session(session_id)
            text = _text(user_message)
            with self._lock:
                s["turns"] += 1
                if not s["first"] and text.strip():
                    s["first"] = text.strip()[:200]
                full = bool(is_first_turn) or s["full_next"]
                s["full_next"] = False
            budget = self._budget()
            if budget <= 0:
                return None
            state = self.env.state()
            if not state["cfg"]["modules"].get("brain", True) or state["vault"] is None:
                return None
            night = ""
            if full:
                try:
                    night = _night.brain_line(state.get("night_store"))
                except Exception:
                    night = ""
            if night and len(night) + 2 > budget:
                night = ""
            room = budget - (len(night) + 2 if night else 0)
            block = c.context_block(state["vault"], text, room, full=full, layout=state.get("layout"))
            if night:
                block = (block + "\n\n" + night) if block else "Kokpit brain context (read-only):\n" + night
                block = block[:budget]
            if not block:
                return None
            if state["mode"] == "demo":
                block = block.replace("Kokpit brain context (read-only):", "Kokpit brain context (demo data, read-only):", 1)
                if full:
                    hint = "\n\n" + c.DEMO_HINT
                    block = (block[: max(0, budget - len(hint))].rstrip() + hint) if len(hint) < budget else block
            return {"context": block}
        except c.NotReady:
            return None  # the tools explain the setup; a turn must never fail because of Kokpit
        except Exception:
            return None

    # ------------------------------------------------------------ on_session_start: optional night auto-run

    def on_session_start(self, session_id=None, **_kw):
        """With night_auto on, run the night pass in the background at most once per 20 hours.
        With collect_auto on, collect each enabled module whose last good run is older than 20 hours."""
        self._night_auto()
        self._collect_auto()
        return None

    def auto_jobs(self, state: dict) -> list:
        """(job, fn) pairs collect_auto may run: only modules that are on and have something to collect."""
        jobs = []
        mods = state["cfg"]["modules"]
        if mods.get("briefing") and (state.get("briefing") or {}).get("topics"):
            jobs.append((_collector.JOB, lambda st: _collector.collect(st)))
        return jobs

    def _collect_auto(self):
        try:
            if not c.as_bool(self.env.setting("collect_auto", False)):
                return None
            state = self.env.state()
            if state.get("mode") == "demo":
                return None
            jobs = self.auto_jobs(state)
            if not jobs:
                return None
            now = self.now()
            path = (state.get("store") or {}).get("path")
            if path is None or not any(_collector.due(path, j, now) for j, _ in jobs):
                return None
            with self._lock:
                if self._collect_running:
                    return None
                self._collect_running = True

            def work():
                try:
                    _collector.run_auto(state, jobs, now)
                except Exception:
                    pass  # run_auto records failures as runs rows; nothing reaches the session
                finally:
                    with self._lock:
                        self._collect_running = False

            t = threading.Thread(target=work, name="kokpit-collect", daemon=True)
            t.start()
            self.last_collect = t
        except Exception:
            return None
        return None

    def _night_auto(self):
        try:
            state = self.env.state()
            if not (state["cfg"].get("night") or {}).get("night_auto"):
                return None
            if not state["cfg"]["modules"].get("brain", True) or state["vault"] is None:
                return None
            if not _night.due_for_auto(state.get("night_store")):
                return None
            with self._lock:
                if self._night_running:
                    return None
                self._night_running = True

            def work():
                try:
                    _night.run(state)
                except Exception:
                    pass  # a failed background pass shows up as an old last run in /kokpit night
                finally:
                    with self._lock:
                        self._night_running = False

            t = threading.Thread(target=work, name="kokpit-night", daemon=True)
            t.start()
            self.last_auto = t
        except Exception:
            return None
        return None

    # ------------------------------------------------------------ post_tool_call

    def post_tool_call(self, tool_name="", args=None, result=None, session_id=None, task_id=None, status="", **_kw):
        try:
            s = self._session(session_id or task_id)
            with self._lock:
                s["tools"][str(tool_name or "?")] += 1
                if tool_name in WRITE_TOOLS and _ok(status) and isinstance(args, dict):
                    for k in PATH_KEYS:
                        v = args.get(k)
                        if isinstance(v, str) and v and len(s["files"]) < 200 and v not in s["files"]:
                            s["files"].append(v)
                if tool_name in ("kokpit_capture", "kokpit_night") and isinstance(result, str) and '"ok": true' in result:
                    m = re.search(r'"written": "([^"]+)"', result)
                    if m and m.group(1) not in s["captured"]:
                        s["captured"].append(m.group(1))
                    if tool_name == "kokpit_night" and '"status": "done"' in result:
                        s["accepted"] += 1
        except Exception:
            pass

    # ------------------------------------------------------------ post_compaction

    def post_compaction(self, session_id=None, old_session_id="", **_kw):
        """The earlier context block was summarised away: inject the full block again on the next turn."""
        try:
            new = c.safe_session(session_id) or "unknown"
            old = c.safe_session(old_session_id)
            with self._lock:
                if old and old != new and old in self._s and new not in self._s:
                    self._s[new] = self._s.pop(old)
            self._session(session_id)["full_next"] = True
        except Exception:
            pass

    # ------------------------------------------------------------ card and receipt

    def refs(self, s: dict, vault: Path, card_rel: str) -> list[str]:
        """Notes touched in this session (vault-relative, bounded), the session card first."""
        out = [card_rel]
        root = Path(vault).resolve()
        for p in s["captured"] + s["files"]:
            try:
                q = Path(os.path.expanduser(p))
                q = (q if q.is_absolute() else root / q).resolve()
                r = q.relative_to(root).as_posix()
            except (ValueError, OSError):
                continue  # outside the vault: not a note
            if r not in out:
                out.append(r)
            if len(out) >= REFS_MAX:
                break
        return out

    def card_lines(self, s: dict, vault: Path, layout: dict | None = None) -> tuple[str, list[str]]:
        first = c.clean(s["first"], 70)
        title = "Hermes session" if not first or SECRET.search(first) else first
        lines = [f"- Done: {d}" for d in s["done"][-2:]]
        where = []
        if s["captured"]:
            where.append("captured " + ", ".join(s["captured"][:4]))
        if s["files"]:
            shown = [_short_path(p) for p in s["files"][:4]]
            more = len(s["files"]) - len(shown)
            where.append("files " + ", ".join(shown) + (f" (+{more} more)" if more > 0 else ""))
        if s["accepted"]:
            where.append(f"{s['accepted']} night proposal(s) accepted")
        if where:
            lines.append("- Where: " + "; ".join(where) + ".")
        opens = [f"{t['title']}: {o['text']}" for t in c.read_vault(vault, layout=layout)["threads"] for o in t["open"]][:3]
        threads_name = layout["threads"]["name"] if layout else "Threads.md"
        lines.append(f"- Open (from {threads_name}): " + ("; ".join(c.clean(x, 90) for x in opens) if opens else "none listed") + ".")
        lines.append(f"- Harness: hermes, {s['turns']} turns. Written by the kokpit plugin without a model.")
        return title, lines

    def _summary(self, s: dict, title: str) -> str:
        if s["done"]:
            return s["done"][-1]
        bits = []
        if s["captured"]:
            bits.append(f"captured {len(s['captured'])} ({', '.join(s['captured'][:2])})")
        if s["files"]:
            bits.append(f"{len(s['files'])} file(s) written")
        if s["accepted"]:
            bits.append(f"{s['accepted']} night proposal(s) accepted")
        return c.clean(f"{title}: " + ", ".join(bits) + ".", RECEIPT_MAX)

    def _write(self, sid: str, s: dict, state: dict, event_id: str, summary: str) -> dict:
        vault = state["vault"]
        if vault is None or not Path(vault).is_dir():
            raise c.NotReady("vault not found", "run /kokpit setup <folder> to create it, or point vault_path at your notes folder")
        layout = state.get("layout") or c.resolve_layout(vault)
        if c.ambiguous(layout, "last_session"):
            raise c.NotReady("several Last-Session.md files in the vault",
                             "set plugins.entries.kokpit.settings.last_session_file")
        card_path = layout["last_session"]["path"]
        title, lines = self.card_lines(s, Path(vault), layout)
        c.write_card(vault, sid, title, lines, path=card_path)
        card_rel = c.rel(vault, card_path)
        refs = self.refs(s, Path(vault), card_rel)
        rec = c.write_receipt(vault, event_id, summary, refs, sid, folder=layout["receipts"]["path"])
        return {"card": card_rel, "receipt": rec["path"], "receipt_status": rec["status"], "refs": refs,
                "where": "demo vault" if state["mode"] == "demo" else "vault"}

    def done(self, state: dict, session_id, text: str) -> dict:
        """Explicit close of a result: this session's card plus a short receipt. Raises NotReady on bad input."""
        text = c.clean(c.CONTROL.sub(" ", str(text or "")))
        if not text:
            raise c.NotReady("no summary given", f"usage: {DONE_USAGE}")
        if SECRET.search(text):
            raise c.NotReady("the summary looks like it contains a secret", "leave keys and passwords out of the receipt")
        if len(text) > RECEIPT_MAX:
            text = text[: RECEIPT_MAX - 3].rstrip() + "..."
        sid = c.safe_session(session_id) or f"manual-{datetime.now():%Y%m%d}"
        s = self._session(sid)
        with self._lock:
            if text not in s["done"]:
                s["done"].append(text)
            s["covered"] = self._results(s)
        eid = f"hermes-done-{sid}-" + hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]
        return self._write(sid, s, state, eid, text)

    # ------------------------------------------------------------ on_session_finalize

    def on_session_finalize(self, session_id=None, platform="", **_kw):
        sid = c.safe_session(session_id)
        if not sid:
            return None
        with self._lock:
            s = self._s.pop(sid, None)
        if s is None or not self.meaningful(s):
            return None  # chat or lookups only: nothing is written
        try:
            if not _on(self.env.setting("session_card", True)):
                return None
            state = self.env.state()
            if not state["cfg"]["modules"].get("brain", True):
                return None
            if state["vault"] is None or not Path(state["vault"]).is_dir():
                return None
            title = self.card_lines(s, Path(state["vault"]), state.get("layout"))[0]
            if s["done"] and self._results(s) <= s["covered"]:
                eid, summary = f"hermes-done-{sid}-" + hashlib.sha256(s["done"][-1].encode("utf-8")).hexdigest()[:12], s["done"][-1]
            else:
                eid, summary = f"hermes-session-{sid}", self._summary(s, title)
            out = self._write(sid, s, state, eid, summary)
        except c.NotReady as e:
            return {"message": f"Kokpit: session card not written, {e.error}. Fix: {e.fix}."} \
                if "Last-Session" in e.error else None
        except Exception as e:
            return {"message": f"Kokpit: session card not written ({type(e).__name__})."}
        return {"message": f"Kokpit: session card saved to {out['card']} in the {out['where']} "
                           f"(receipt {out['receipt']}, {out['receipt_status']})."}
