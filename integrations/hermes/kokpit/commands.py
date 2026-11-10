"""/kokpit slash command: setup, brief, today, brain, health, capture (note, todo, journal), night proposals
and the opt-in news briefing.

Short plain text that reads well in the CLI and in every Hermes gateway, Telegram included."""
from __future__ import annotations

import json
import shlex

from . import collector
from . import contract as c
from . import jobs
from . import podcast
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
        "/kokpit done <summary> - close a result: session card plus a short receipt\n"
        "/kokpit health - Kokpit jobs: failed, 0 records, never run\n"
        "/kokpit news - today's news briefing (news run: collect now; off by default)\n"
        "/kokpit news add <topic> <feed-url> [keywords...] | news remove <topic> | news on | news off\n"
        "/kokpit jobs - job listings with score (jobs run | market | on | off | profile; off by default)\n"
        "/kokpit jobs help - profile, CV proposal and the job source\n"
        "/kokpit podcast - podcast of today's briefing: status (podcast make | script | engine tts|notebooklm | "
        "on | off; off by default)\n"
        "/kokpit help - this list")


def _err(r: dict) -> str:
    return f"Kokpit: {r['error']}. Fix: {r['fix']}"


def _demo(r: dict) -> str:
    return " (demo data)" if r.get("mode") == "demo" else ""


def today_text(r: dict) -> str:
    lines = [f"Today {r['date']}{_demo(r)}"]
    cal, w = r["calendar"], r["weather"]
    if not r.get("app_db", True):
        lines.append("Calendar and weather: not connected (they come from the Kokpit app, set db_path)")
    elif cal.get("status") not in ("ok", "stale"):  # never run, or unknown / table calendar missing ...
        lines.append(f"Calendar: {cal.get('status') or 'unknown'}")
    else:
        todays = [e for e in cal["events"] if e.get("date") == r["date"]]
        lines.append("Calendar" + (" (stale)" if cal.get("status") == "stale" else "") + ":"
                     + ("" if todays else " nothing today"))
        lines += [f"- {e.get('start') or 'all day'} {e.get('title', '')}" for e in todays[:6]]
    if not r.get("app_db", True):
        pass
    elif w.get("status") not in ("ok", "stale"):
        lines.append(f"Weather: {w.get('status') or 'unknown'}")
    else:
        d = (w.get("days") or [{}])[0]
        lines.append(f"Weather: {d.get('text', 'unknown')}, {d.get('min', '?')} to {d.get('max', '?')} C"
                     + (f", rain {d['rain_pct']}%" if d.get("rain_pct") is not None else "")
                     + (" (stale)" if w.get("status") == "stale" else ""))
    t = r["tasks"]
    if t.get("status") == "unknown":
        lines.append("Tasks: unknown" + (f" ({t['problem']}). Fix: {t['fix']}" if t.get("fix") else ""))
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
        app = "Kokpit app jobs: " if r.get("plugin_jobs") else ""
        lines = [f"Kokpit health{_demo(r)}: {app}no runs recorded yet. {r.get('reason') or tools.NO_DB}"]
        lines += [f"- {p}" for p in r["problems"] if p.startswith(("vault:", "plugin "))]
    else:
        ok = sum(1 for j in r["jobs"] if j["status"] == "ok")
        lines = [f"Kokpit health{_demo(r)}: {ok} of {len(r['jobs'])} jobs ok"]
        lines += [f"- {p}" for p in r["problems"][:10]] or ["- no problems"]
    for j in r.get("plugin_jobs") or []:
        age = f", {j['age_hours']} h ago" if j.get("age_hours") is not None else ""
        recs = f", {j['records']} records" if j.get("records") is not None else ""
        lines.append(f"Plugin job {j['job']} (collected by this plugin): {j['status']}{age}{recs}")
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
         "add": "/kokpit add <id>", "skip": "/kokpit skip <id>",
         "done": "/kokpit done <short summary of the result>",
         "news": "/kokpit news [run | add <topic> <feed-url> [keywords...] | remove <topic> | on | off]",
         "jobs": "/kokpit jobs [run | market | on | off | linkedin on|off | profile [set <field> <values...> | "
                 "cv <path> [apply]] | help]",
         "podcast": "/kokpit podcast [make | script | engine tts|notebooklm | on | off]"}
