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
jobs = false                  # career: profile, scoring, listings (off until you set it up)
ai_usage = false              # reads local usage logs of AI tools (off until you set it up)

[today]
calendar_ics_urls = []        # read-only ICS feeds (e.g. Google's secret iCal address). SECRET: keep this file
                              # out of git; or set KOKPIT_CALENDAR_ICS_URLS in the environment instead.

[place]                       # set by `kokpit init --city "Lodi"`; geocoded once with OpenStreetMap
city = "{city}"
country = "{country}"         # ISO code, decides which generic sources apply
lat = {lat}
lon = {lon}
radius_km = 40
languages = {languages}       # listing language(s) of the place; first is the main one

[events]
allevents_slug = "{allevents}"   # allevents.in/<slug>; empty disables the source. A wrong slug shows as 0 records.
eventbrite_slug = "{eventbrite}" # eventbrite.<tld>/d/<slug>/; empty disables the source
adapters = []                    # adapters/events/<name> directories to enable (all off by default)
adapter_dirs = []                # extra folders holding your own <name>/collect.py adapters
category_languages = ["en"]      # keyword packs for categories, merged in order; built in: en, it
category_pack_dirs = []          # folders with your own <code>.json packs; same code wins over the built-in one

[career]
profile_path = "data/career-profile.json"   # local profile (skills, targets); never in the vault, never sent anywhere
adapters = []                 # adapters/jobs/<name> directories to enable (all off by default)
adapter_dirs = []             # extra folders holding your own <name>/collect.py job adapters
linkedin_guest = false        # OFF. Reads LinkedIn's public guest job pages; see docs/connections.md before enabling
linkedin_pages = 3            # result pages (10 each) per search; 7-day window
close_after_days = 3          # adapter listings missing this many days are marked closed (LinkedIn is never closed this way)

[ai_usage]
readers = []                  # which local logs to read: "hermes", "claude_code", "codex" (each one is separate; all off by default)
hermes_db = "~/.hermes/state.db"
claude_dir = "~/.claude/projects"
codex_dir = "~/.codex/sessions"
prices_path = "data/ai-usage-prices.json"   # your per-model prices per 1M tokens: {{"models": {{"<id>": {{"input": 3, "output": 15, "cache_read": 0.3, "cache_write": 3.75}}}}}}
price_refresh = false         # opt in: one GET of https://models.dev/api.json at most every 24 h, nothing is sent
currency = "USD"              # currency of the two numbers you enter below
# [ai_usage.subscriptions]    # monthly fee per tool, e.g. claude_code = 20
# [ai_usage.api_spend]        # real API spend per month per tool, entered by you
# [[ai_usage.limits]]         # tool = "codex", name = "5 hour", window_hours = 5, tokens = 2000000, resets_at = "2026-11-01T10:00:00Z"
long_session_tokens = 2000000 # advice: sessions with more fresh tokens than this are listed
stale_days = 3                # advice: a reader whose newest data is older than this is flagged

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
    career: dict = field(default_factory=dict)     # profile_path
    ai_usage: dict = field(default_factory=dict)   # readers, paths, prices, limits, advice thresholds
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
        "category_languages": _str_list(ev.get("category_languages"), ["en"]),
        "category_pack_dirs": _str_list(ev.get("category_pack_dirs"), []),
    }
    cfg.modules.setdefault("events", False)
    cfg.modules.setdefault("jobs", False)
    car = raw.get("career", {})
    cfg.career = {
        "profile_path": str(car.get("profile_path", "data/career-profile.json")),
        "adapters": [str(x) for x in car.get("adapters", [])],
        "adapter_dirs": [str(x) for x in car.get("adapter_dirs", [])],
        "linkedin_guest": bool(car.get("linkedin_guest", False)),
        "linkedin_pages": max(1, min(5, _int(car.get("linkedin_pages"), 3))),
        "close_after_days": max(1, min(60, _int(car.get("close_after_days"), 3))),
    }
    cfg.modules.setdefault("ai_usage", False)
    cfg.ai_usage = _ai_usage(raw.get("ai_usage", {}))
    today = raw.get("today", {})
    env_urls = os.environ.get("KOKPIT_CALENDAR_ICS_URLS", "").split()
    urls = env_urls or today.get("calendar_ics_urls", [])
    if isinstance(urls, str):
        urls = [urls]
    cfg.calendar_ics_urls = [str(u).strip() for u in urls if str(u).strip()]
    return cfg


def _num_map(v) -> dict[str, float]:
    out = {}
    for k, x in (v.items() if isinstance(v, dict) else []):
        try:
            if float(x) >= 0:
                out[str(k)] = float(x)
        except (TypeError, ValueError):
            pass
    return out


def _ai_usage(a: dict) -> dict:
    limits = []
    for x in a.get("limits", []) if isinstance(a.get("limits"), list) else []:
        if not isinstance(x, dict) or not x.get("tool"):
            continue
        hours, tokens = _int(x.get("window_hours"), 0), _int(x.get("tokens"), 0)
        if hours > 0 and tokens > 0:
            limits.append({"tool": str(x["tool"]), "name": str(x.get("name") or f"{hours} hour"), "window_hours": hours, "tokens": tokens,
                           "resets_at": str(x.get("resets_at") or "")})
    return {
        "readers": _str_list(a.get("readers"), []),
        "hermes_db": str(a.get("hermes_db", "~/.hermes/state.db")),
        "claude_dir": str(a.get("claude_dir", "~/.claude/projects")),
        "codex_dir": str(a.get("codex_dir", "~/.codex/sessions")),
        "prices_path": str(a.get("prices_path", "data/ai-usage-prices.json")),
        "price_refresh": bool(a.get("price_refresh", False)),
        "currency": str(a.get("currency", "USD"))[:8] or "USD",
        "subscriptions": _num_map(a.get("subscriptions")),
        "api_spend": _num_map(a.get("api_spend")),
        "limits": limits,
        "long_session_tokens": max(1, _int(a.get("long_session_tokens"), 2_000_000)),
        "cache_write_ratio": max(0.0, _float_or_none(a.get("cache_write_ratio")) or 0.5),
        "cache_write_min_tokens": max(0, _int(a.get("cache_write_min_tokens"), 1_000_000)),
        "background_places": _str_list(a.get("background_places"), ["cron"]),
        "premium_output_price": _float_or_none(a.get("premium_output_price")) or 10.0,
        "stale_days": max(1, _int(a.get("stale_days"), 3)),
    }


def _str_list(v, default: list[str]) -> list[str]:
    if isinstance(v, str):
        v = [v]
    out = [str(x).strip() for x in (v or []) if str(x).strip()] if isinstance(v, list) else []
    return out or list(default)


def _int(v, default: int) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


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
