"""Morning brief: one compact plain-text message built from the existing builders.

Today (calendar, tasks, weather when the database has them), open threads on you, night proposals
(counts and the top 2), the last sessions' next steps and news matches. Fits one Telegram message.
Every section is optional: a missing database or vault only drops that section.
"""
from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path

from . import contract as c
from . import night as _night
from . import tools

LIMIT = 3500


def _today(state: dict) -> list[str]:
    try:
        r = tools.today(state, {})
    except (c.NotReady, sqlite3.Error, OSError):
        return []
    lines = []
    cal = r.get("calendar") or {}
    todays = [e for e in cal.get("events", []) if e.get("date") == r["date"]]
    if cal.get("status") in ("ok", "stale"):
        lines.append("Calendar: " + ("; ".join(f"{e.get('start') or 'all day'} {c.clean(str(e.get('title', '')), 60)}"
                                               for e in todays[:4]) or "nothing today"))
    w = r.get("weather") or {}
    if w.get("status") in ("ok", "stale") and w.get("days"):
        d = w["days"][0]
        lines.append(f"Weather: {d.get('text', 'unknown')}, {d.get('min', '?')} to {d.get('max', '?')} C"
                     + (f", rain {d['rain_pct']}%" if d.get("rain_pct") is not None else ""))
    t = r.get("tasks") or {}
    if "open" in t:
        lines.append(f"Tasks: {t['open']} open, {t['overdue']} overdue")
        lines += [f"- {c.clean(x['text'], 90)}" + (f" (due {x['due']})" if x.get("due") else "") for x in t["next"][:3]]
    soon = [u for u in r.get("upcoming", []) if 0 <= u.get("days_left", 99) <= 3]
    if soon:
        lines.append("Due soon: " + "; ".join(f"{u['date']} {c.clean(u['text'], 60)}" for u in soon[:3]))
    return lines


def _brain(state: dict) -> tuple[list[str], list[str]]:
    vault = state.get("vault")
    if not state["cfg"]["modules"].get("brain", True):
        return [], []
    if vault is None or not Path(vault).is_dir():
        return ["Vault: not found. Fix: run /kokpit setup <folder>, or point vault_path at your notes folder"], []
    v = c.read_vault(vault, layout=state.get("layout"))
    mine = [(t["title"], o["text"]) for t in v["threads"] for o in t["open"] if o["on_you"]]
    threads = ([f"On you ({len(mine)}):"] + [f"- {c.clean(title, 40)}: {c.clean(text, 100)}" for title, text in mine[:4]]
               if mine else [f"Open threads: {len(v['threads'])}, nothing marked on you"] if v["threads"] else [])
    sessions = []
    if v["sessions"]:
        s = v["sessions"][0]
        sessions.append(f"Last session: {s['at']} {c.clean(s['title'], 60)}")
        sessions += [f"- Open: {c.clean(n, 110)}" for n in s.get("open", [])[:3]]
    return threads, sessions


def _night_lines(state: dict) -> tuple[list[str], list[str]]:
    if state.get("vault") is None or not Path(state["vault"]).is_dir():
        return [], []
    store = _night.load(state.get("night_store"))
    if not store["last_run"]:
        return ["Night: no pass yet, run /kokpit night run"], []
    items = _night.ordered(store["proposals"])
    counts = _night.open_counts(store)
    total = sum(counts.values())
    head = [f"Night ({store['last_run'][:10]}): " + (", ".join(f"{n} {k}" for k, n in counts.items() if n)
                                                    if total else "nothing open")]
    head += [f"- [{p['id'][:6]}] {c.clean(p['text'], 110)}" for p in items if p["kind"] != "news"][:2]
    news = [f"- {c.clean(p['text'], 120)}" for p in items if p["kind"] == "news"][:3]
    return head, (["News for your projects:"] + news if news else [])


def build(state: dict, today: date | None = None) -> dict:
    today = today or date.today()
    demo = state["mode"] == "demo"
    sections = [[f"Kokpit brief {today.isoformat()}" + (" (demo data)" if demo else "")]]
    sections.append(_today(state))
    threads, sessions = _brain(state)
    night_head, news = _night_lines(state)
    sections += [threads, night_head, sessions, news]
    if state.get("vault") is not None and Path(state["vault"]).is_dir() and (night_head or threads):
        sections.append(["Act: /kokpit night, /kokpit add <id>, /kokpit todo <text>"])
    tail = "\n\n" + c.DEMO_HINT if demo else ""
    text = "\n\n".join("\n".join(s) for s in sections if s)
    if len(text) + len(tail) > LIMIT:
        text = text[: LIMIT - len(tail) - 4].rstrip() + "\n..."
    text += tail
    return {"date": today.isoformat(), "text": text, "chars": len(text)}
