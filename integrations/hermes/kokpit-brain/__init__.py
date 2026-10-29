"""Kokpit brain context for Hermes Agent. Read-only.

On every user turn (``pre_llm_call``) this plugin reads the Kokpit vault
(Threads.md, Last-Session.md, To-Do.md, notes/) and returns a short text block
that Hermes appends to the user message. It never writes to the vault, never
calls a network, and needs no model.

Install: copy or symlink this folder to ``~/.hermes/plugins/kokpit-brain/``.
Configure (one of):
  - ``KOKPIT_CONFIG=/path/to/kokpit.toml`` in Hermes' environment, or
  - ``KOKPIT_VAULT=/path/to/vault`` to skip the config entirely, or
  - a ``kokpit.toml`` in one of Kokpit's default locations.
``KOKPIT_CONTEXT_MAX_CHARS`` (default 3500) caps the block.

Kokpit itself must be importable: either ``pip install -e /path/to/kokpit`` into
Hermes' Python, or set ``KOKPIT_SRC=/path/to/kokpit`` (the repo root).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

PLUGIN_NAME = "kokpit-brain"


def _vault() -> Path | None:
    env = os.environ.get("KOKPIT_VAULT")
    if env:
        return Path(env).expanduser()
    src = os.environ.get("KOKPIT_SRC")
    if src and src not in sys.path:
        sys.path.insert(0, src)
    try:
        from kokpit import config as cfgmod
    except ImportError:
        return None
    path = cfgmod.find_config(os.environ.get("KOKPIT_CONFIG"))
    if path is None or not Path(path).exists():
        return None
    return cfgmod.load(path).vault


def _pre_llm_call(**kw) -> dict | None:
    vault = _vault()
    if vault is None or not vault.exists():
        return None
    try:
        from kokpit.modules.brain.context import build_context
    except ImportError:
        return None
    message = kw.get("user_message")
    if isinstance(message, dict):
        message = message.get("content", "")
    if not isinstance(message, str):
        message = str(message or "")
    max_chars = int(os.environ.get("KOKPIT_CONTEXT_MAX_CHARS", "3500"))
    text = build_context(vault, message, max_chars=max_chars)
    return {"context": text} if text else None


def register(ctx) -> None:
    ctx.register_hook("pre_llm_call", _pre_llm_call)
