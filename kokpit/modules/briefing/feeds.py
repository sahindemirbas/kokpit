"""RSS 2.0 and Atom parsing with the standard library. ``fetch.get`` is the only network call."""
from __future__ import annotations

import html
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

from ... import fetch

MAX_BYTES = 2_000_000


def _local(tag) -> str:
    return tag.rsplit("}", 1)[-1].lower() if isinstance(tag, str) else ""


def _text(el) -> str:
    return "".join(el.itertext()).strip() if el is not None else ""


def strip_html(s: str) -> str:
    s = re.sub(r"<[^>]+>", " ", s or "")
    return re.sub(r"\s+", " ", html.unescape(s)).strip()


def parse_date(s: str) -> datetime | None:
    s = (s or "").strip()
    if not s:
        return None
    try:
        d = parsedate_to_datetime(s)
    except (TypeError, ValueError):
        try:
            d = datetime.fromisoformat(s.replace("Z", "+00:00"))
        except ValueError:
            return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def parse(text: str) -> list[dict]:
    """Items of an RSS or Atom document: title, url, summary, tags, published (datetime or None)."""
    head = text[:4000].lower()
    if "<!doctype" in head or "<!entity" in head:  # no entity tricks in a feed
        raise ValueError("feed with a DTD is refused")
    root = ET.fromstring(text.encode("utf-8") if isinstance(text, str) else text)
    out = []
    for el in root.iter():
        if _local(el.tag) not in ("item", "entry"):
            continue
        row = {"title": "", "url": "", "summary": "", "tags": [], "published": None}
        for ch in el:
            n = _local(ch.tag)
            if n == "title":
                row["title"] = strip_html(_text(ch))
            elif n == "link":
                href = ch.get("href") or _text(ch)
                if href and (not row["url"] or ch.get("rel") in (None, "alternate")):
                    row["url"] = href.strip()
            elif n == "guid" and not row["url"] and _text(ch).startswith("http"):
                row["url"] = _text(ch)
            elif n in ("description", "summary") or (n == "content" and not row["summary"]):
                row["summary"] = strip_html(_text(ch))
            elif n == "category":
                tag = (ch.get("term") or _text(ch)).strip()
                if tag:
                    row["tags"].append(tag)
            elif n in ("pubdate", "published", "updated", "date") and row["published"] is None:
                row["published"] = parse_date(_text(ch))
        if row["title"] and row["url"].lower().startswith(("http://", "https://")):
            out.append(row)
    return out


def fetch_feed(url: str) -> list[dict]:
    return parse(fetch.get(url)[:MAX_BYTES])
