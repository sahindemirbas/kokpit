"""Connect an Instagram account: the step guide, one read-only token check, storing the token, switching the module on.

Shared by ``kokpit instagram setup|check`` and the Social tab form (POST /api/instagram/token). The token is never
returned, printed or logged; it is written only to the token file (mode 600), never to kokpit.toml.

Meta facts used here (checked against the official docs, see docs/connections.md "Instagram analysis"):
``GET graph.instagram.com/me?fields=user_id,username,account_type`` answers for an Instagram user token;
``account_type`` is ``Business`` or ``Media_Creator``. A token made with "Generate token" in the App Dashboard is
long-lived (60 days). ``content_publishing_limit`` needs ``instagram_business_content_publish``.
"""
from __future__ import annotations

import os
import re

from ...config import Config
from .client import ApiError, Client
from . import token as ig_token

PORTAL = "https://developers.facebook.com/apps"
DOCS = "https://developers.facebook.com/docs/instagram-platform/instagram-api-with-instagram-login/create-a-meta-app-with-instagram"
PROFESSIONAL = {"BUSINESS", "MEDIA_CREATOR"}
MAX_TOKEN = 1000
CRON = "0 7,19 * * * cd {dir} && python -m kokpit instagram collect"

STEPS = [
    "Your Instagram account must be a professional account (Business or Creator) and public. "
    "In the Instagram app: Settings, then Account type and tools, then switch to a professional account.",
    f"Open {PORTAL} and click Create app. Pick the use case Other, then the app type Business, give it a name and click Create.",
    "In the new app's dashboard find the product Instagram and click Set up. "
    "This adds \"API setup with Instagram login\" (left menu: Instagram).",
    "On \"API setup with Instagram login\", under Generate access tokens, click Add account and log in with your Instagram account. "
    "If Instagram asks you to accept a tester invite, accept it (Instagram app: Settings, Apps and websites, Tester invites).",
    "Click Generate token next to your account, log in again if asked, allow the permissions, and copy the token. "
    "This token is valid for 60 days.",
    "Paste the token here. Kokpit checks it with one read-only call and stores it in a local file only you can read.",
]


class ConnectError(Exception):
    """A check failed. ``advice`` is one plain sentence with the next step; ``kind`` follows ApiError."""

    def __init__(self, kind: str, advice: str, detail: str = ""):
        super().__init__(advice)
        self.kind, self.advice, self.detail = kind, advice, detail


def advice(exc: ApiError) -> str:
    """Turn an API error into the next step. The message is already cleaned of the token by the client."""
    msg = str(exc).lower()
    if exc.kind == "network":
        return "Could not reach graph.instagram.com. Check the internet connection of this machine and try again."
    if "developer role" in msg or "tester" in msg:
        return ("This account is not allowed to use the app yet. In the app dashboard add it under Instagram, "
                "API setup with Instagram login (or App roles, Roles), and accept the invite in the Instagram app, then generate a new token.")
    if exc.code == 190 or "expired" in msg or "session has been invalidated" in msg:
        return ("The token is expired or not valid. Generate a new one: app dashboard, Instagram, "
                "API setup with Instagram login, Generate token. Copy the whole token, with no spaces.")
    if exc.code == 10 or (exc.code is not None and 200 <= exc.code <= 299) or "permission" in msg:
        return ("The token is missing a permission. Generate it again and allow instagram_business_basic and "
                "instagram_business_manage_insights (and instagram_business_content_publish if you publish).")
    if exc.kind == "rate":
        return "Instagram's rate limit is reached. Wait an hour and try again."
    return "Instagram refused the check. Generate a new token in the app dashboard and try again."


def clean_token(raw) -> str:
    if not isinstance(raw, str):
        raise ConnectError("input", "Paste the token as text.")
    t = raw.strip()
    if not t:
        raise ConnectError("input", "The token is empty. Copy it from Generate token in the app dashboard.")
    if len(t) > MAX_TOKEN or not re.fullmatch(r"[^\s\"'<>]+", t):
        raise ConnectError("input", "This does not look like a token. Copy only the token, with no spaces or quotes.")
    return t


