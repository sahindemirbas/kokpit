"""Small Telegram Bot API client over urllib.

The token sits in the URL path, so it is scrubbed from every error text. ``opener`` is any callable
``opener(request, timeout=...)`` returning a file-like object, so tests can stub the network.
"""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.request

BASE = "https://api.telegram.org/bot"
TIMEOUT = 25
MAX_TEXT = 4096


class BotApiError(Exception):
    """kind: auth (bad token), rate, network, call. The message never contains the token."""

    def __init__(self, kind: str, message: str, code: int | None = None):
        super().__init__(message)
        self.kind, self.code = kind, code


def scrub(text, token: str = "") -> str:
    text = re.sub(r"bot\d+:[A-Za-z0-9_-]+", "bot***", str(text))
    text = re.sub(r"\d{6,}:[A-Za-z0-9_-]{20,}", "***", text)
    if token:
        text = text.replace(token, "***")
    return text


def classify(status: int | None) -> str:
    if status in (401, 403, 404):
        return "auth"
    if status == 429:
        return "rate"
    return "call"


class Client:
    def __init__(self, token: str, opener=None):
        self.token = token
        self.opener = opener or urllib.request.urlopen
        self.calls = 0

    def clean(self, text) -> str:
        return scrub(text, self.token)

    def call(self, method: str, params: dict | None = None, timeout: float = TIMEOUT):
        self.calls += 1
        body = json.dumps({k: v for k, v in (params or {}).items() if v is not None}).encode("utf-8")
        req = urllib.request.Request(f"{BASE}{self.token}/{method}", data=body, method="POST",
                                     headers={"Content-Type": "application/json"})
        try:
            with self.opener(req, timeout=timeout) as r:
                data = json.load(r)
        except urllib.error.HTTPError as e:
            desc = ""
            try:
                desc = str(json.load(e).get("description", ""))
            except Exception:
                pass
            text = f"HTTP {e.code}" + (f": {desc}" if desc else "")
            raise BotApiError(classify(e.code), self.clean(text)[:200], e.code) from None
        except (urllib.error.URLError, OSError, TimeoutError) as e:
            raise BotApiError("network", self.clean(f"network error ({type(e).__name__})")) from None
        except ValueError:
            raise BotApiError("call", "response was not JSON") from None
        if not isinstance(data, dict) or not data.get("ok"):
            code = data.get("error_code") if isinstance(data, dict) else None
            desc = data.get("description", "") if isinstance(data, dict) else ""
            raise BotApiError(classify(code), self.clean(f"API error {code}: {desc}")[:200], code)
        return data.get("result")

    def get_updates(self, offset: int | None = None, timeout: int = 0) -> list[dict]:
        res = self.call("getUpdates", {"offset": offset, "timeout": timeout,
                                       "allowed_updates": ["message", "callback_query"]}, timeout=timeout + 10)
        return res if isinstance(res, list) else []

    def send_message(self, chat_id, text: str, reply_markup: dict | None = None):
        return self.call("sendMessage", {"chat_id": chat_id, "text": str(text)[:MAX_TEXT], "reply_markup": reply_markup})

    def answer_callback_query(self, callback_id: str, text: str | None = None):
        return self.call("answerCallbackQuery", {"callback_query_id": callback_id, "text": (text or "")[:200] or None})

    def edit_message_reply_markup(self, chat_id, message_id: int, reply_markup: dict | None = None):
        return self.call("editMessageReplyMarkup", {"chat_id": chat_id, "message_id": message_id,
                                                    "reply_markup": reply_markup or {"inline_keyboard": []}})

    def edit_message_text(self, chat_id, message_id: int, text: str, reply_markup: dict | None = None):
        """Replaces the text; with no markup the inline buttons are removed."""
        return self.call("editMessageText", {"chat_id": chat_id, "message_id": message_id, "text": str(text)[:MAX_TEXT],
                                             "reply_markup": reply_markup or {"inline_keyboard": []}})
