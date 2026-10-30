"""Token and chat id lookup. The environment variable wins over ``[bot] token``; nothing here prints either value."""
from __future__ import annotations

import os

from ...config import Config

ENV = "KOKPIT_TELEGRAM_TOKEN"


def resolve(cfg: Config) -> str:
    return os.environ.get(ENV, "").strip() or cfg.bot.get("token", "")


def chat_id(cfg: Config) -> str:
    return cfg.bot.get("chat_id", "")


def missing(cfg: Config) -> list[str]:
    out = []
    if not resolve(cfg):
        out.append("no token (set KOKPIT_TELEGRAM_TOKEN or [bot] token)")
    if not chat_id(cfg):
        out.append("no chat_id ([bot] chat_id)")
    return out
