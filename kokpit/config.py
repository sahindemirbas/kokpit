"""Configuration: one TOML file, explicit defaults, no secrets in the repository.

The one secret-like value is ``[today] calendar_ics_urls``: secret ICS addresses
live only in the local, git-ignored ``kokpit.toml`` (or in the
``KOKPIT_CALENDAR_ICS_URLS`` environment variable, whitespace-separated, which
wins when set). They are never printed; see ``modules/today/calendar.py``.

Lookup order for the config file:
1. ``KOKPIT_CONFIG`` environment variable
2. ``./kokpit.toml`` in the current directory
3. ``~/.config/kokpit/kokpit.toml``

Paths inside the file may use ``~``. Relative paths are resolved against the
directory that holds the config file, so a repository checkout is self-contained.
"""
from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_LOCATIONS = (Path("kokpit.toml"), Path.home() / ".config/kokpit/kokpit.toml")

DEFAULT_TOML = """# Kokpit configuration. Secrets never go here (use .env when a module needs one).

[modules]
today = true
brain = true
health = true
events = {events}

[today]
calendar_ics_urls = []        # read-only ICS feeds (e.g. Google's secret iCal address). SECRET: keep this file
                              # out of git; or set KOKPIT_CALENDAR_ICS_URLS in the environment instead.

[place]                       # set by `kokpit init --city "Lodi"`; geocoded once with OpenStreetMap
city = "{city}"
country = "{country}"         # ISO code, decides which generic sources apply
lat = {lat}
lon = {lon}
radius_km = 40
languages = {languages}       # keyword packs for event categories; first is the main one

[events]
allevents_slug = "{allevents}"   # allevents.in/<slug>; empty disables the source. A wrong slug shows as 0 records.
eventbrite_slug = "{eventbrite}" # eventbrite.<tld>/d/<slug>/; empty disables the source
adapters = []                    # adapters/events/<name> directories to enable (all off by default)
adapter_dirs = []                # extra folders holding your own <name>/collect.py adapters

[brain]
vault_path = "{vault}"        # Markdown folder; a starter structure is created if it is empty
quick_capture = true          # allow appending a note or task from the dashboard

[health]
db_path = "{db}"
expected = []                 # job names that must have run today, e.g. ["heartbeat"]

[server]
host = "127.0.0.1"
port = 8501
"""


@dataclass
class Config:
    path: Path
    modules: dict[str, bool] = field(default_factory=lambda: {"today": True, "brain": True, "health": True})
    vault_path: Path = Path("data/vault")
    quick_capture: bool = True
    db_path: Path = Path("data/kokpit.db")
    expected_jobs: list[str] = field(default_factory=list)
    host: str = "127.0.0.1"
    port: int = 8501
    place: dict = field(default_factory=dict)      # city, country, lat, lon, radius_km, languages
    events: dict = field(default_factory=dict)     # allevents_slug, eventbrite_slug, adapters, adapter_dirs
    calendar_ics_urls: list[str] = field(default_factory=list, repr=False)  # secret, never printed

    @property
    def has_place(self) -> bool:
        p = self.place
        return bool(p.get("city")) and p.get("lat") is not None and p.get("lon") is not None and (p["lat"], p["lon"]) != (0.0, 0.0)

    @property
    def base_dir(self) -> Path:
        return self.path.parent

    def resolve(self, p: str | Path) -> Path:
        p = Path(os.path.expanduser(str(p)))
        return p if p.is_absolute() else (self.base_dir / p).resolve()

    @property
    def vault(self) -> Path:
        return self.resolve(self.vault_path)

    @property
    def db(self) -> Path:
        return self.resolve(self.db_path)


def find_config(explicit: str | Path | None = None) -> Path | None:
    if explicit:
        return Path(explicit)
    env = os.environ.get("KOKPIT_CONFIG")
    if env:
        return Path(env)
    for p in DEFAULT_LOCATIONS:
        if p.exists():
            return p.resolve()
    return None


def load(path: str | Path) -> Config:
    path = Path(path).resolve()
    with path.open("rb") as fh:
        raw = tomllib.load(fh)
    cfg = Config(path=path)
    mods = raw.get("modules", {})
    if mods:
        cfg.modules = {k: bool(v) for k, v in mods.items()}
    brain = raw.get("brain", {})
    cfg.vault_path = Path(brain.get("vault_path", cfg.vault_path))
    cfg.quick_capture = bool(brain.get("quick_capture", True))
    health = raw.get("health", {})
    cfg.db_path = Path(health.get("db_path", cfg.db_path))
    cfg.expected_jobs = list(health.get("expected", []))
    server = raw.get("server", {})
    cfg.host = str(server.get("host", cfg.host))
    cfg.port = int(server.get("port", cfg.port))
    place = raw.get("place", {})
    cfg.place = {
        "city": str(place.get("city", "")) or None,
        "country": str(place.get("country", "")).upper() or None,
        "lat": _float_or_none(place.get("lat")),
        "lon": _float_or_none(place.get("lon")),
        "radius_km": float(place.get("radius_km", 40)),
        "languages": [str(x) for x in place.get("languages", ["en"])] or ["en"],
    }
    ev = raw.get("events", {})
    cfg.events = {
        "allevents_slug": str(ev.get("allevents_slug", "")),
        "eventbrite_slug": str(ev.get("eventbrite_slug", "")),
        "adapters": [str(x) for x in list(ev.get("adapters", [])) + list(ev.get("enabled_adapters", []))],
        "adapter_dirs": [str(x) for x in ev.get("adapter_dirs", [])],
    }
    cfg.modules.setdefault("events", False)
    today = raw.get("today", {})
    env_urls = os.environ.get("KOKPIT_CALENDAR_ICS_URLS", "").split()
    urls = env_urls or today.get("calendar_ics_urls", [])
    if isinstance(urls, str):
        urls = [urls]
    cfg.calendar_ics_urls = [str(u).strip() for u in urls if str(u).strip()]
    return cfg


def _float_or_none(v) -> float | None:
    try:
        return float(v) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


def write_default(path: Path, vault: str = "data/vault", db: str = "data/kokpit.db",
                  place: dict | None = None, slugs: dict | None = None) -> Path:
    """Write the default config. ``place`` (from the geocoder) switches the events module on."""
    path.parent.mkdir(parents=True, exist_ok=True)
    place = place or {}
    slugs = slugs or {}
    langs = _language_guess(place.get("country"))
    text = DEFAULT_TOML.format(
        vault=vault, db=db, events="true" if place.get("lat") is not None else "false",
        city=place.get("city") or "", country=place.get("country") or "",
        lat=place.get("lat") if place.get("lat") is not None else 0.0,
        lon=place.get("lon") if place.get("lon") is not None else 0.0,
        languages="[" + ", ".join(f'"{x}"' for x in langs) + "]",
        allevents=slugs.get("allevents_slug", ""), eventbrite=slugs.get("eventbrite_slug", ""),
    )
    path.write_text(text, encoding="utf-8")
    return path


COUNTRY_LANGUAGE = {"IT": "it", "GB": "en", "IE": "en", "US": "en", "AU": "en", "CA": "en", "NZ": "en"}


def _language_guess(country: str | None) -> list[str]:
    lang = COUNTRY_LANGUAGE.get((country or "").upper())
    return [lang, "en"] if lang and lang != "en" else ["en"]
