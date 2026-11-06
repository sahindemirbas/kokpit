"""/kokpit slash command: setup, brief, today, brain, health, capture (note, todo, journal) and night proposals.

Short plain text that reads well in the CLI and in every Hermes gateway, Telegram included."""
from __future__ import annotations

from . import contract as c
from . import tools
from . import vault_setup

HELP = ("Kokpit commands:\n"
        "/kokpit setup [path] - make your own vault (default ~/Kokpit) or adopt an existing one\n"
        "/kokpit brief - morning brief: today, threads on you, night, next steps, news\n"
        "/kokpit today - calendar, tasks, weather and counts\n"
        "/kokpit brain - open threads and the last session card\n"
        "/kokpit brain <words> - search the vault\n"
        "/kokpit note <text> - new note in inbox/\n"
        "/kokpit todo <text> [due:YYYY-MM-DD] - task in To-Do.md\n"
        "/kokpit j <text> - journal line in today's daily note (also: journal)\n"
        "/kokpit night - open night proposals (night run: make a new pass)\n"
        "/kokpit add <id> - accept a proposal; /kokpit skip <id> - dismiss it\n"
        "/kokpit health - Kokpit jobs: failed, 0 records, never run\n"
        "/kokpit help - this list")


def _err(r: dict) -> str:
    return f"Kokpit: {r['error']}. Fix: {r['fix']}"


def _demo(r: dict) -> str:
    return " (demo data)" if r.get("mode") == "demo" else ""


def today_text(r: dict) -> str:
    lines = [f"Today {r['date']}{_demo(r)}"]
    cal = r["calendar"]
    if cal.get("status") not in ("ok", "stale"):  # never run, or unknown / table calendar missing ...
        lines.append(f"Calendar: {cal.get('status') or 'unknown'}")
    else:
        todays = [e for e in cal["events"] if e.get("date") == r["date"]]
        lines.append("Calendar" + (" (stale)" if cal.get("status") == "stale" else "") + ":"
                     + ("" if todays else " nothing today"))
        lines += [f"- {e.get('start') or 'all day'} {e.get('title', '')}" for e in todays[:6]]
    w = r["weather"]
    if w.get("status") not in ("ok", "stale"):
        lines.append(f"Weather: {w.get('status') or 'unknown'}")
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
        lines = [f"Kokpit health{_demo(r)}: no runs recorded yet. {r.get('reason') or tools.NO_DB}"]
        lines += [f"- {p}" for p in r["problems"] if p.startswith("vault:")]
    else:
        ok = sum(1 for j in r["jobs"] if j["status"] == "ok")
        lines = [f"Kokpit health{_demo(r)}: {ok} of {len(r['jobs'])} jobs ok"]
        lines += [f"- {p}" for p in r["problems"][:10]] or ["- no problems"]
    if "vault_files" in r:
        lines.append(vault_text(r["vault_files"]))
    return "\n".join(lines)


def capture_text(r: dict) -> str:
    if r["kind"] == "note":
        return f"Saved note: {r['written']}{_demo(r)}"
    return f"Added to {r['written']}{_demo(r)}: {r['line']}"


ICON = {"todo": "todo", "improve": "next", "link": "link", "idea": "idea", "news": "news"}


def _counts(open_: dict) -> str:
    parts = [f"{n} {k}" for k, n in open_.items() if n]
    return ", ".join(parts) if parts else "nothing open"


def night_run_text(r: dict) -> str:
    g = r["generated"]
    lines = [f"Night pass done{_demo(r)} in {r['seconds']} s. Notes were not changed.",
             "Found: " + ", ".join(f"{g[k]} {k}" for k in g) + f" ({r['new']} new)"]
    news = r.get("news") or {}
    lines.append(f"News source: {news.get('source', 'unknown')}"
                 + (f", {news['matched']} of {news['items']} items matched a project" if news.get("items") else ""))
    if r.get("model") and r["model"] != "off":
        m = r["model"]
        if m == "on":
            m = f"on, {r.get('model_kept', 0)} kept, {r.get('model_rejected', 0)} rejected by the checks"
        lines.append(f"Model: {m}")
    lines.append(f"Open now: {_counts(r['open'])}. See them with /kokpit night")
    return "\n".join(lines)


def _src(s: dict) -> str:
    q = s["quote"] if len(s["quote"]) <= 90 else s["quote"][:87] + "..."
    return f"{s.get('path') or s.get('item')}: \"{q}\""


