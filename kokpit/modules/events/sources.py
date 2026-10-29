"""Generic event sources: pages that take a city parameter and publish JSON-LD.

Each source returns records in the adapter contract (see adapters/README.md).
``fetch.get`` is the only network call; tests replace it with fixture HTML.

Silent-empty trap: a wrong slug returns a normal page with no events. The
collector returns [] and the health table shows "0 records"; it never guesses.
"""
from __future__ import annotations

import html
import json
import math
import re

from ... import fetch

EVENTBRITE_DOMAIN = {"IT": "eventbrite.it", "DE": "eventbrite.de", "FR": "eventbrite.fr", "ES": "eventbrite.es",
                     "GB": "eventbrite.co.uk", "NL": "eventbrite.nl", "PT": "eventbrite.pt", "AT": "eventbrite.at",
                     "CH": "eventbrite.ch", "BE": "eventbrite.be", "IE": "eventbrite.ie", "CA": "eventbrite.ca",
                     "AU": "eventbrite.com.au"}


def clean(s) -> str | None:
    if not s:
        return None
    s = html.unescape(re.sub(r"<[^>]+>", " ", str(s)))
    return re.sub(r"\s+", " ", s).strip() or None


def jsonld_events(page: str) -> list[dict]:
    """Event objects from every JSON-LD block on the page (ItemList wrappers included)."""
    out = []
    for block in re.findall(r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', page, re.S):
        try:
            data = json.loads(block.strip())
        except json.JSONDecodeError:
            continue
        items = []
        if isinstance(data, dict) and isinstance(data.get("itemListElement"), list):
            for it in data["itemListElement"]:
                e = it.get("item") if isinstance(it, dict) else None
                if isinstance(e, dict):
                    e = dict(e)
                    e.setdefault("url", it.get("url"))
                    items.append(e)
        elif isinstance(data, dict) and isinstance(data.get("@graph"), list):
            items.extend(x for x in data["@graph"] if isinstance(x, dict))
        elif isinstance(data, dict):
            items.append(data)
        elif isinstance(data, list):
            items.extend(x for x in data if isinstance(x, dict))
        for e in items:
            if str(e.get("@type", "")).endswith("Event") and e.get("name") and e.get("url"):
                out.append(e)
    return out


def _geo(e: dict) -> tuple[float, float] | None:
    loc = e.get("location")
    geo = loc.get("geo") if isinstance(loc, dict) else None
    try:
        return float(geo["latitude"]), float(geo["longitude"])
    except (TypeError, KeyError, ValueError):
        return None


def _location(e: dict) -> tuple[str | None, str | None, str | None]:
    """(city, venue, street)."""
    loc = e.get("location")
    if not isinstance(loc, dict):
        return None, clean(loc) if isinstance(loc, str) else None, None
    addr = loc.get("address")
    city = street = None
    if isinstance(addr, dict):
        city = clean(addr.get("addressLocality"))
        street = clean(addr.get("streetAddress"))
        if street:
            street = re.sub(r"\s*\b\d{4,5}\s*$", "", street).strip(" ,") or None
    elif isinstance(addr, str):
        street = clean(addr)
    return city, clean(loc.get("name")), street


def _price(e: dict) -> str:
    offers = e.get("offers")
    if isinstance(offers, list):
        offers = offers[0] if offers else None
    if not isinstance(offers, dict):
        if e.get("isAccessibleForFree") is True:
            return "free"
        return "unknown"
    p = offers.get("price")
    if p is None and offers.get("lowPrice") is not None:
        p = offers.get("lowPrice")
    try:
        return "free" if float(p) == 0 else "paid"
    except (TypeError, ValueError):
        return "unknown"


def _when(v) -> str | None:
    """Keep the source's precision: a bare date stays a date; never invent a time."""
    if not v:
        return None
    s = str(v).strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
        return s
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2})?([+-]\d{2}:?\d{2}|Z)?", s):
        return s
    return s[:10] if re.match(r"\d{4}-\d{2}-\d{2}", s) else None


def record(e: dict, source: str, language: str | None = None) -> dict | None:
    start = _when(e.get("startDate"))
    if not start:
        return None
    city, venue, street = _location(e)
    geo = _geo(e)
    url = str(e["url"]).split("?")[0]
    image = e.get("image")
    if isinstance(image, list):
        image = image[0] if image else None
    if isinstance(image, dict):
        image = image.get("url")
    return {
        "source": source, "source_id": url, "title": clean(e["name"]), "description": clean(e.get("description")),
        "start": start, "end": _when(e.get("endDate")), "venue": venue, "address": street, "city": city,
        "lat": geo[0] if geo else None, "lon": geo[1] if geo else None, "url": url,
        "image": str(image) if image else None, "price": _price(e), "category_hint": None, "language": language,
    }


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def within(records: list[dict], lat: float | None, lon: float | None, radius_km: float) -> list[dict]:
    """Attach distance_km and drop records with coordinates outside the radius.

    A record without coordinates is kept (the page itself was chosen by city); its distance stays null.
    """
    out = []
    for r in records:
        if lat is None or lon is None or r.get("lat") is None or r.get("lon") is None:
            r["distance_km"] = None
            out.append(r)
            continue
        d = haversine_km(lat, lon, r["lat"], r["lon"])
        if d <= radius_km:
            r["distance_km"] = round(d, 1)
            out.append(r)
    return out


def allevents(slug: str, language: str | None = None) -> list[dict]:
    """``allevents.in/<slug>``; slug like ``lodi-it``."""
    if not slug:
        return []
    page = fetch.get(f"https://allevents.in/{slug}")
    return [r for r in (record(e, "allevents", language) for e in jsonld_events(page)) if r]


def eventbrite(slug: str, country: str | None = None, language: str | None = None) -> list[dict]:
    """``eventbrite.<tld>/d/<country>--<city>/<city>/``; slug like ``italy--lodi``."""
    if not slug:
        return []
    domain = EVENTBRITE_DOMAIN.get((country or "").upper(), "eventbrite.com")
    city = slug.split("--")[-1]
    page = fetch.get(f"https://www.{domain}/d/{slug}/{city}/")
    return [r for r in (record(e, "eventbrite", language) for e in jsonld_events(page)) if r]


SOURCES = {"allevents": allevents, "eventbrite": eventbrite}
