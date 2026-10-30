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
import re
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
instagram = false             # Instagram analysis through the official API (off until you add a token)
briefing = false                # daily digest of the RSS/Atom feeds you list under [briefing] (off until you set topics)
bot = false                   # Telegram bot: commands and a morning message (off until you add a token and chat id)
organiser = false             # night organiser: proposals only, never edits a note (off until you want it)

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
translate_to = ""                # language code to translate titles and descriptions into (e.g. "en"); needs [model] and uses.events_translate

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

[instagram]
analyse = false                # read-only insights (needs a token, see docs/connections.md)
publish = false                # separate switch; publishing is not part of the analysis job
token_file = "data/instagram-token"   # written by `kokpit instagram refresh-token`; keep out of git
# token = ""                   # or set KOKPIT_INSTAGRAM_TOKEN in the environment (preferred over writing it here)
media_limit = 30               # recent posts and reels read per run (1 to 100)
plan_guard_days = 3            # Health warning when nothing is planned for the next N days (0 = off)
poll_seconds = 3               # publishing: wait between container status checks
poll_timeout = 120             # publishing: give up waiting for a container after this many seconds

[bot]
# token = ""                  # or set KOKPIT_TELEGRAM_TOKEN in the environment (preferred over writing it here)
chat_id = ""                  # the one chat the bot answers; every other chat is ignored
morning_time = "07:30"        # used by `kokpit bot poll` to send the morning line once a day; cron can use the bot-morning job instead
poll_timeout = 25             # long poll seconds for `kokpit bot poll` (1 to 50)
remind_hours = 24             # job bot-instagram-remind: remind about Instagram slots due within this many hours (1 to 168)

[model]                       # optional model connection, OFF. The key is read from the environment, never from this file
enabled = false
base_url = ""                 # OpenAI-compatible endpoint, e.g. https://provider.example/v1 (not used with "hermes")
model = ""
key_env = "KOKPIT_MODEL_API_KEY"   # name of the environment variable that holds the key (not used with "hermes")
timeout = 30                  # seconds; an agent call is slower, 120 is safer with "hermes"
provider = "openai_compatible"   # or "hermes": calls go through a local Hermes agent and its connected accounts, no key here
                              # with "hermes", model may stay empty (Hermes uses its own default)
# hermes_bin = "hermes"       # with "hermes": command name or path
# hermes_provider = ""        # with "hermes": optional, passed to Hermes as --provider
[model.uses]                  # one switch per purpose; the Connections screen lists the fields each one sends
events_translate = false
events_classify = false
digest = false
night = false
career_weekly = false         # weekly job-market note in the Career tab; without it the tab shows numbers only

[briefing]                      # digest topics: you choose them, nothing is built in. Example (remove the leading # to use):
# max_per_topic = 5           # items kept per topic (1 to 20)
# max_age_days = 7            # older feed entries are ignored
# [[briefing.topics]]
# name = "open source releases"
# feeds = ["https://example.org/releases.atom"]
# keywords = ["release", "security", "python"]
# [briefing.github]             # optional: new GitHub repositories by search query, off until queries are set
# queries = ["topic:llm", "static site generator"]
# language = "python"         # optional language filter
# created_days = 30           # only repositories created in the last N days
# max_items = 5
# token_env = "KOKPIT_GITHUB_TOKEN"   # optional token from the environment (higher rate limit); works without one
# [briefing.huggingface]        # optional: Hugging Face models by search term and/or the daily papers list, off by default
# models = ["text-to-speech"]
# papers = false
# max_items = 5
# [briefing.tts]                # optional audio summary, off. The key is read from the environment, never from this file
# provider = "none"           # "none" | "openai_compatible" (POST <base_url>/audio/speech)
# base_url = "https://provider.example/v1"
# model = ""
# voice = ""
# key_env = "KOKPIT_TTS_API_KEY"

