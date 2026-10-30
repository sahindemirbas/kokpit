"""Minimal Instagram API (Instagram Login) client over urllib. GET only in this module.

The token is sent as a query parameter (the API's own convention). It is removed from every error message
and never printed or logged. ``opener`` is any callable ``opener(url, timeout=...)`` returning a file-like
object, so tests can stub the network.
"""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request

BASE = "https://graph.instagram.com/v23.0/"
REFRESH_URL = "https://graph.instagram.com/refresh_access_token"
TIMEOUT = 25

RATE_CODES = {4, 17, 32, 613, 80002, 80006}
AUTH_CODES = {10, 102, 190}


class ApiError(Exception):
    """kind: auth (token missing, expired, no permission), rate (limit reached), network, call (this call only)."""

    def __init__(self, kind: str, message: str, code: int | None = None):
        super().__init__(message)
        self.kind, self.code = kind, code

    @property
    def fatal(self) -> bool:
        return self.kind in ("auth", "rate", "network")


def classify(status: int | None, code: int | None) -> str:
    if status == 429 or code in RATE_CODES:
        return "rate"
    if status in (401, 403) or code in AUTH_CODES or (code is not None and 200 <= code <= 299):
        return "auth"
    return "call"


class Client:
    def __init__(self, token: str, opener=None):
        self.token = token
        self.opener = opener or urllib.request.urlopen
        self.calls = 0

    def clean(self, text: str) -> str:
        text = re.sub(r"access_token=[^&\s\"']*", "access_token=***", str(text))
        if self.token:
            text = text.replace(self.token, "***")
        return text

    def get(self, path: str, **params) -> dict:
        url = path if path.startswith("http") else BASE + path.lstrip("/")
        url += "?" + urllib.parse.urlencode({**params, "access_token": self.token})
        self.calls += 1
        try:
            with self.opener(url, timeout=TIMEOUT) as r:
                data = json.load(r)
        except urllib.error.HTTPError as e:
            code, msg = None, ""
            try:
                err = json.load(e).get("error", {})
                code, msg = err.get("code"), str(err.get("message", ""))
            except Exception:
                pass
            text = f"HTTP {e.code}" + (f" code {code}" if code else "") + (f": {msg}" if msg else "")
            raise ApiError(classify(e.code, code), self.clean(text)[:200], code) from None
        except (urllib.error.URLError, OSError, TimeoutError) as e:
            raise ApiError("network", self.clean(f"network error ({type(e).__name__})")) from None
        except ValueError:
            raise ApiError("call", "response was not JSON") from None
        if isinstance(data, dict) and isinstance(data.get("error"), dict):
            err = data["error"]
            raise ApiError(classify(None, err.get("code")), self.clean(f"code {err.get('code')}: {err.get('message', '')}")[:200], err.get("code"))
        return data


def values(resp: dict) -> dict:
    """Insights response to {metric: number}."""
    out = {}
    for m in resp.get("data", []) or []:
        if "total_value" in m:
            v = (m["total_value"] or {}).get("value")
        else:
            v = ((m.get("values") or [{}])[-1] or {}).get("value")
        out[m.get("name")] = v if isinstance(v, (int, float)) else None
    return out