def account(token: str, opener=None) -> dict:
    """One read-only GET of /me. Returns username, account type and whether it is a professional account."""
    try:
        resp = Client(token, opener).get("me", fields="user_id,username,account_type")
    except ApiError as exc:
        raise ConnectError(exc.kind, advice(exc), str(exc)) from None
    if isinstance(resp.get("data"), list) and resp["data"]:
        resp = resp["data"][0] or {}
    kind = str(resp.get("account_type") or "").upper()
    return {"username": str(resp.get("username") or ""), "user_id": str(resp.get("user_id") or resp.get("id") or ""),
            "account_type": kind or "unknown", "professional": kind in PROFESSIONAL}


def can_publish(token: str, user_id: str, opener=None) -> bool | None:
    """Read-only probe of content_publishing_limit, which needs instagram_business_content_publish.
    True: allowed. False: the API refused the permission. None: could not tell (network, rate, other)."""
    try:
        Client(token, opener).get(f"{user_id or 'me'}/content_publishing_limit", fields="quota_usage")
        return True
    except ApiError as exc:
        if exc.kind == "auth":
            return False
        return None


def warning(acc: dict) -> str:
    if acc["professional"]:
        return ""
    return (f"The account type is {acc['account_type']}, not Business or Creator, so insights will not work. "
            "In the Instagram app: Settings, Account type and tools, switch to a professional account. The token can stay.")


def store(cfg: Config, token: str) -> str:
    """Write the token file (mode 600). Returns the file name only."""
    f = ig_token.token_file(cfg)
    ig_token.write_file(f, token)
    return f.name


def enable(cfg: Config) -> list[str]:
    """Set [modules] instagram = true and [instagram] analyse = true in kokpit.toml (nothing else, never the token)."""
    from ... import tomledit, wizard
    text = cfg.path.read_text(encoding="utf-8")
    changed = []
    if not cfg.modules.get("instagram"):
        text = tomledit.set_value(text, "modules", "instagram", True); changed.append("modules.instagram")
    if not cfg.instagram.get("analyse"):
        text = tomledit.set_value(text, "instagram", "analyse", True); changed.append("instagram.analyse")
    if changed:
        tomledit.write_atomic(cfg.path, text)
        wizard.reload(cfg)
    return changed


def connect(cfg: Config, raw_token, opener=None) -> dict:
    """Check the token, store it, switch analysis on. Raises ConnectError with advice; nothing is stored then."""
    t = clean_token(raw_token)
    acc = account(t, opener)
    stored = store(cfg, t)
    changed = enable(cfg)
    return {"ok": True, "username": acc["username"], "account_type": acc["account_type"],
            "professional": acc["professional"], "warning": warning(acc), "stored": stored, "changed": changed}


def check(cfg: Config, opener=None) -> dict:
    """Check the token Kokpit would use now. Read-only; never writes."""
    t = ig_token.resolve(cfg)
    if not t:
        raise ConnectError("missing", "No token yet. Run: python -m kokpit instagram setup")
    acc = account(t, opener)
    out = {"ok": True, "username": acc["username"], "account_type": acc["account_type"],
           "professional": acc["professional"], "warning": warning(acc), "source": source(cfg),
           "module_on": bool(cfg.modules.get("instagram")), "analyse": bool(cfg.instagram.get("analyse")),
           "publish": bool(cfg.instagram.get("publish")), "can_publish": None}
    if out["publish"]:
        out["can_publish"] = can_publish(t, acc["user_id"], opener)
    return out


def source(cfg: Config) -> str:
    f = ig_token.token_file(cfg)
    if f.is_file() and f.read_text(encoding="utf-8").strip():
        return f"token file {f.name}"
    if os.environ.get(ig_token.ENV, "").strip():
        return f"environment variable {ig_token.ENV}"
    return "[instagram] token in kokpit.toml"