SET_LINE = "hermes config set plugins.entries.kokpit.settings.{key} {value}"
JOBS_HELP = ("Job listings (off by default):\n"
             "/kokpit jobs - top open listings with score and the main reasons\n"
             "/kokpit jobs run - search now; /kokpit jobs market - market view\n"
             "/kokpit jobs on|off - the module; /kokpit jobs linkedin on|off - the only source\n"
             "/kokpit jobs profile - show your profile; profile set <field> <values...> - edit one field\n"
             "  lists are comma separated; locations use ';' so a comma stays inside one: "
             "profile set locations Milan, Italy; Turin\n"
             "/kokpit jobs profile cv <path> - propose skills and languages from a .txt/.md/.pdf CV; add apply to save\n"
             + "Fields: " + ", ".join(jobs.FIELDS) + "\n" + jobs.RISK)


def news_text(r: dict) -> str:
    src = r.get("source")
    if not r["items"]:
        lines = [f"News {r['day']}{_demo(r)}: {r['status']}"]
        if r.get("next"):
            lines.append("Next: " + r["next"])
    else:
        fr = r.get("freshness") or {}
        age = f", last collected {fr['age_hours']} h ago" if fr.get("age_hours") is not None else ""
        lines = [f"News {r['day']}{_demo(r)} ({r['count']} items from the {src}{age}):"]
        topic = None
        for it in r["items"][:12]:
            if it["topic"] != topic:
                topic = it["topic"]
                lines.append(f"{topic}:")
            lines.append(f"- {'* ' if it['headline'] else ''}{c.clean(it['title'], 110)} {it['url']}")
    note = (r.get("store") or {}).get("note")
    if note:
        lines.append("Note: " + note)
    lines += [f"Settings: {p}" for p in (r.get("settings_problems") or [])[:3]]
    return "\n".join(lines)


def collect_text(r: dict) -> str:
    lines = [f"News collected{_demo(r)}: {r['stored']} items from {r['feeds_fetched']} feeds "
             f"({', '.join(f'{k} {n}' for k, n in r['topics'].items())}). Summaries by model: {r['model']}."]
    lines += [f"- {h['topic']}: {h['title']}" for h in r["headlines"][:5]]
    lines += [f"Failed feed: {f['url']} ({f['error']})" for f in r["failed_feeds"][:5]]
    lines += [f"Warning: {w}" for w in r["warnings"] if not any(f["url"] in w for f in r["failed_feeds"])][:5]
    if (r.get("store") or {}).get("note"):
        lines.append("Note: " + r["store"]["note"])
    return "\n".join(lines)


def _save(env: c.Env, key: str, value) -> str:
    """Write one plugin setting through the host when it can, else return the exact command to run."""
    env.override(key, value)
    shown = json.dumps(value, ensure_ascii=False) if not isinstance(value, bool) else str(value).lower()
    cmd = SET_LINE.format(key=key, value=shlex.quote(shown))
    if env.set_config is None:
        return f"Not saved for the next start (this Hermes has no plugin settings writer). Run: {cmd}"
    try:
        env.set_config(key, value)
    except Exception as e:  # managed install, administrator-managed key
        return f"Not saved for the next start ({type(e).__name__}). Run: {cmd}"
    return "Saved to the plugin settings."


def _topics(env: c.Env) -> list[dict]:
    return collector.parse_topics(env.setting("briefing_topics", []))[0]


