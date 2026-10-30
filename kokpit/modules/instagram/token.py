"""Token lookup and the explicit refresh helper.

Lookup order: the local token file (written only by ``kokpit instagram refresh-token``), the environment variable
KOKPIT_INSTAGRAM_TOKEN, then ``[instagram] token`` in the git-ignored kokpit.toml. The token is only handed to the client;
nothing here prints it.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ...config import Config
from .client import REFRESH_URL, ApiError, Client

ENV = "KOKPIT_INSTAGRAM_TOKEN"


def token_file(cfg: Config) -> Path:
    return cfg.resolve(cfg.instagram["token_file"])


def resolve(cfg: Config) -> str:
    f = token_file(cfg)
    if f.is_file():
        t = f.read_text(encoding="utf-8").strip()
        if t:
            return t
    return os.environ.get(ENV, "").strip() or cfg.instagram.get("token", "")


def configured(cfg: Config) -> bool:
    return bool(resolve(cfg))


def refresh(cfg: Config, opener=None, now: datetime | None = None) -> dict:
    """Ask for a fresh long-lived token (valid 60 days) and store it in the token file (mode 600).

    Returns ``{"expires_at": date, "expires_in_days": n, "stored": file name}``; the token itself is never returned.
    Raises ApiError (message without token) when the API refuses, e.g. a token younger than 24 hours or already expired.
    """
    token = resolve(cfg)
    if not token:
        raise ApiError("auth", "no token configured")
    resp = Client(token, opener).get(REFRESH_URL, grant_type="ig_refresh_token")
    new = str(resp.get("access_token") or "")
    secs = int(resp.get("expires_in") or 0)
    if not new:
        raise ApiError("call", "refresh answered without a token")
    f = token_file(cfg)
    f.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(f, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(new + "\n")
    os.chmod(f, 0o600)
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    return {"expires_at": (now + timedelta(seconds=secs)).strftime("%Y-%m-%d"), "expires_in_days": secs // 86400, "stored": f.name}