# [organiser]                 # night organiser, 03:30 daily; proposals go to SQLite and one report file in the data folder
# stale_days = 30             # an active thread unchanged for this long is proposed for closing
# types = ["idea", "project", "note", "person", "reference"]
# required_fields = ["type", "summary"]
# short_summary = 20          # a summary shorter than this many characters counts as empty
# apply_report_to_vault = false   # true: write the report as ONE new file under report_folder in the vault
# report_folder = "Organiser"
# [organiser.folders]         # type = folder, for the folder mismatch check
# project = "projects"

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
    instagram: dict = field(default_factory=lambda: _instagram({}), repr=False)  # switches, token_file, media_limit; token is secret
    model: dict = field(default_factory=lambda: _model({}), repr=False)  # no secret inside: the key lives in the environment
    bot: dict = field(default_factory=lambda: _bot({}), repr=False)  # token and chat_id are secret-like
    briefing: dict = field(default_factory=lambda: _briefing({}), repr=False)  # topics, feeds, tts (no key inside)
    organiser: dict = field(default_factory=lambda: _organiser({}), repr=False)  # thresholds and report mode
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


LEGACY_BRIEFING = "agenda"  # name of the briefing module before the rename; still read, with a warning


def _legacy_briefing(raw: dict) -> None:
    """Accept configs written before the rename: [agenda] and modules.agenda are read as briefing."""
    import logging
    log = logging.getLogger(__name__)
    old = LEGACY_BRIEFING
    if old in raw and "briefing" not in raw:
        raw["briefing"] = raw.pop(old)
        log.warning("kokpit.toml: [%s] is deprecated, rename it to [briefing]", old)
    mods = raw.get("modules")
    if isinstance(mods, dict) and old in mods and "briefing" not in mods:
        mods["briefing"] = mods.pop(old)
        log.warning("kokpit.toml: modules.%s is deprecated, rename it to modules.briefing", old)


def load(path: str | Path) -> Config:
    path = Path(path).resolve()
    with path.open("rb") as fh:
        raw = tomllib.load(fh)
    _legacy_briefing(raw)
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
    cfg.host = os.environ.get("KOKPIT_HOST", "").strip() or str(server.get("host", cfg.host))  # env wins (Docker)
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
        "translate_to": str(ev.get("translate_to", "")).strip().lower()[:12],
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
    cfg.modules.setdefault("instagram", False)
    cfg.instagram = _instagram(raw.get("instagram", {}))
    cfg.model = _model(raw.get("model", {}))
    cfg.modules.setdefault("briefing", False)
    cfg.briefing = _briefing(raw.get("briefing", {}))
    cfg.modules.setdefault("organiser", False)
    cfg.organiser = _organiser(raw.get("organiser", {}))
    cfg.modules.setdefault("bot", False)
    cfg.bot = _bot(raw.get("bot", {}))
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


def _instagram(a: dict) -> dict:
    return {
        "analyse": bool(a.get("analyse", False)),
        "publish": bool(a.get("publish", False)),
        "token_file": str(a.get("token_file", "data/instagram-token")),
        "token": str(a.get("token", "")).strip(),
        "media_limit": max(1, min(100, _int(a.get("media_limit"), 30))),
        "plan_guard_days": max(0, min(60, _int(a.get("plan_guard_days"), 3))),
        "poll_seconds": max(1, min(30, _int(a.get("poll_seconds"), 3))),
        "poll_timeout": max(5, min(600, _int(a.get("poll_timeout"), 120))),
    }


def _model(a: dict) -> dict:
    uses = a.get("uses", {}) if isinstance(a.get("uses"), dict) else {}
    return {
        "enabled": bool(a.get("enabled", False)),
        "provider": "hermes" if str(a.get("provider", "")).strip().lower() == "hermes" else "openai_compatible",
        "hermes_bin": str(a.get("hermes_bin", "hermes")).strip() or "hermes",
        "hermes_provider": str(a.get("hermes_provider", "")).strip(),
        "base_url": str(a.get("base_url", "")).strip(),
        "model": str(a.get("model", "")).strip(),
        "key_env": str(a.get("key_env", "KOKPIT_MODEL_API_KEY")).strip() or "KOKPIT_MODEL_API_KEY",
        "timeout": max(1, min(300, _int(a.get("timeout"), 30))),
        "uses": {k: bool(uses.get(k, False)) for k in ("events_translate", "events_classify", "digest", "night", "career_weekly")},
    }


