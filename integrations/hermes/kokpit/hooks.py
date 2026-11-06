"""Hooks: brain context before a turn; a session card, a receipt and a session log line when the session ends.

By default no model calls and no network: the card is built from rules (which tools ran, which files
were written, what the Threads file still lists as open). With card_model on, the host ctx.llm adds
decisions and next steps that must quote the user's own messages word for word (see card.py).
"""
from __future__ import annotations

import os
import re
import threading
from collections import Counter, deque
from datetime import datetime
from pathlib import Path

from . import card as _card
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


def _on(value) -> bool:
    return str(value).strip().lower() not in ("false", "0", "off", "no", "")


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
                                    "first": "", "full_next": True, "started": datetime.now(),
                                    "msgs": deque(maxlen=_card.MSG_KEEP)}
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
                if text.strip() and not SECRET.search(text):  # memory only, never on disk; key-like text never kept
                    s["msgs"].append(text.strip()[:_card.MSG_MAX])
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

    def model_card(self, s: dict, title: str, lines: list[str]) -> tuple[list[str], dict]:
        """card_model on: decisions and next steps quoted from the user's messages; rule card on any failure."""
        try:
            llm = self.env.llm() if callable(self.env.llm) else self.env.llm
        except Exception:
            llm = None
        extra, info = _card.model_lines(llm, list(s.get("msgs") or ()))
        extra = [x for x in extra if not SECRET.search(x)]
        if not extra:
            return lines, info
        rule = [x for x in lines if not x.startswith("- Written by the kokpit plugin")]
        note = (f"- Decisions and next steps by the host model, each quoted from your messages "
                f"({info.get('rejected', 0)} unquoted lines rejected).")
        return rule[:1] + extra + rule[1:] + [note], info

    def log_line(self, s: dict, sid: str, title: str, lines: list[str], now: datetime) -> str:
        tools = sum(s["tools"].values())
        nxt = [m.group(1) for m in c.NEXT_LINE.finditer("\n".join(lines))][:1]
        return (f"{now:%Y-%m-%d %H:%M} · {title} · {sid} · {s['turns']} turns, {tools} tool calls"
                + (f", {len(s['files'])} files" if s["files"] else "")
                + (f", captured {', '.join(s['captured'][:3])}" if s["captured"] else "")
                + (f". Next: {nxt[0]}" if nxt else ""))

    def on_session_finalize(self, session_id=None, platform="", **_kw):
        sid = c.safe_session(session_id)
        if not sid:
            return None
        with self._lock:
            s = self._s.pop(sid, None)
        if not s or s["turns"] == 0:
            return None
        try:
            if not _on(self.env.setting("session_card", True)):
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
            info = {"model": "off"}
            if _on(self.env.setting("card_model", False)):
                lines, info = self.model_card(s, title, lines)
            now = datetime.now()
            card = c.write_card(vault, sid, title, lines, now=now, path=card_path)
            card_rel = c.rel(vault, card_path)
            rec = c.write_receipt(vault, f"hermes-session-{sid}", card, [card_rel, *s["captured"][:5]], sid,
                                  folder=layout["receipts"]["path"])
            logged = ""
            if _on(self.env.setting("session_log", True)):
                try:
                    logged = c.append_session_log(vault, self.log_line(s, sid, title, lines, now), now=now)
                except OSError as e:
                    logged = f"not written ({type(e).__name__})"
        except c.NotReady:
            return None
        except Exception as e:
            return {"message": f"Kokpit: session card not written ({type(e).__name__})."}
        where = "demo vault" if state["mode"] == "demo" else "vault"
        msg = f"Kokpit: session card saved to {card_rel} in the {where} ({rec['path']})"
        if logged:
            msg += f"; session log {logged}"
        if info.get("model") not in ("off", "on"):
            msg += f"; card model {info['model']}, rule card used"
        return {"message": msg + "."}