def news(env: c.Env, rest: str) -> str:
    words = rest.split()
    verb = words[0].lower() if words else ""
    if verb in ("", "list", "today"):
        r = tools.run(env, "kokpit_briefing", {"action": "list", "limit": 20})
        return news_text(r) if r["ok"] else _err(r)
    if verb == "run":
        r = tools.run(env, "kokpit_briefing", {"action": "collect"})
        return collect_text(r) if r["ok"] else _err(r)
    if verb in ("on", "off"):
        saved = _save(env, "briefing", verb == "on")
        extra = (" Nothing is fetched until you add a topic and run /kokpit news run or schedule hermes kokpit-collect."
                 if verb == "on" else " No feed is fetched while it is off.")
        return f"News briefing {verb}.{extra} {saved}"
    if verb == "add":
        if len(words) < 3:
            return f"Usage: /kokpit news add <topic> <feed-url> [keywords...]"
        name, url, kws = c.clean(words[1], 40), words[2], words[3:]
        try:
            url = collector.check_url(url)
        except ValueError as e:
            return f"Kokpit: feed not added: {e}. Only http and https feed URLs are accepted."
        topics = _topics(env)
        t = next((x for x in topics if x["name"].casefold() == name.casefold()), None)
        if t is None:
            if len(topics) >= collector.MAX_TOPICS:
                return f"Kokpit: at most {collector.MAX_TOPICS} topics; remove one first."
            t = {"name": name, "feeds": [], "keywords": []}
            topics.append(t)
        if url not in t["feeds"]:
            if len(t["feeds"]) >= collector.MAX_FEEDS_PER_TOPIC:
                return f"Kokpit: topic {t['name']} already has {collector.MAX_FEEDS_PER_TOPIC} feeds."
            t["feeds"].append(url)
        for k in kws:
            k = c.clean(k, 40)
            if k and k not in t["keywords"] and len(t["keywords"]) < collector.MAX_KEYWORDS:
                t["keywords"].append(k)
        saved = _save(env, "briefing_topics", collector.topics_setting(topics))
        on = tools.run(env, "kokpit_briefing", {"action": "list", "limit": 1})
        hint = "" if on.get("ok") else " The module is off: /kokpit news on."
        return (f"Topic {t['name']}: {len(t['feeds'])} feeds, keywords {', '.join(t['keywords']) or 'none'}. "
                f"{saved}{hint}")
    if verb == "remove":
        if len(words) < 2:
            return "Usage: /kokpit news remove <topic>"
        name = " ".join(words[1:])
        topics = _topics(env)
        left = [x for x in topics if x["name"].casefold() != name.casefold()]
        if len(left) == len(topics):
            return f"Kokpit: no topic named {c.clean(name, 40)}. Topics: {', '.join(x['name'] for x in topics) or 'none'}"
        saved = _save(env, "briefing_topics", collector.topics_setting(left))
        return f"Topic {c.clean(name, 40)} removed; items already collected stay until they age out. {saved}"
    return f"Usage: {USAGE['news']}"


# ---------------------------------------------------------------- /kokpit jobs

def jobs_list_text(r: dict) -> str:
    fr = r.get("freshness") or {}
    if not r.get("listings"):
        lines = [f"Jobs{_demo(r)}: {r.get('status') or 'no open listings'}"]
        if r.get("next"):
            lines.append("Next: " + r["next"])
        return "\n".join(lines)
    age = f", last collected {fr['age_hours']} h ago" if fr.get("age_hours") is not None else ""
    lines = [f"Jobs{_demo(r)}: {r['open_total']} open, top {r['count']} from the {r.get('source') or 'unknown source'}{age}:"]
    for it in r["listings"]:
        where = ", ".join(x for x in (it.get("company"), it.get("location")) if x) or "company unknown"
        mk = it.get("max_known")
        judged = (f", judged {mk}/{jobs.FULL_SCALE}" if isinstance(mk, int) and isinstance(it.get("score"), int)
                  and mk < jobs.COVERAGE_MIN else "")
        lines.append(f"- [{it['score']}{judged}] {c.clean(it['title'], 80)} ({c.clean(where, 60)})"
                     + (f" {it['url']}" if it.get("url") else ""))
        why = [x for x in it.get("reasons") or [] if not x.startswith("unknown")][:2]
        if why:
            lines.append("  " + "; ".join(c.clean(x, 90) for x in why))
    note = (r.get("store") or {}).get("note")
    if note:
        lines.append("Note: " + note)
    return "\n".join(lines)


def jobs_collect_text(r: dict) -> str:
    lines = [f"Jobs collected: status {r['status']}, {r['fetched']} listings from {r['source']} in "
             f"{r.get('requests', 'unknown')} requests; new {r.get('new')}, updated {r.get('updated')}, "
             f"closed {r.get('closed')}; {r.get('open')} open of {r.get('total')} stored."]
    if r.get("note"):
        lines.append("Note: " + r["note"])
    lines += [f"Warning: {w}" for w in r.get("warnings") or []][:5]
    if r.get("notice"):
        lines.append("About the source: " + r["notice"])
    if (r.get("store") or {}).get("note"):
        lines.append("Note: " + r["store"]["note"])
    return "\n".join(lines)


