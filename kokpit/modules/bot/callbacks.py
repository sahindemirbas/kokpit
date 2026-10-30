"""Callback dispatcher: inline-button data looks like ``prefix:rest``; a registry maps prefix to handler.

A handler is ``fn(ctx, rest) -> str | None`` (the text shown to the user as a toast). Step 1b plugs the
Instagram approval in with ``register("igapprove", handler)``. Unknown prefixes answer "unknown action".
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

UNKNOWN = "unknown action"
FAILED = "action failed"


@dataclass
class Context:
    cfg: Any
    conn: Any
    client: Any
    chat_id: str
    message_id: int | None = None


Handler = Callable[[Context, str], "str | None"]
REGISTRY: dict[str, Handler] = {}


def register(prefix: str, fn: Handler) -> None:
    if not prefix or ":" in prefix:
        raise ValueError("prefix must be non-empty and contain no colon")
    REGISTRY[prefix] = fn


def dispatch(ctx: Context, data: str, registry: dict[str, Handler] | None = None) -> str:
    reg = REGISTRY if registry is None else registry
    prefix, _, rest = (data or "").partition(":")
    fn = reg.get(prefix)
    if fn is None:
        return UNKNOWN
    try:
        return fn(ctx, rest) or "done"
    except Exception:  # the toast stays generic; details never go to the chat
        return FAILED
