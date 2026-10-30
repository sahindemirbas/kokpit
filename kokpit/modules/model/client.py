"""Optional model connection: one OpenAI-compatible chat-completions client, off by default.

Rules (docs/connections.md, Model API):
- ``[model] enabled`` is the master switch, ``[model.uses]`` has one switch per purpose; all false by default.
- The key is read from the environment variable named in ``[model] key_env`` and never from the toml file.
- A disabled use or a missing key raises ``ModelDisabled`` before any network call.
- Every call is recorded in the ``runs`` table (job ``model:<purpose>``) with the declared fields it sends.
- Nothing here prints or stores the key.
"""
from __future__ import annotations

import json
import os
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
    "digest": "titles, tags and short summaries of selected items",
    "night": "text of the notes being organised",
}


class ModelDisabled(Exception):
    """The use is off, or no key is available. No network call was made."""


class ModelError(Exception):
    """The provider call failed. The message never contains the key."""


def key(cfg: Config) -> str:
    return os.environ.get(cfg.model["key_env"], "").strip()


def use_enabled(cfg: Config, purpose: str) -> bool:
    m = cfg.model
    return bool(m["enabled"] and m["uses"].get(purpose) and m["base_url"] and m["model"] and key(cfg))


def check(cfg: Config, purpose: str) -> None:
    m = cfg.model
    if purpose not in USES:
        raise ModelDisabled(f"unknown model use '{purpose}'")
    if not m["enabled"]:
        raise ModelDisabled("model connection is off ([model] enabled = false)")
    if not m["uses"].get(purpose):
        raise ModelDisabled(f"model use '{purpose}' is off ([model.uses] {purpose} = false)")
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


def complete(cfg: Config, purpose: str, system: str, user: str, max_tokens: int = 512) -> str:
    check(cfg, purpose)
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
    return {"enabled": m["enabled"], "base_host": _host(m["base_url"]), "model": m["model"], "key_env": m["key_env"],
            "key_present": bool(key(cfg)), "uses": uses}


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