def market_text(r: dict) -> str:
    if r.get("status") != "ok":
        lines = [f"Job market{_demo(r)}: {r.get('status')}"]
    else:
        lines = [f"Job market{_demo(r)} ({r['total']} listings, {r['open']} open, {r['companies_n']} companies, "
                 f"from the {r.get('source')}):"]
        if r["companies"]:
            lines.append("Companies: " + ", ".join(f"{x['company']} {x['open']}" for x in r["companies"][:8]))
        if r["weekly"]:
            lines.append("Weekly: " + ", ".join(f"{x['week']} {x['listings']}" for x in r["weekly"][-8:]))
        if r["skills"]:
            lines.append("Skills asked: " + ", ".join(f"{x['skill']} {x['open']}" for x in r["skills"][:10]))
    lines += [f"Note: {n}" for n in r.get("notes") or [] if n != r.get("status")][:4]
    return "\n".join(lines)


def profile_text(prof: dict) -> str:
    lines = ["Job profile (stored only in the plugin data dir):"]
    for f in jobs.FIELDS:
        v = prof.get(f)
        if f == "languages":
            v = ", ".join(f"{x['language']}:{x['level'] or 'level not set'}" for x in v or [])
        elif f == "locations":
            v = "; ".join(v or [])
        elif isinstance(v, list):
            v = ", ".join(v)
        lines.append(f"- {f}: {v if v not in (None, '') else 'not set'}")
    lines.append("Lists are comma separated; locations are separated by ';' "
                 "(set locations Milan, Italy; Turin).")
    return "\n".join(lines)


def _profile_path(env: c.Env):
    return env.jobs_settings()["profile_path"]


def jobs_profile(env: c.Env, rest: str) -> str:
    path = _profile_path(env)
    try:
        words = shlex.split(rest)
    except ValueError:
        words = rest.split()
    verb = words[0].lower() if words else ""
    if verb in ("", "show"):
        return profile_text(jobs.load_profile(path))
    if verb == "set":
        if len(words) < 2:
            return (f"Usage: /kokpit jobs profile set <field> <values...> (lists comma separated, locations "
                    f"separated by ';', e.g. set locations Milan, Italy; Turin). Fields: {', '.join(jobs.FIELDS)}")
        try:
            prof = jobs.save_profile(path, jobs.set_field(jobs.load_profile(path), words[1], " ".join(words[2:])))
        except ValueError as e:
            return f"Kokpit: profile not changed: {e}"
        return f"Saved {words[1].lower()}.\n" + profile_text(prof)
    if verb == "cv":
        if len(words) < 2:
            return "Usage: /kokpit jobs profile cv <path> [apply]"
        apply = len(words) > 2 and words[-1].lower() == "apply"
        target = " ".join(words[1:-1] if apply else words[1:])
        try:
            proposal = jobs.propose(jobs.extract_text(target))
        except jobs.CVError as e:
            return f"Kokpit: CV not read: {e}"
        langs = ", ".join(f"{x['language']}:{x['level'] or 'level not found'}" for x in proposal["languages"]) or "none found"
        lines = [f"Proposed from the CV (rules only, no model; the CV text is not stored or sent anywhere):",
                 f"- skills: {', '.join(proposal['skills']) or 'none found'}", f"- languages: {langs}"]
        if not apply:
            lines.append(f"Nothing saved. To add these to your profile: /kokpit jobs profile cv {shlex.quote(target)} apply")
            return "\n".join(lines)
        jobs.save_profile(path, jobs.merge_proposal(jobs.load_profile(path), proposal))
        lines.append("Added to your profile (existing entries kept). Check it: /kokpit jobs profile")
        return "\n".join(lines)
    return "Usage: " + USAGE["jobs"]


