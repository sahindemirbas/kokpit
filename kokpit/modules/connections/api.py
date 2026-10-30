"""Connections: read-only view of what Kokpit reads, with what permission, and when it last worked.

Three facts per connection (docs/connections.md): what is read / what may be
written, which permission, last successful update with record count. Values
shown are names and counts only; paths are reduced to a file name, tokens never
appear. Calendar ICS addresses are secrets: only their host is shown.
"""
from __future__ import annotations

import json
from datetime import date

from ...config import Config
from ...db import connect


def _last_run(cfg: Config, job: str) -> dict | None:
    if not cfg.db.exists():
        return None
    conn = connect(cfg.db, readonly=True)
    try:
        try:
            r = conn.execute("SELECT * FROM runs WHERE job=? ORDER BY id DESC LIMIT 1", (job,)).fetchone()
        except Exception:
            return None
    finally:
        conn.close()
    if not r:
        return None
    return {"finished": r["finished"], "ok": bool(r["ok"]), "rows": r["rows"],
            "today": str(r["day"]) == date.today().isoformat(), "info": json.loads(r["info"] or "{}"),
            "warnings": json.loads(r["warnings"] or "[]")}


def view(cfg: Config) -> dict:
    items = []
    vault_ok = cfg.vault.exists()
    items.append({
        "id": "vault", "name": "Notes folder", "module": "brain", "kind": "local path",
        "enabled": cfg.modules.get("brain", True), "reads": "Markdown files", "writes": "append-only quick capture" if cfg.quick_capture else "nothing (quick capture off)",
        "permission": "read" + (" + append" if cfg.quick_capture else ""), "target": cfg.vault.name,
        "status": "ok" if vault_ok else "missing", "last": _last_run(cfg, "brain-check"),
    })
    items.append({
        "id": "db", "name": "Local database", "module": "health", "kind": "local file",
        "enabled": cfg.modules.get("health", True), "reads": "job runs, events", "writes": "jobs only",
        "permission": "read/write", "target": cfg.db.name, "status": "ok" if cfg.db.exists() else "not created yet",
        "last": _last_run(cfg, "heartbeat"),
    })
    place = cfg.place
    items.append({
        "id": "place", "name": "City / place", "module": "events", "kind": "setting",
        "enabled": cfg.has_place, "reads": "geocoded once (OpenStreetMap Nominatim) at init", "writes": "nothing",
        "permission": "none", "target": f"{place.get('city') or '-'} {place.get('country') or ''}".strip(),
        "detail": f"radius {place.get('radius_km')} km, languages {', '.join(place.get('languages') or [])}" if cfg.has_place else "run `kokpit init --city ...`",
        "status": "ok" if cfg.has_place else "not set", "last": None,
    })
    ev_run = _last_run(cfg, "events-collect")
    for src, key, url in (("allevents", "allevents_slug", "allevents.in"), ("eventbrite", "eventbrite_slug", "eventbrite.*")):
        slug = cfg.events.get(key) or ""
        per = (ev_run or {}).get("info", {}).get("sources", {}) if ev_run else {}
        items.append({
            "id": src, "name": src.capitalize(), "module": "events", "kind": "public page", "enabled": bool(slug) and cfg.modules.get("events", False),
            "reads": f"listings on {url}/{slug}" if slug else "nothing (no slug)", "writes": "nothing", "permission": "none (public page)",
            "target": slug or "-", "status": ("ok" if per.get(src) else "0 records") if (slug and ev_run and src in per) else ("not run yet" if slug else "off"),
            "last": {**ev_run, "rows": per.get(src)} if ev_run and src in per else ev_run,
        })
    adapters = cfg.events.get("adapters") or []
    items.append({
        "id": "adapters", "name": "Local event adapters", "module": "events", "kind": "per-city adapter",
        "enabled": bool(adapters), "reads": ", ".join(adapters) if adapters else "none enabled", "writes": "nothing",
        "permission": "none", "target": f"{len(adapters)} enabled", "status": "ok" if adapters else "off", "last": None,
    })
    items.append({
        "id": "weather", "name": "Weather", "module": "today", "kind": "public API (Open-Meteo)", "enabled": cfg.has_place,
        "reads": "forecast for the place", "writes": "nothing", "permission": "none (no key)", "target": place.get("city") or "-",
        "status": "ok" if cfg.has_place else "needs a place", "last": _last_run(cfg, "weather"),
    })
    from ..today.calendar import mask
    urls = cfg.calendar_ics_urls
    cal_last = _last_run(cfg, "calendar")
    items.append({
        "id": "calendar", "name": "Calendar (ICS)", "module": "today", "kind": "ICS URL (secret)", "enabled": bool(urls),
        "reads": "events today and the next 6 days", "writes": "nothing", "permission": "read-only",
        "target": ", ".join(sorted({mask(u) for u in urls})) if urls else "-",
        "status": ("ok" if cal_last and cal_last["ok"] else "error" if cal_last else "not run yet") if urls else "not set",
        "last": cal_last,
    })
    items.append({"id": "google-calendar", "name": "Google Calendar (read-only)", "module": "planned", "kind": "planned",
                  "enabled": False, "reads": "-", "writes": "-", "permission": "-", "target": "-", "status": "planned", "last": None})
    items.extend(_career_items(cfg))
    items.extend(_ai_usage_items(cfg))
    items.append(_instagram_item(cfg))
    items.append(_bot_item(cfg))
    items.append(_model_item(cfg))
    items.append(_tts_item(cfg))
    for name in ("CV file",):
        items.append({"id": name.lower().replace(" ", "-"), "name": name, "module": "planned", "kind": "planned",
                      "enabled": False, "reads": "-", "writes": "-", "permission": "-", "target": "-", "status": "planned", "last": None})
    return {"config": cfg.path.name, "connections": items}


