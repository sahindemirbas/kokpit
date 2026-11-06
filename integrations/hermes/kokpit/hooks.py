"""Hooks: brain context before a turn, a session card and a receipt when the session ends.

No model calls and no network: the card is built from rules (which tools ran, which files
were written, what the Threads file still lists as open).
"""
from __future__ import annotations

import os
import re
import threading
from collections import Counter
from datetime import datetime
from pathlib import Path

from . import contract as c
from . import night as _night

WRITE_TOOLS = {"write_file", "patch", "kokpit_capture"}
PATH_KEYS = ("path", "file_path", "filename", "target_file")
MAX_SESSIONS = 64
# key-like strings: provider key prefixes, private key blocks, "password: ..." style assignments
SECRET = re.compile(r"\bs[k]-[A-Za-z0-9_-]{6,}|\bgh[p]_[A-Za-z0-9]{6,}|\bxox[abprs]-|\bAKIA[0-9A-Z]{8,}|-----BEGIN"
                    r"|\b(?:tok[e]n|api[_-]?k[e]y|secret|passw(?:or)?d)\s*[=:]", re.I)


def _short_path(p: str) -> str:
    home = os.path.expanduser("~")
    return "~" + p[len(home):] if home and p.startswith(home + os.sep) else p


def _text(message) -> str:
    if isinstance(message, dict):
        message = message.get("content", "")
    if isinstance(message, list):
        message = " ".join(str(x.get("text", "")) for x in message if isinstance(x, dict))
    return message if isinstance(message, str) else str(message or "")


class Hooks:
    def __init__(self, env: c.Env):
        self.env = env
        self._lock = threading.Lock()
        self._s: dict[str, dict] = {}
        self._night_running = False
        self.last_auto: threading.Thread | None = None

    # ------------------------------------------------------------ per-session state

    def _session(self, sid) -> dict:
        sid = c.safe_session(sid) or "unknown"
        with self._lock:
            s = self._s.get(sid)
            if s is None:
                if len(self._s) >= MAX_SESSIONS:
                    self._s.pop(next(iter(self._s)))
                s = self._s[sid] = {"turns": 0, "tools": Counter(), "files": [], "captured": [],
                                    "first": "", "full_next": True, "started": datetime.now()}
            return s

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
                    s["first"] = text.strip()
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
            return {"context": block}
        except c.NotReady:
            return None  # the tools explain the setup; a turn must never fail because of Kokpit
        except Exception:
            return None

    # ------------------------------------------------------------ on_session_start: optional night auto-run

    def on_session_start(self, session_id=None, **_kw):
        """With night_auto on, run the night pass in the background at most once per 20 hours."""
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
                if tool_name in WRITE_TOOLS and status in ("", "ok", "success", None) and isinstance(args, dict):
                    for k in PATH_KEYS:
                        v = args.get(k)
                        if isinstance(v, str) and v and len(s["files"]) < 200 and v not in s["files"]:
                            s["files"].append(v)
                if tool_name in ("kokpit_capture", "kokpit_night") and isinstance(result, str) and '"ok": true' in result:
                    m = re.search(r'"written": "([^"]+)"', result)
                    if m and m.group(1) not in s["captured"]:
                        s["captured"].append(m.group(1))
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

    # ------------------------------------------------------------ on_session_finalize

    def card_lines(self, s: dict, vault: Path, layout: dict | None = None) -> tuple[str, list[str]]:
        first = c.clean(s["first"], 70)
        title = "Hermes session" if not first or SECRET.search(first) else first
        tools = ", ".join(f"{n} x{k}" if k > 1 else n for n, k in s["tools"].most_common(8)) or "none"
        lines = [f"- Harness: hermes. Turns: {s['turns']}. Tools: {tools}."]
        if s["files"]:
            shown = [_short_path(p) for p in s["files"][:6]]
            more = len(s["files"]) - len(shown)
            lines.append("- Files written: " + ", ".join(shown) + (f" (+{more} more)" if more > 0 else "") + ".")
        if s["captured"]:
            lines.append("- Captured: " + ", ".join(s["captured"][:5]) + ".")
        opens = [f"{t['title']}: {o['text']}" for t in c.read_vault(vault, layout=layout)["threads"] for o in t["open"]][:3]
        threads_name = layout["threads"]["name"] if layout else "Threads.md"
        lines.append(f"- Open (from {threads_name}): " + ("; ".join(c.clean(x, 90) for x in opens) if opens else "none listed") + ".")
        lines.append("- Written by the kokpit plugin without a model; edit it if the summary needs more.")
        return title, lines

    def on_session_finalize(self, session_id=None, platform="", **_kw):
        sid = c.safe_session(session_id)
        if not sid:
            return None
        with self._lock:
            s = self._s.pop(sid, None)
        if not s or s["turns"] == 0:
            return None
        try:
            on = self.env.setting("session_card", True)
            if str(on).strip().lower() in ("false", "0", "off", "no"):
                return None
            state = self.env.state()
            if not state["cfg"]["modules"].get("brain", True):
                return None
            vault = state["vault"]
            if vault is None or not Path(vault).is_dir():
                return None
            layout = state.get("layout") or c.resolve_layout(vault)
            if c.ambiguous(layout, "last_session"):
                return {"message": "Kokpit: session card not written, several Last-Session.md files in the vault; "
                                   "set plugins.entries.kokpit.settings.last_session_file."}
            card_path = layout["last_session"]["path"]
            title, lines = self.card_lines(s, Path(vault), layout)
            card = c.write_card(vault, sid, title, lines, path=card_path)
            card_rel = c.rel(vault, card_path)
            rec = c.write_receipt(vault, f"hermes-session-{sid}", card, [card_rel, *s["captured"][:5]], sid,
                                  folder=layout["receipts"]["path"])
        except c.NotReady:
            return None
        except Exception as e:
            return {"message": f"Kokpit: session card not written ({type(e).__name__})."}
        where = "demo vault" if state["mode"] == "demo" else "vault"
        return {"message": f"Kokpit: session card saved to {card_rel} in the {where} ({rec['path']})."}