def night_list_text(r: dict) -> str:
    if r["last_run"] == "never":
        return f"No night pass yet{_demo(r)}. Run one with /kokpit night run"
    lines = [f"Night proposals, last run {r['last_run'].replace('T', ' ')[:16]}{_demo(r)}: {_counts(r['open'])}"]
    for p in r["proposals"]:
        lines.append(f"[{p['short']}] {ICON.get(p['kind'], p['kind'])}: {p['text']}")
        if p["sources"]:
            lines.append(f"    from {_src(p['sources'][0])}")
    if not r["proposals"]:
        lines.append("Nothing open. Run /kokpit night run again later.")
    else:
        lines.append("Accept: /kokpit add <id>   Dismiss: /kokpit skip <id>")
    app = r.get("kokpit_app") or []
    if app:
        lines.append(f"From the Kokpit app organiser ({len(app)} open, review them in Kokpit):")
        lines += [f"- {a['kind']}: {a['text']}" for a in app[:3]]
    return "\n".join(lines)


def decide_text(r: dict) -> str:
    if r["status"] == "dismissed":
        return f"Dismissed [{r['id'][:6]}]: {r['text']}. It will not come back."
    if r.get("written"):
        return f"Accepted [{r['id'][:6]}]. Wrote {r['written']}" + (f": {r['line']}" if r.get("line") else "")
    return f"Accepted [{r['id'][:6]}]: {r['text']}" + (f" ({r['line']})" if r.get("line") else ". Marked done.")


CAPTURE = {"note": "note", "todo": "task", "j": "journal", "journal": "journal"}
USAGE = {"note": "/kokpit note <text>", "todo": "/kokpit todo <text> [due:YYYY-MM-DD]",
         "j": "/kokpit j <text>", "journal": "/kokpit journal <text>",
         "add": "/kokpit add <id>", "skip": "/kokpit skip <id>"}


NO_HINT = {"setup", "help"}


def _demo_mode(env: c.Env) -> bool:
    try:
        return env.state()["mode"] == "demo"
    except Exception:
        return False


def run(env: c.Env, raw: str) -> str:
    parts = (raw or "").strip().split(None, 1)
    sub = parts[0].lower() if parts else "help"
    out = _run(env, raw)
    if sub not in NO_HINT and sub in SUBS and c.DEMO_HINT not in out and _demo_mode(env):
        out = out.rstrip() + "\n" + c.DEMO_HINT
    return out


def _run(env: c.Env, raw: str) -> str:
    parts = (raw or "").strip().split(None, 1)
    sub = parts[0].lower() if parts else "help"
    rest = parts[1].strip() if len(parts) > 1 else ""
    if sub == "setup":
        return vault_setup.text(vault_setup.run(env, rest))
    if sub == "brief":
        r = tools.run(env, "kokpit_today", {"brief": True})
        return r["text"] if r["ok"] else _err(r)
    if sub in CAPTURE:
        if not rest:
            return f"Usage: {USAGE[sub]}"
        args = {"kind": CAPTURE[sub], "text": rest}
        if sub == "todo":
            args["text"], args["due"] = c.split_due(rest)
            if not args["text"]:
                return f"Usage: {USAGE[sub]}"
        r = tools.run(env, "kokpit_capture", args)
        return capture_text(r) if r["ok"] else _err(r)
    if sub == "night":
        if rest.lower() == "run":
            r = tools.run(env, "kokpit_night", {"action": "run"})
            return night_run_text(r) if r["ok"] else _err(r)
        kind = rest.lower()
        r = tools.run(env, "kokpit_night", {"action": "list", "limit": 5, **({"kind": kind} if kind else {})})
        return night_list_text(r) if r["ok"] else _err(r)
    if sub in ("add", "skip"):
        if not rest:
            return f"Usage: {USAGE[sub]}"
        r = tools.run(env, "kokpit_night", {"action": "accept" if sub == "add" else "dismiss", "id": rest.split()[0]})
        return decide_text(r) if r["ok"] else _err(r)
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


SUBS = {"setup", "brief", "today", "brain", "health", "help", "night", "add", "skip", *CAPTURE}


def handler(env: c.Env):
    def handle(raw_args: str = "", **_kw) -> str:
        try:
            return run(env, raw_args)
        except Exception as e:  # a slash command must answer, not crash the session
            return f"Kokpit: command failed ({type(e).__name__})."
    return handle
