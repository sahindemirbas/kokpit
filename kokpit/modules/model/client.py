"""Optional model connection: one OpenAI-compatible chat-completions client, off by default.

Rules (docs/connections.md, Model API):
- ``[model] enabled`` is the master switch, ``[model.uses]`` has one switch per purpose; all false by default.
- The key is read from the environment variable named in ``[model] key_env`` and never from the toml file.
- A disabled use or a missing key raises ``ModelDisabled`` before any network call.
- Every call is recorded in the ``runs`` table (job ``model:<purpose>``) with the declared fields it sends.
- Nothing here prints or stores the key.
- ``[model] provider = "hermes"`` sends the same prompt to a local ``hermes chat`` command instead;
  no key is involved and the accounts used are the ones Hermes itself is connected to.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import urllib.error
import urllib.parse
import urllib.request

from ...config import Config
from ...db import connect
from ..health import store

# purpose -> the fields that leave the machine for that use (shown on the Connections screen)
USES: dict[str, str] = {
    "events_translate": "event title and description",
    "events_classify": "event title, description and venue name",
    "digest": "titles, tags, short summaries and measured facts (stars, likes...) of selected items",
    "night": "text of the notes being organised",
    "career_weekly": "weekly listing counts, top companies, listing titles, requested skills and the profile's target roles, locations, seniority and skills (no name or contact)",
    "podcast": "titles, tags and summaries of the day's digest items, the last lines of the previous segment, the optional listener first name and, only with [briefing.podcast] brain_context = true, the bounded personal context",
}


class ModelDisabled(Exception):
    """The use is off, or no key is available. No network call was made."""


class ModelError(Exception):
    """The provider call failed. The message never contains the key."""


def key(cfg: Config) -> str:
    if _is_hermes(cfg):
        return ""
    return os.environ.get(cfg.model["key_env"], "").strip()


def _is_hermes(cfg: Config) -> bool:
    return cfg.model.get("provider") == "hermes"


def _hermes_bin(cfg: Config) -> str:
    return cfg.model.get("hermes_bin") or "hermes"


def use_enabled(cfg: Config, purpose: str) -> bool:
    m = cfg.model
    if not (m["enabled"] and m["uses"].get(purpose)):
        return False
    if _is_hermes(cfg):
        return shutil.which(_hermes_bin(cfg)) is not None
    return bool(m["base_url"] and m["model"] and key(cfg))


def check(cfg: Config, purpose: str) -> None:
    m = cfg.model
    if purpose not in USES:
        raise ModelDisabled(f"unknown model use '{purpose}'")
    if not m["enabled"]:
        raise ModelDisabled("model connection is off ([model] enabled = false)")
    if not m["uses"].get(purpose):
        raise ModelDisabled(f"model use '{purpose}' is off ([model.uses] {purpose} = false)")
    if _is_hermes(cfg):
        if shutil.which(_hermes_bin(cfg)) is None:
            raise ModelDisabled("hermes command not found")
        return
    if not m["base_url"] or not m["model"]:
        raise ModelDisabled("[model] base_url and model are not set")
    if not key(cfg):
        raise ModelDisabled(f"no key in the environment variable {m['key_env']}")


def _record(cfg: Config, purpose: str, started: str, ok: bool, info: dict, warnings: list[str]) -> None:
    try:
        conn = connect(cfg.db)
        try:
            store.record(conn, f"model:{purpose}", started, ok, rows=1 if ok else 0, info=info, warnings=warnings)
        finally:
            conn.close()
    except Exception:  # recording must never hide the result
        pass


def _scrub(text: str, secret: str) -> str:
    return text.replace(secret, "***") if secret else text


_ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)")


def _hermes_complete(cfg: Config, purpose: str, system: str, user: str) -> str:
    """One `hermes chat` call; the prompt goes in on stdin, the answer comes back on stdout.

    Which account and model answer is Hermes's own setting; Kokpit passes no key.
    """
    m = cfg.model
    cmd = [_hermes_bin(cfg), "chat", "--oneshot", "-Q", "--query-file", "-", "--source", "tool"]
    if m["model"]:
        cmd += ["-m", m["model"]]
    if m.get("hermes_provider"):
        cmd += ["--provider", m["hermes_provider"]]
    started = store.now_iso()
    info = {"purpose": purpose, "declared_fields": USES[purpose], "provider": "hermes",
            "model": m["model"] or "hermes default", "chars_sent": len(system) + len(user)}
    prompt = (system + "\n\n" + user) if system else user
    try:
        proc = subprocess.run(cmd, input=prompt, capture_output=True, text=True, timeout=m["timeout"])
        if proc.returncode != 0:
            err = _ANSI.sub("", proc.stderr or proc.stdout or "").strip()
            raise RuntimeError(f"hermes exited with {proc.returncode}: {err[:160]}")
        text = _ANSI.sub("", proc.stdout or "").strip()
        if not text:
            raise ValueError("empty answer")
    except Exception as exc:
        msg = f"{type(exc).__name__}: {str(exc)[:160]}"
        _record(cfg, purpose, started, False, {**info, "error": msg}, [f"model {purpose}: {msg}"])
        raise ModelError("empty answer" if isinstance(exc, ValueError) else msg) from None
    _record(cfg, purpose, started, True, info, [])
    return text


def complete(cfg: Config, purpose: str, system: str, user: str, max_tokens: int = 512) -> str:
    check(cfg, purpose)
    if _is_hermes(cfg):
        return _hermes_complete(cfg, purpose, system, user)
    m, secret = cfg.model, key(cfg)
    started = store.now_iso()
    info = {"purpose": purpose, "declared_fields": USES[purpose], "model": m["model"], "chars_sent": len(system) + len(user)}
    body = json.dumps({"model": m["model"], "max_tokens": int(max_tokens), "temperature": 0,
                       "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}).encode()
    req = urllib.request.Request(m["base_url"].rstrip("/") + "/chat/completions", data=body, method="POST",
                                 headers={"Content-Type": "application/json", "Authorization": "Bearer " + secret})
    try:
        with urllib.request.urlopen(req, timeout=m["timeout"]) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        text = data["choices"][0]["message"]["content"]
        if not isinstance(text, str):
            raise ValueError("no text in the answer")
    except Exception as exc:
        msg = _scrub(f"{type(exc).__name__}: {str(exc)[:160]}", secret)
        _record(cfg, purpose, started, False, {**info, "error": msg}, [f"model {purpose}: {msg}"])
        raise ModelError(msg) from None
    _record(cfg, purpose, started, True, info, [])
    return text


def view(cfg: Config) -> dict:
    """What the Connections screen shows. Names, switches, fields and times only; never the key."""
    m = cfg.model
    uses = []
    for purpose, fields in USES.items():
        run = _last_ok(cfg, purpose)
        uses.append({"purpose": purpose, "on": bool(m["uses"].get(purpose)), "fields": fields, "last_success": run})
    if _is_hermes(cfg):
        return {"enabled": m["enabled"], "provider": "hermes", "base_host": _hermes_bin(cfg),
                "model": m["model"] or "hermes default", "key_env": "", "key_present": False,
                "command_found": shutil.which(_hermes_bin(cfg)) is not None, "uses": uses}
    return {"enabled": m["enabled"], "provider": "openai_compatible", "base_host": _host(m["base_url"]), "model": m["model"],
            "key_env": m["key_env"], "key_present": bool(key(cfg)), "uses": uses}


def _host(url: str) -> str:
    return urllib.parse.urlparse(url).netloc if url else ""


def _last_ok(cfg: Config, purpose: str) -> str | None:
    if not cfg.db.exists():
        return None
    conn = connect(cfg.db, readonly=True)
    try:
        r = conn.execute("SELECT finished FROM runs WHERE job=? AND ok=1 ORDER BY id DESC LIMIT 1", (f"model:{purpose}",)).fetchone()
    except Exception:
        return None
    finally:
        conn.close()
    return r["finished"] if r else None
