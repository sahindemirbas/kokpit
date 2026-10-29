"""Shared HTTP helpers for connections that need no key.

One function, ``get``, used by the event sources, the geocoder and the weather
connection. It is deliberately tiny so tests can replace it.
"""
from __future__ import annotations

import gzip
import urllib.request

UA = "Kokpit/0.1 (+https://github.com/sahindemirbas/kokpit)"


def get(url: str, timeout: int = 25, headers: dict | None = None) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Encoding": "gzip", **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        body = r.read()
        if r.headers.get("Content-Encoding") == "gzip" or body[:2] == b"\x1f\x8b":
            body = gzip.decompress(body)
        return body.decode(r.headers.get_content_charset() or "utf-8", errors="replace")