def _model_item(cfg: Config) -> dict:
    from ..model import client
    d = client.view(cfg)
    on_uses = [u for u in d["uses"] if u["on"]]
    on = d["enabled"] and bool(on_uses)
    runs = [_last_run(cfg, f"model:{u['purpose']}") for u in on_uses]
    failing = [u["purpose"] for u, r in zip(on_uses, runs) if r and not r["ok"]]
    hermes = d.get("provider") == "hermes"
    if not on:
        status = "off"
    elif hermes and not d.get("command_found"):
        status = "not configured"
    elif not hermes and (not d["key_present"] or not d["base_host"] or not d["model"]):
        status = "not configured"
    elif failing:
        status = "warning"
    elif any(u["last_success"] for u in on_uses):
        status = "ok"
    else:
        status = "not run yet"
    lines = [f"{u['purpose']}: {'on' if u['on'] else 'off'}, sends {u['fields']}, last success {u['last_success'] or 'never'}" for u in d["uses"]]
    if hermes:
        return {
            "id": "model-api", "name": "Model API", "module": "model", "kind": "local command (Hermes agent)", "enabled": on,
            "provider": "hermes",
            "reads": "nothing", "writes": "sends the listed fields to the local Hermes agent, only for uses that are switched on" if on else "nothing (off)",
            "permission": "none in Kokpit (no API key); Hermes uses its own connected accounts", "target": d["base_host"],
            "detail": "provider: hermes (local command, no API key). Command found: " + ("yes" if d.get("command_found") else "no")
                      + f". Model: {d['model']}. " + " | ".join(lines) + ".",
            "status": status, "last": next((r for r in reversed(runs) if r), None),
        }
    return {
        "id": "model-api", "name": "Model API", "module": "model", "kind": "API with key", "enabled": on,
        "provider": "openai_compatible",
        "reads": "nothing", "writes": "sends the listed fields to the provider, only for uses that are switched on" if on else "nothing (off)",
        "permission": f"API key in the environment variable {d['key_env']}", "target": d["base_host"] or "-",
        "detail": "Key configured: " + ("yes" if d["key_present"] else "no") + ". " + " | ".join(lines) + ". The key is never shown.",
        "status": status, "last": next((r for r in reversed(runs) if r), None),
    }


