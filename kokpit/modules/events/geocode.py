"""Place setup: turn a city name into coordinates once, at ``kokpit init``.

Uses OpenStreetMap Nominatim (no key, one request, cached in kokpit.toml).
Nothing here runs at page load.
"""
from __future__ import annotations

import json
import re
from urllib.parse import urlencode

from ... import fetch

NOMINATIM = "https://nominatim.openstreetmap.org/search?"


def parse(body: str) -> dict | None:
    """First hit of a Nominatim JSON answer as {city, country, lat, lon, display}."""
    try:
        hits = json.loads(body)
    except json.JSONDecodeError:
        return None
    if not isinstance(hits, list) or not hits:
        return None
    h = hits[0]
    addr = h.get("address") or {}
    city = addr.get("city") or addr.get("town") or addr.get("village") or addr.get("municipality") or h.get("name")
    try:
        return {
            "city": city,
            "country": str(addr.get("country_code", "")).upper() or None,
            "country_name": addr.get("country"),
            "province": addr.get("county") or addr.get("state_district") or addr.get("province"),
            "lat": round(float(h["lat"]), 4),
            "lon": round(float(h["lon"]), 4),
            "display": h.get("display_name"),
        }
    except (KeyError, TypeError, ValueError):
        return None


def geocode(city: str) -> dict | None:
    q = urlencode({"q": city, "format": "jsonv2", "limit": 1, "addressdetails": 1, "accept-language": "en"})
    return parse(fetch.get(NOMINATIM + q, timeout=20))


def slugify(s: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", s.lower().strip())
    return s.strip("-")


def propose_slugs(place: dict) -> dict:
    """Slugs the generic sources probably use. The user confirms them on the Connections screen."""
    city = slugify(place.get("city") or "")
    cc = (place.get("country") or "").lower()
    country = slugify(place.get("country_name") or "")
    return {
        "allevents_slug": f"{city}-{cc}" if city and cc else city,
        "eventbrite_slug": f"{country}--{city}" if city and country else city,
    }


def allevents_candidates(place: dict) -> list[str]:
    """AllEvents is inconsistent: big cities are ``/cremona``, smaller ones ``/lodi-it``."""
    city = slugify(place.get("city") or "")
    cc = (place.get("country") or "").lower()
    out = [f"{city}-{cc}", city] if city and cc else [city]
    return [s for s in out if s]


def verify_slugs(place: dict, slugs: dict, fetchers: dict | None = None) -> tuple[dict, dict[str, int]]:
    """Try each proposed slug once and keep the first that returns records.

    Returns (slugs, counts). A source that cannot be reached keeps the proposed
    slug and gets count -1; the user can still fix it on the Connections screen.
    """
    from . import sources
    fetchers = fetchers or sources.SOURCES
    lang = (place.get("languages") or ["en"])[0]
    out, counts = dict(slugs), {}
    cands = {"allevents": allevents_candidates(place), "eventbrite": [slugs.get("eventbrite_slug") or ""]}
    for src, key in (("allevents", "allevents_slug"), ("eventbrite", "eventbrite_slug")):
        counts[src] = -1
        for slug in [s for s in cands[src] if s]:
            try:
                got = fetchers[src](slug, lang) if src == "allevents" else fetchers[src](slug, place.get("country"), lang)
            except Exception:
                break
            counts[src] = len(got)
            if got:
                out[key] = slug
                break
    return out, counts