def _briefing(a: dict) -> dict:
    topics = []
    for t in a.get("topics", []) if isinstance(a.get("topics"), list) else []:
        if not isinstance(t, dict) or not str(t.get("name", "")).strip():
            continue
        feeds = [u for u in _str_list(t.get("feeds"), []) if u.lower().startswith(("http://", "https://"))]
        topics.append({"name": str(t["name"]).strip()[:80], "feeds": feeds,
                       "keywords": [k.lower() for k in _str_list(t.get("keywords"), [])]})
    tts = a.get("tts", {}) if isinstance(a.get("tts"), dict) else {}
    prov = str(tts.get("provider", "none")).strip().lower()
    gh = a.get("github", {}) if isinstance(a.get("github"), dict) else {}
    hf = a.get("huggingface", {}) if isinstance(a.get("huggingface"), dict) else {}
    return {
        "topics": topics,
        "github": {
            "queries": [q for q in _str_list(gh.get("queries"), []) if q.strip()][:5],
            "language": str(gh.get("language", "")).strip()[:40],
            "created_days": max(1, min(365, _int(gh.get("created_days"), 30))),
            "max_items": max(1, min(20, _int(gh.get("max_items"), 5))),
            "token_env": str(gh.get("token_env", "KOKPIT_GITHUB_TOKEN")).strip() or "KOKPIT_GITHUB_TOKEN",
            "base_url": str(gh.get("base_url", "https://api.github.com")).strip().rstrip("/") or "https://api.github.com",
        },
        "huggingface": {
            "models": [q for q in _str_list(hf.get("models"), []) if q.strip()][:5],
            "papers": hf.get("papers") is True,
            "max_items": max(1, min(20, _int(hf.get("max_items"), 5))),
            "base_url": str(hf.get("base_url", "https://huggingface.co")).strip().rstrip("/") or "https://huggingface.co",
        },
        "max_per_topic": max(1, min(20, _int(a.get("max_per_topic"), 5))),
        "max_age_days": max(1, min(60, _int(a.get("max_age_days"), 7))),
        "tts": {
            "provider": prov if prov in ("none", "openai_compatible") else "none",
            "base_url": str(tts.get("base_url", "")).strip(),
            "model": str(tts.get("model", "")).strip(),
            "voice": str(tts.get("voice", "")).strip(),
            "key_env": str(tts.get("key_env", "KOKPIT_TTS_API_KEY")).strip() or "KOKPIT_TTS_API_KEY",
            "timeout": max(1, min(300, _int(tts.get("timeout"), 60))),
        },
    }


def _organiser(a: dict) -> dict:
    folders = a.get("folders", {}) if isinstance(a.get("folders"), dict) else {}
    return {
        "stale_days": max(1, min(365, _int(a.get("stale_days"), 30))),
        "types": [t.lower() for t in _str_list(a.get("types"), ["idea", "project", "note", "person", "reference"])],
        "required_fields": [t.lower() for t in _str_list(a.get("required_fields"), ["type", "summary"])],
        "short_summary": max(0, min(500, _int(a.get("short_summary"), 20))),
        "folders": {str(k).lower(): str(v).strip().strip("/") for k, v in folders.items() if str(v).strip()},
        "apply_report_to_vault": a.get("apply_report_to_vault") is True,
        "report_folder": str(a.get("report_folder", "Organiser")).strip().strip("/") or "Organiser",
    }


def _bot(a: dict) -> dict:
    mt = str(a.get("morning_time", "07:30")).strip()
    m = re.fullmatch(r"([01]?\d|2[0-3]):([0-5]\d)", mt)
    return {
        "token": str(a.get("token", "")).strip(),
        "chat_id": str(a.get("chat_id", "")).strip(),
        "morning_time": f"{int(m.group(1)):02d}:{m.group(2)}" if m else "07:30",
        "poll_timeout": max(1, min(50, _int(a.get("poll_timeout"), 25))),
        "remind_hours": max(1, min(168, _int(a.get("remind_hours"), 24))),
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
