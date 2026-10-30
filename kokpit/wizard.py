"""First-run setup: the answers behind the setup screen and ``kokpit init``.

Reads and writes kokpit.toml through ``tomledit`` (comments and order stay) and
keys through ``secrets`` (the ``.env`` file next to kokpit.toml). Nothing here
returns a key: only whether one is set.
"""
from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

from . import config as cfgmod
from . import secrets, tomledit
from .config import Config

MAX_NAME = 80
MAX_TOPIC = 60
MAX_ITEMS = 20
MAX_ITEM_CHARS = 300
PROVIDERS = ("hermes", "openai_compatible", "none")


def env_path(cfg_or_path) -> Path:
    p = cfg_or_path.path if isinstance(cfg_or_path, Config) else Path(cfg_or_path)
    return p.resolve().parent / ".env"


# ---------- locales ----------

def _locale_file(code: str) -> Path:
    return cfgmod.LOCALES_DIR / f"{code}.json"


def strings(code: str) -> dict[str, str]:
    """The dictionary of one language, English filling any missing key."""
    out: dict[str, str] = {}
    for c in ("en", code) if code != "en" else ("en",):
        try:
            data = json.loads(_locale_file(c).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        out.update({str(k): str(v) for k, v in data.items() if isinstance(v, str)})
    return out


def languages() -> list[dict]:
    return [{"code": c, "label": strings(c).get("_label", c)} for c in cfgmod.locale_codes()]


def locale_view(cfg: Config, lang: str | None = None) -> dict:
    code = cfgmod.ui_language(lang) if lang else cfg.ui_language
    return {"language": code, "languages": languages(), "name": cfg.user_name, "strings": strings(code)}


# ---------- place ----------

def find_place(city: str) -> dict:
    """Geocode a city once and check the generic event sources. Raises OSError family errors when offline.

    Returns {place: {...} | None, slugs: {...}, counts: {source: n}} (n < 0: the source was unreachable).
    """
    from .modules.events import geocode
    place = geocode.geocode(city)
    if not place:
        return {"place": None, "slugs": {}, "counts": {}}
    slugs, counts = geocode.verify_slugs(place, geocode.propose_slugs(place))
    return {"place": place, "slugs": slugs, "counts": counts}


# ---------- current answers ----------

def _model_view(cfg: Config) -> dict:
    m = cfg.model
    names = secrets.names(env_path(cfg))
    path = shutil.which(m["hermes_bin"])
    return {
        "provider": m["provider"] if m["enabled"] else "none",
        "base_url": m["base_url"], "model": m["model"], "key_env": m["key_env"],
        "key_set": bool(os.environ.get(m["key_env"])) or m["key_env"] in names,
        "hermes_bin": m["hermes_bin"], "hermes_found": bool(path), "hermes_path": path or "",
        "summaries": bool(m["uses"].get("digest") and m["uses"].get("career_weekly")),
    }


def view(cfg: Config) -> dict:
    from .modules.career import profile
    prof = profile.load(cfg)
    p = cfg.place
    return {
        "language": cfg.ui_language, "languages": languages(), "name": cfg.user_name,
        "place": {"city": p.get("city") or "", "country": p.get("country") or "", "lat": p.get("lat"), "lon": p.get("lon"),
                  "set": cfg.has_place},
        "events": {"allevents_slug": cfg.events.get("allevents_slug", ""), "eventbrite_slug": cfg.events.get("eventbrite_slug", "")},
        "career": {"locations": prof.get("locations", []), "remote_pref": prof.get("remote_pref", "any"),
                   "target_roles": prof.get("target_roles", []), "saved": profile.exists(cfg)},
        "topics": [{"name": t["name"], "keywords": t["keywords"], "feeds": t["feeds"]} for t in cfg.briefing.get("topics", [])],
        "model": _model_view(cfg),
        "hermes_bin": cfg.model["hermes_bin"],
        "modules": {k: bool(cfg.modules.get(k, False)) for k in ("events", "jobs", "briefing")},
        "env_file": env_path(cfg).is_file(),
        "done": cfg.setup_done,
    }


# ---------- validation ----------

def _text(v, what: str, limit: int) -> str:
    if v is None:
        return ""
    if not isinstance(v, str):
        raise ValueError(f"{what} must be text")
    v = " ".join(v.split())
    if len(v) > limit:
        raise ValueError(f"{what} is longer than {limit} characters")
    return v


def _list(v, what: str, limit: int = MAX_ITEMS) -> list[str]:
    if v is None:
        return []
    if isinstance(v, str):
        v = v.split(",")
    if not isinstance(v, list):
        raise ValueError(f"{what} must be a list")
    out = []
    for x in v:
        x = _text(x, what, MAX_ITEM_CHARS)
        if x and x not in out:
            out.append(x)
    if len(out) > limit:
        raise ValueError(f"at most {limit} {what}")
    return out


def _url(v: str, what: str) -> str:
    if not v.lower().startswith(("http://", "https://")) or " " in v:
        raise ValueError(f"{what} must start with http:// or https://")
    return v


def _topics(v) -> list[dict]:
    if not isinstance(v, list):
        raise ValueError("topics must be a list")
    if len(v) > MAX_ITEMS:
        raise ValueError(f"at most {MAX_ITEMS} topics")
    out = []
    for t in v:
        if not isinstance(t, dict):
            raise ValueError("each topic must be an object")
        name = _text(t.get("name"), "topic name", MAX_TOPIC)
        kws = [k.lower() for k in _list(t.get("keywords"), "keywords")]
        feeds = [_url(f, "a feed address") for f in _list(t.get("feeds"), "feeds")]
        if not name:
            if kws or feeds:
                raise ValueError("a topic needs a name")
            continue
        out.append({"name": name, "keywords": kws, "feeds": feeds})
    return out


def _place(v) -> dict:
    if not isinstance(v, dict):
        raise ValueError("place must be an object")
    if not v:
        return {}
    city = _text(v.get("city"), "city", 120)
    try:
        lat, lon = float(v.get("lat")), float(v.get("lon"))
    except (TypeError, ValueError):
        raise ValueError("place needs lat and lon (use Find first)") from None
    if not (-90 <= lat <= 90 and -180 <= lon <= 180) or not city:
        raise ValueError("place is not valid")
    country = _text(v.get("country"), "country", 3).upper()
    slug = lambda k: _text(v.get(k), k, 80)
    return {"city": city, "country": country, "lat": lat, "lon": lon,
            "allevents_slug": slug("allevents_slug"), "eventbrite_slug": slug("eventbrite_slug")}


def validate(body: dict) -> dict:
    """Checked, normalised copy of a POST /api/setup body. Raises ValueError with a short message."""
    if not isinstance(body, dict):
        raise ValueError("JSON object expected")
    out: dict = {}
    if "language" in body and body["language"] is not None:
        code = str(body["language"]).strip().lower()
        if code not in cfgmod.locale_codes():
            raise ValueError("unknown language")
        out["language"] = code
    if "name" in body and body["name"] is not None:
        out["name"] = _text(body["name"], "name", MAX_NAME)
    if "place" in body and body["place"] is not None:
        out["place"] = _place(body["place"])
    if "career" in body and body["career"] is not None:
        c = body["career"]
        if not isinstance(c, dict):
            raise ValueError("career must be an object")
        car = {}
        if "locations" in c:
            car["locations"] = _list(c["locations"], "locations")
        if "target_roles" in c:
            car["target_roles"] = _list(c["target_roles"], "roles")
        if "remote_pref" in c:
            rp = str(c["remote_pref"] or "any").lower()
            if rp not in ("any", "onsite", "hybrid", "remote"):
                raise ValueError("remote_pref must be any, onsite, hybrid or remote")
            car["remote_pref"] = rp
        out["career"] = car
    if "topics" in body and body["topics"] is not None:
        out["topics"] = _topics(body["topics"])
    if "model" in body and body["model"] is not None:
        m = body["model"]
        if not isinstance(m, dict):
            raise ValueError("model must be an object")
        prov = str(m.get("provider", "")).strip().lower()
        if prov not in PROVIDERS:
            raise ValueError("provider must be hermes, openai_compatible or none")
        mod = {"provider": prov, "model": _text(m.get("model"), "model", 200)}
        base = _text(m.get("base_url"), "base_url", 300)
        if prov == "openai_compatible":
            if not base:
                raise ValueError("base_url is required for an API key connection")
            mod["base_url"] = _url(base, "base_url")
        key = m.get("api_key")
        if key is not None and not isinstance(key, str):
            raise ValueError("api_key must be text")
        key = (key or "").strip()
        if len(key) > 4000 or any(ch in key for ch in "\r\n\0"):
            raise ValueError("api_key is not valid")
        mod["api_key"] = key
        if "summaries" in m:
            mod["summaries"] = bool(m["summaries"])
        out["model"] = mod
    if "done" in body:
        out["done"] = bool(body["done"])
    return out


# ---------- writing ----------

def apply(cfg: Config, body: dict) -> dict:
    """Write the sent answers. Fields not sent stay as they are. Returns what changed (names only)."""
    data = validate(body)
    path = cfg.path
    text = path.read_text(encoding="utf-8")
    changed: list[str] = []
    s = tomledit.set_value
    if "language" in data:
        text = s(text, "ui", "language", data["language"]); changed.append("language")
    if "name" in data:
        text = s(text, "user", "name", data["name"]); changed.append("name")
    if "place" in data:
        p = data["place"]
        text = s(text, "place", "city", p.get("city", ""))
        text = s(text, "place", "country", p.get("country", ""))
        text = s(text, "place", "lat", p.get("lat", 0.0))
        text = s(text, "place", "lon", p.get("lon", 0.0))
        if p:
            text = s(text, "place", "languages", cfgmod._language_guess(p.get("country")))
        text = s(text, "events", "allevents_slug", p.get("allevents_slug", ""))
        text = s(text, "events", "eventbrite_slug", p.get("eventbrite_slug", ""))
        text = s(text, "modules", "events", bool(p))
        changed.append("place")
    if "topics" in data:
        text = tomledit.remove_tables(text, "briefing.topics")
        for t in data["topics"]:
            text = tomledit.append_table(text, "briefing.topics", t)
        text = s(text, "modules", "briefing", bool(data["topics"]))
        changed.append("topics")
    key_change = None
    if "model" in data:
        m = data["model"]
        key_env = cfg.model["key_env"]
        if m["provider"] == "none":
            text = s(text, "model", "enabled", False)
            key_change = (key_env, "")
        else:
            text = s(text, "model", "enabled", True)
            text = s(text, "model", "provider", m["provider"])
            text = s(text, "model", "model", m["model"])
            if m["provider"] == "openai_compatible":
                text = s(text, "model", "base_url", m["base_url"])
                if m["api_key"]:
                    key_change = (key_env, m["api_key"])
            elif m["provider"] == "hermes" and cfg.model["timeout"] < 120:
                text = s(text, "model", "timeout", 120)
        if "summaries" in m:
            on = m["summaries"] and m["provider"] != "none"
            text = s(text, "model.uses", "digest", on)
            text = s(text, "model.uses", "career_weekly", on)
        changed.append("model")
    if "career" in data:
        text = s(text, "modules", "jobs", True)
        changed.append("career")
    if "done" in data:
        text = s(text, "setup", "done", data["done"]); changed.append("done")
    tomledit.write_atomic(path, text)  # validated again; nothing is written when the result would not parse
    if key_change:
        k, v = key_change
        secrets.set_env_value(env_path(cfg), k, v)
        if v:
            os.environ[k] = v
        else:
            os.environ.pop(k, None)
    reload(cfg)
    if "career" in data:
        from .modules.career import profile
        prof = profile.load(cfg)
        prof.update(data["career"])
        profile.save(cfg, prof)
    return {"changed": changed}


def reload(cfg: Config) -> Config:
    """Read kokpit.toml again into the same object, so the running server sees the new answers without a restart."""
    fresh = cfgmod.load(cfg.path)
    cfg.__dict__.update(fresh.__dict__)
    return cfg


# ---------- terminal questions (kokpit init) ----------

def ask_cli(path: Path, ask=input, ask_secret=None, say=print) -> None:
    """The setup questions in a terminal. Empty answers skip; the setup screen can change everything later."""
    import getpass
    ask_secret = ask_secret or getpass.getpass
    cfg = cfgmod.load(path)
    body: dict = {}
    codes = cfgmod.locale_codes()
    lang = ask(f"Language ({'/'.join(codes)}) [{cfg.ui_language}]: ").strip().lower()
    if lang in codes:
        body["language"] = lang
    name = ask("Your name (for the greeting, optional): ").strip()
    if name:
        body["name"] = name
    if not cfg.has_place:
        city = ask("Your city (for local events, optional): ").strip()
        if city:
            try:
                found = find_place(city)
            except Exception as exc:  # offline: keep going without a place
                found = None
                say(f"could not look the city up ({exc.__class__.__name__}); events stay off, you can add the city later")
            if found and found["place"]:
                pl = found["place"]
                say(f"place: {pl.get('display') or pl.get('city')}; events: "
                    + ", ".join(f"{k} {'unreachable' if n < 0 else n}" for k, n in found["counts"].items()))
                body["place"] = {**{k: pl.get(k) for k in ("city", "country", "lat", "lon")}, **found["slugs"]}
            elif found is not None:
                say(f"no match for '{city}'; events stay off")
    locs = ask("Job locations, comma separated (optional): ").strip()
    roles = ask("Roles you look for, comma separated (optional): ").strip()
    if locs or roles:
        rp = ask("Remote preference (any/onsite/hybrid/remote) [any]: ").strip().lower() or "any"
        body["career"] = {"locations": locs, "target_roles": roles,
                          "remote_pref": rp if rp in ("any", "onsite", "hybrid", "remote") else "any"}
    topic = ask("A news topic for the daily digest (optional, more on the setup screen): ").strip()
    if topic:
        kws = ask("  keywords, comma separated: ").strip()
        feeds = ask("  RSS/Atom feed addresses, comma separated: ").strip()
        body["topics"] = [{"name": topic, "keywords": kws, "feeds": feeds}]
    prov = ask("Model: hermes, key (an OpenAI-compatible API key) or none [none]: ").strip().lower()
    if prov in ("hermes", "key"):
        m = {"provider": "hermes" if prov == "hermes" else "openai_compatible", "model": ask("  model name (optional): ").strip()}
        if prov == "key":
            m["base_url"] = ask("  base URL, e.g. https://provider.example/v1: ").strip()
            m["api_key"] = ask_secret("  API key (hidden, saved to .env): ").strip()
        m["summaries"] = ask("  let the model write summaries (digest, weekly career note)? [y/N]: ").strip().lower() in ("y", "yes", "e", "evet")
        body["model"] = m
    for k in [k for k in body]:  # one bad answer should not lose the others
        try:
            validate({k: body[k]})
        except ValueError as exc:
            say(f"{k} not saved: {exc}")
            body.pop(k)
    body["done"] = True
    apply(cfg, body)
    say(f"saved to {path}" + (" and .env" if body.get("model", {}).get("api_key") else ""))