def _tts_item(cfg: Config) -> dict:
    from ..briefing import tts
    t = cfg.briefing["tts"]
    on = cfg.modules.get("briefing", False) and t["provider"] != "none"
    run = _last_run(cfg, "tts:briefing")
    if not on:
        status = "off"
    elif not (t["base_url"] and t["model"]) or not tts.key(cfg):
        status = "not configured"
    elif run and not run["ok"]:
        status = "warning"
    elif run:
        status = "ok"
    else:
        status = "not run yet"
    try:
        host = t["base_url"].split("/")[2]
    except IndexError:
        host = ""
    return {
        "id": "tts-briefing", "name": "TTS provider (Briefing audio)", "module": "briefing", "kind": "API with key", "enabled": on,
        "reads": "nothing", "writes": f"sends {tts.FIELDS} to the provider" if on else "nothing (off)",
        "permission": f"API key in the environment variable {t['key_env']}", "target": host or "-",
        "detail": "Key configured: " + ("yes" if tts.key(cfg) else "no") + ". Without TTS the Briefing tab can read the digest with the browser's own speech, which sends nothing. The key is never shown.",
        "status": status, "last": run,
    }


def _bot_item(cfg: Config) -> dict:
    from ..bot import api as bot_api
    d = bot_api.view(cfg)
    on = d["enabled"]
    run = _last_run(cfg, "bot-poll")
    if not on:
        status = "off"
    elif not d["token"] or not d["chat_id"]:
        status = "not configured"
    elif d["last_outcome"] and d["last_outcome"] != "ok":
        status = "warning"
    elif d["last_poll"]:
        status = "ok"
    else:
        status = "not run yet"
    return {
        "id": "telegram-bot", "name": "Telegram bot", "module": "bot", "kind": "API with token", "enabled": on,
        "reads": "commands from the one allowed chat" if on else "nothing (off)",
        "writes": "replies and the morning message to that chat" if on else "nothing",
        "permission": "bot token (chat id restricts who is answered)", "target": "api.telegram.org",
        "detail": f"Token configured: {'yes' if d['token'] else 'no'}. Chat id configured: {'yes' if d['chat_id'] else 'no'}. "
                  f"Last poll: {d['last_poll'] or 'never'} ({d['last_outcome'] or 'n/a'}). Updates ignored from other chats: {d['foreign']}. Token and chat id are never shown.",
        "status": status, "last": run,
    }


def _career_items(cfg: Config) -> list[dict]:
    from ..career import adapters as job_adapters, browser, linkedin
    on = cfg.modules.get("jobs", False)
    run = _last_run(cfg, "career")
    per = (run or {}).get("info", {}).get("sources", {}) if run else {}
    names = job_adapters.enabled_names(cfg)
    out = [{
        "id": "job-adapters", "name": "Job adapters", "module": "jobs", "kind": "per-source adapter", "enabled": bool(names) and on,
        "reads": ", ".join(sorted(names)) if names else "none enabled", "writes": "nothing", "permission": "none",
        "target": f"{len(names)} enabled", "status": "ok" if names and on else "off",
        "last": {**run, "rows": sum(per.get(n, 0) for n in per if n != linkedin.SOURCE and n not in browser.SOURCES)} if run and names else None,
    }]
    li = bool(cfg.career.get("linkedin_guest"))
    out.append({
        "id": "linkedin-guest", "name": "LinkedIn guest search", "module": "jobs", "kind": "public pages (no login)", "enabled": li and on,
        "reads": "LinkedIn's public guest job pages, 7-day window, keywords and places from your profile" if li else "nothing (off)",
        "writes": "nothing", "permission": "none (public pages)", "target": "linkedin.com",
        "detail": "Off by default. LinkedIn's User Agreement restricts automated access; use is at your own risk, it can break or be blocked at any time, and Kokpit does not bypass blocks.",
        "status": ("ok" if per.get(linkedin.SOURCE) else "0 records") if (li and run and linkedin.SOURCE in per) else ("not run yet" if li else "off"),
        "last": {**run, "rows": per.get(linkedin.SOURCE)} if run and linkedin.SOURCE in per else None,
    })
    enabled = cfg.career.get("browser_sources") or []
    state = browser.status(cfg)
    site, _ = browser.site_for(cfg)
    for name in browser.SOURCES:
        b_on = name in enabled
        out.append({
            "id": f"browser-{name}", "name": f"{browser.NAMES[name]} (browser)", "module": "jobs", "kind": "local headless browser (no login)",
            "enabled": b_on and on,
            "reads": "public search and posting pages, keywords and places from your profile" if b_on else "nothing (off)",
            "writes": "none", "permission": "none (no account)", "target": (site or {}).get(name) or "no site for this country",
            "detail": "Off by default. Needs the optional Playwright package and a local Chromium. The site's terms apply; "
                      "Kokpit pauses between pages, stops after repeated errors or a block page and does not bypass blocks.",
            "status": ("off" if not b_on else state if state != "on" else
                       ((("ok" if per.get(name) else "0 records") if name in per else "not run yet"))),
            "last": {**run, "rows": per.get(name)} if run and name in per else None,
        })
    return out