def jobs_cmd(env: c.Env, rest: str) -> str:
    words = rest.split()
    verb = words[0].lower() if words else ""
    if verb in ("", "list"):
        r = tools.run(env, "kokpit_jobs", {"action": "list", "limit": 8})
        return jobs_list_text(r) if r["ok"] else _err(r)
    if verb == "run":
        r = tools.run(env, "kokpit_jobs", {"action": "collect"})
        return jobs_collect_text(r) if r["ok"] else _err(r)
    if verb == "market":
        r = tools.run(env, "kokpit_market", {})
        return market_text(r) if r["ok"] else _err(r)
    if verb in ("on", "off"):
        saved = _save(env, "jobs", verb == "on")
        extra = (" Nothing is fetched until you set target roles, turn the source on (/kokpit jobs linkedin on) "
                 "and run /kokpit jobs run." if verb == "on" else " No job source is searched while it is off.")
        return f"Job listings {verb}.{extra} {saved}"
    if verb == "linkedin":
        sw = words[1].lower() if len(words) > 1 else ""
        if sw not in ("on", "off"):
            return "Usage: /kokpit jobs linkedin on|off\n" + jobs.RISK
        saved = _save(env, "jobs_linkedin", sw == "on")
        return (f"LinkedIn guest search {sw}. " + (jobs.RISK + " " if sw == "on" else "") + saved)
    if verb == "profile":
        return jobs_profile(env, rest[len(words[0]):].strip())
    if verb == "help":
        return JOBS_HELP
    return "Usage: " + USAGE["jobs"]


SCRIPT_SHOWN = 6000


def podcast_status_text(r: dict) -> str:
    lines = [f"Podcast {r['day']}{_demo(r)}: {r['status']}. Engine: {r['engine']}"
             + ("" if r["engine_ok"] else " (not valid)") + f", script by {r['script_mode']}, {r['items']} briefing items."]
    t = r["today"]
    for key, label in (("episode", "Episode"), ("playlist", "Parts playlist"), ("source_md", "Source pack"),
                       ("script", "Script")):
        if t.get(key):
            lines.append(f"{label}: {t[key]}" + (f" ({r['episode_duration']})"
                                                  if key == "episode" and r.get("episode_duration") else ""))
    for x in r["last"][:2]:
        age = f"{x['age_hours']} h ago" if x["age_hours"] is not None else "age unknown"
        lines.append(f"Last {x['engine']}: {x['status']} for {x['day']}, {x['duration']}, {age}"
                     + ("" if x["exists"] else " (file no longer there)"))
    lines += [f"Missing: {m}" for m in r["missing"]]
    if r.get("note"):
        lines.append("Note: " + r["note"])
    if r.get("next"):
        lines.append("Next: " + r["next"])
    if r.get("risk"):
        lines.append(r["risk"])
    return "\n".join(lines)


def podcast_script_text(r: dict) -> str:
    if not r.get("text"):
        return f"Podcast script {r['day']}{_demo(r)}: {r['status']}." + (f" Next: {r['next']}" if r.get("next") else "")
    head = (f"Podcast script {r['day']}{_demo(r)} ({r['mode']}, about {r['words']} words, "
            f"{r['estimated_minutes']} min" + (f"; {r['note']}" if r.get("note") else "") + "):")
    text = r["text"]
    if len(text) > SCRIPT_SHOWN:
        text = text[:SCRIPT_SHOWN].rstrip() + "\n..." + (f"\nFull script: {r['path']}" if r.get("path") else "")
    return head + "\n\n" + text


def podcast_make_text(r: dict) -> str:
    lines = [f"Podcast {r['day']} ({r['engine']}, script by {r['script']}): {r['status']} in {r['took_seconds']} s."]
    if r.get("file"):
        lines.append(f"Episode: {r['file']} ({r['duration']})")
    if r.get("playlist"):
        lines.append(f"Parts playlist: {r['playlist']} ({len(r['parts'])} parts, {r['duration']})")
    pack = r.get("source_pack")
    if pack and r["status"] == "source pack ready":
        lines += [f"Source pack: {pack['source_md']} and {pack['focus_txt']}"] + list(r.get("steps") or [])
    if r.get("script_note"):
        lines.append("Script: " + r["script_note"])
    for key in ("note", "voice", "risk"):
        if r.get(key):
            lines.append(r[key])
    lines += [f"Warning: {w}" for w in r.get("warnings", [])[:5]]
    return "\n".join(lines)


