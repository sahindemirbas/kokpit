"""Example event adapter: a template, no network, synthetic events.

Copy this folder to adapters/events/<your-city-source>/ (or to a folder listed in
[events] adapter_dirs), rename NAME, and replace the body of collect() with code
that reads one public listing page. Enable it with [events] adapters = ["<folder>"].
"""
from __future__ import annotations

from datetime import date, timedelta

NAME = "example-city"          # becomes the `source` of every record; unique slug
CITY = "Example City"
COUNTRY = "IT"
ENABLED_BY_DEFAULT = False     # informational: only [events] adapters in kokpit.toml enables an adapter


def collect(settings: dict) -> list[dict]:
    """Return event records (adapters/README.md). Raise on a broken page; return [] when it is genuinely empty."""
    day = date.today() + timedelta(days=3)
    return [
        {
            "source_id": "example-1",
            "title": "Example concert in the square",
            "description": "Synthetic event from the example adapter.",
            "start": f"{day.isoformat()}T21:00:00+01:00",
            "end": None,
            "venue": "Main square",
            "address": None,
            "city": settings.get("city") or CITY,
            "lat": None,
            "lon": None,
            "url": "https://example.org/events/1",
            "image": None,
            "price": "free",
            "category_hint": "concert",
            "language": "en",
        },
        {
            "source_id": "example-2",
            "title": "Example book talk",
            "start": (day + timedelta(days=1)).isoformat(),   # date only: never invent a time
            "city": settings.get("city") or CITY,
            "url": "https://example.org/events/2",
            "price": "unknown",
        },
    ]