def _instagram_item(cfg: Config) -> dict:
    from ..instagram import api as ig_api, token as ig_token
    module_on = cfg.modules.get("instagram", False)
    on = module_on and cfg.instagram["analyse"]
    has = ig_token.configured(cfg)
    pub = module_on and cfg.instagram["publish"]
    run = _last_run(cfg, "instagram")
    d = ig_api.view(cfg) if module_on else {}
    ok = bool(d.get("last_success"))
    if not on:
        status = "off"
    elif not has:
        status = "no token"
    elif ok and run and run["ok"] and not run["warnings"]:
        status = "ok"
    elif ok or (run and run["warnings"]):
        status = "warning"
    else:
        status = "not run yet"
    return {
        "id": "instagram", "name": "Instagram (official API)", "module": "instagram", "kind": "API with token", "enabled": on,
        "reads": "account fields, account insights, media and story insights" if on else "nothing (off)",
        "writes": "a post, reel, carousel or story to your account, only after you press Approve on that slot (publish switch is on)" if pub else "nothing (publishing is a separate switch and is off)",
        "permission": "instagram_business_basic, instagram_business_manage_insights" + ("; publishing: instagram_business_content_publish" if pub else " (read-only)"),
        "target": "graph.instagram.com",
        "detail": f"Token configured: {'yes' if has else 'no'}. Last success: {d.get('last_success') or 'never'}. Metric errors in the last run: {len(d.get('errors') or [])}. The token is stored locally and never shown.",
        "status": status, "last": run,
    }


def _ai_usage_items(cfg: Config) -> list[dict]:
    from ..ai_usage import collect as aiu
    on = cfg.modules.get("ai_usage", False)
    run = _last_run(cfg, "ai_usage")
    per = (run or {}).get("info", {}).get("readers", {}) if run else {}
    labels = {"hermes": ("Hermes usage log", "session and model usage rows in state.db"),
              "claude_code": ("Claude Code usage logs", "token usage of assistant messages in the project JSONL logs"),
              "codex": ("Codex usage logs", "token_count totals in the session JSONL logs")}
    src = aiu.paths(cfg)
    out = []
    for tool, (name, reads) in labels.items():
        enabled = on and tool in cfg.ai_usage["readers"]
        out.append({
            "id": f"ai-usage-{tool}", "name": name, "module": "ai_usage", "kind": "local files", "enabled": enabled,
            "reads": reads if enabled else "nothing (off)", "writes": "nothing", "permission": "read-only (database opened read-only)" if tool == "hermes" else "read-only",
            "target": src[tool].name, "detail": "Counts and model names only; session ids are kept as a short hash. Nothing leaves this machine.",
            "status": (("ok" if per.get(tool) else "0 records") if tool in per else "not run yet") if enabled else "off",
            "last": {**run, "rows": per.get(tool)} if run and tool in per else None,
        })
    refresh = cfg.ai_usage["price_refresh"]
    out.append({
        "id": "ai-usage-prices", "name": "Model price list", "module": "ai_usage", "kind": "public API (models.dev)", "enabled": on and refresh,
        "reads": "one GET of https://models.dev/api.json, at most every 24 hours" if refresh else "nothing (off; prices come from your local prices file)",
        "writes": "nothing", "permission": "none (no key)", "target": "models.dev" if refresh else "-",
        "detail": "Opt-in. Nothing about you or your usage is sent.", "status": "ok" if (on and refresh and run and run.get("info", {}).get("price_refresh") != "failed") else ("not run yet" if on and refresh else "off"),
        "last": run if (on and refresh) else None,
    })
    return out
