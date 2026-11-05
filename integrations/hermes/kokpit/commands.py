"""/kokpit slash command: today | brain | health | help. Short plain text that reads well in Telegram."""
from __future__ import annotations

from . import contract as c
from . import tools

HELP = ("Kokpit commands:\n"
        "/kokpit today - calendar, tasks, weather and counts\n"
        "/kokpit brain - open threads and the last session card\n"
        "/kokpit brain <words> - search the vault\n"
        "/kokpit health - Kokpit jobs: failed, 0 records, never run\n"
        "/kokpit help - this list")


def _err(r: dict) -> str:
    return f"Kokpit: {r['error']}. Fix: {r['fix']}"


def _demo(r: dict) -> str:
    return " (demo data)" if r.get("mode") == "demo" else ""


def today_text(r: dict) -> str:
    lines = [f"Today {r['date']}{_demo(r)}"]
    cal = r["calendar"]
    if cal.get("status") == "never run":
        lines.append("Calendar: never run")
    else:
        todays = [e for e in cal["events"] if e.get("date") == r["date"]]
        lines.append("Calendar" + (" (stale)" if cal.get("status") == "stale" else "") + ":"
                     + ("" if todays else " nothing today"))
        lines += [f"- {e.get('start') or 'all day'} {e.get('title', '')}" for e in todays[:6]]
    w = r["weather"]
    if w.get("status") == "never run":
        lines.append("Weather: never run")
    else:
        d = (w.get("days") or [{}])[0]
        lines.append(f"Weather: {d.get('text', 'unknown')}, {d.get('min', '?')} to {d.get('max', '?')} C"
                     + (f", rain {d['rain_pct']}%" if d.get("rain_pct") is not None else "")
                     + (" (stale)" if w.get("status") == "stale" else ""))
    t = r["tasks"]
    if t.get("status") == "unknown":
        lines.append("Tasks: unknown")
    else:
        lines.append(f"Tasks: {t['open']} open, {t['overdue']} overdue")
        lines += [f"- {x['text']}" + (f" (due {x['due']})" if x.get("due") else "") for x in t["next"][:4]]
    counts = r.get("counts") or {}
    if counts:
        lines.append("Counts: " + ", ".join(f"{k.replace('_', ' ')} {v}" for k, v in counts.items()))
    return "\n".join(lines)


def threads_text(r: dict) -> str:
    lines = [f"Open threads{_demo(r)}: {len(r['threads'])}"]
    for t in r["threads"][:6]:
        mine = [o["text"] for o in t["open"] if o["on_you"]]
        lines.append(f"- {t['title']}" + (f": on you: {mine[0]}" if mine else ""))
    s = r.get("last_session")
    if s:
        lines.append(f"Last session: {s['at']} {s['title']}")
    return "\n".join(lines)


def search_text(r: dict) -> str:
    if not r["results"]:
        return f"No note matched: {r['query']}{_demo(r)}"
    lines = [f"Notes for '{r['query']}'{_demo(r)}:"]
    lines += [f"- {h['path']}:{h['line']} {h['snippet']}" for h in r["results"]]
    return "\n".join(lines)


def vault_text(files: dict) -> str:
    if not files or files.get("status"):
        return "Vault files: " + str((files or {}).get("status") or "unknown")
    parts = []
    for item in files.values():
        if item["how"] == "ambiguous":
            parts.append(f"{', '.join(item['candidates'][:2])} (ambiguous)")
        else:
            parts.append(item["file"] + ("" if item["found"] else " (missing)")
                         + (" (found by search)" if item["how"] == "discovered" else ""))
    return "Vault files: " + ", ".join(parts)


def health_text(r: dict) -> str:
    if r.get("db") in ("missing", "empty"):
        lines = [f"Kokpit health{_demo(r)}: no runs recorded yet. {tools.NO_DB}"]
        lines += [f"- {p}" for p in r["problems"] if p.startswith("vault:")]
    else:
        ok = sum(1 for j in r["jobs"] if j["status"] == "ok")
        lines = [f"Kokpit health{_demo(r)}: {ok} of {len(r['jobs'])} jobs ok"]
        lines += [f"- {p}" for p in r["problems"][:10]] or ["- no problems"]
    if "vault_files" in r:
        lines.append(vault_text(r["vault_files"]))
    return "\n".join(lines)


def run(env: c.Env, raw: str) -> str:
    parts = (raw or "").strip().split(None, 1)
    sub = parts[0].lower() if parts else "help"
    rest = parts[1].strip() if len(parts) > 1 else ""
    if sub == "today":
        r = tools.run(env, "kokpit_today", {})
        return today_text(r) if r["ok"] else _err(r)
    if sub == "brain":
        r = tools.run(env, "kokpit_brain_search", {"query": rest}) if rest else tools.run(env, "kokpit_threads", {})
        if not r["ok"]:
            return _err(r)
        return search_text(r) if rest else threads_text(r)
    if sub == "health":
        r = tools.run(env, "kokpit_health", {})
        return health_text(r) if r["ok"] else _err(r)
    if sub == "help":
        return HELP
    return f"Unknown subcommand: {sub}\n" + HELP


def handler(env: c.Env):
    def handle(raw_args: str = "", **_kw) -> str:
        try:
            return run(env, raw_args)
        except Exception as e:  # a slash command must answer, not crash the session
            return f"Kokpit: command failed ({type(e).__name__})."
    return handle