def podcast_cmd(env: c.Env, rest: str) -> str:
    words = rest.split()
    verb = words[0].lower() if words else ""
    if verb in ("", "status"):
        r = tools.run(env, "kokpit_podcast", {"action": "status"})
        return podcast_status_text(r) if r["ok"] else _err(r)
    if verb == "script":
        r = tools.run(env, "kokpit_podcast", {"action": "script"})
        return podcast_script_text(r) if r["ok"] else _err(r)
    if verb == "make":
        r = tools.run(env, "kokpit_podcast", {"action": "make"})
        return podcast_make_text(r) if r["ok"] else _err(r)
    if verb == "engine":
        name = words[1].lower() if len(words) > 1 else ""
        if name not in podcast.ENGINES:
            return (f"Unknown engine {c.clean(name, 30)!r}: use tts or notebooklm. Nothing saved." if name else
                    "Usage: /kokpit podcast engine tts|notebooklm")
        saved = _save(env, "podcast_engine", name)
        extra = podcast.TTS_VOICE if name == "tts" else (
            "Make writes a source pack for NotebookLM; the notebooklm command runs only with "
            "podcast_notebooklm_cli on. " + podcast.RISK)
        return f"Podcast engine {name}. {extra} {saved}"
    if verb in ("on", "off"):
        saved = _save(env, "podcast", verb == "on")
        extra = (" Nothing is made until you run /kokpit podcast make; collect_auto never makes a podcast."
                 if verb == "on" else " No podcast is made while it is off.")
        return f"Podcast {verb}.{extra} {saved}"
    return "Usage: " + USAGE["podcast"]


def done_text(r: dict) -> str:
    refs = ", ".join(r["refs"][:5]) + (f" (+{len(r['refs']) - 5} more)" if len(r["refs"]) > 5 else "")
    state = "saved" if r["status"] == "succeeded" else "already saved"
    return f"Receipt {state}{_demo(r)}: {r['receipt']}. Session card updated in {r['card']}. Refs: {refs}"


NO_HINT = {"setup", "help"}


def _demo_mode(env: c.Env) -> bool:
    try:
        return env.state()["mode"] == "demo"
    except Exception:
        return False


def run(env: c.Env, raw: str, session_id: str = "") -> str:
    parts = (raw or "").strip().split(None, 1)
    sub = parts[0].lower() if parts else "help"
    out = _run(env, raw, session_id)
    if sub not in NO_HINT and sub in SUBS and c.DEMO_HINT not in out and _demo_mode(env):
        out = out.rstrip() + "\n" + c.DEMO_HINT
    return out


def _record(env: c.Env, session_id: str, r: dict, accepted: bool = False) -> None:
    rec = getattr(env, "recorder", None)
    if rec is not None and r.get("ok") and (r.get("written") or accepted):
        rec.record(session_id, r.get("written") or "", accepted=accepted)


def _run(env: c.Env, raw: str, session_id: str = "") -> str:
    parts = (raw or "").strip().split(None, 1)
    sub = parts[0].lower() if parts else "help"
    rest = parts[1].strip() if len(parts) > 1 else ""
    if sub == "setup":
        return vault_setup.text(vault_setup.run(env, rest))
    if sub == "brief":
        r = tools.run(env, "kokpit_today", {"brief": True})
        return r["text"] if r["ok"] else _err(r)
    if sub == "done":
        if not rest:
            return f"Usage: {USAGE[sub]}"
        r = tools.run(env, "kokpit_capture", {"kind": "receipt", "text": rest}, session_id=session_id)
        return done_text(r) if r["ok"] else _err(r)
    if sub in CAPTURE:
        if not rest:
            return f"Usage: {USAGE[sub]}"
        args = {"kind": CAPTURE[sub], "text": rest}
        if sub == "todo":
            args["text"], args["due"] = c.split_due(rest)
            if not args["text"]:
                return f"Usage: {USAGE[sub]}"
        r = tools.run(env, "kokpit_capture", args)
        _record(env, session_id, r)
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
        if sub == "add" and r.get("ok") and r.get("status") == "done":
            _record(env, session_id, r, accepted=True)
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
    if sub == "news":
        return news(env, rest)
    if sub == "jobs":
        return jobs_cmd(env, rest)
    if sub == "podcast":
        return podcast_cmd(env, rest)
    if sub == "help":
        return HELP
    return f"Unknown subcommand: {sub}\n" + HELP


SUBS = {"setup", "brief", "today", "brain", "health", "help", "night", "add", "skip", "done", "news", "jobs",
        "podcast", *CAPTURE}


def handler(env: c.Env):
    def handle(raw_args: str = "", session_id: str = "", **_kw) -> str:
        try:
            return run(env, raw_args, session_id=session_id if isinstance(session_id, str) else "")
        except Exception as e:  # a slash command must answer, not crash the session
            return f"Kokpit: command failed ({type(e).__name__})."
    return handle
