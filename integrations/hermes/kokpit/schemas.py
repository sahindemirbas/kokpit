"""Tool schemas (OpenAI function format) for the kokpit toolset."""
from __future__ import annotations


def _s(name, desc, props=None, required=None):
    return {"name": name, "description": desc,
            "parameters": {"type": "object", "properties": props or {}, "required": required or []}}


_LIMIT = {"type": "integer", "description": "Maximum number of items."}
ALL = {
    "kokpit_today": _s("kokpit_today", "Kokpit Today: calendar, open tasks, weather, upcoming due dates, module counts and data freshness. With brief=true: one plain-text morning brief (today, threads on you, night proposals, last session next steps, news matches), under 3500 characters. Read-only.",
                       {"brief": {"type": "boolean", "description": "Return the morning brief as plain text instead of the structured view."}}),
    "kokpit_brain_search": _s("kokpit_brain_search", "Search the Kokpit brain vault (notes/, inbox/, top-level Markdown) by words. Returns vault-relative paths, titles, line numbers and snippets. Read-only.",
                              {"query": {"type": "string", "description": "Words to look for (case-insensitive)."},
                               "limit": {"type": "integer", "description": "Results, default 5, max 20."}}, ["query"]),
    "kokpit_threads": _s("kokpit_threads", "Open threads from Threads.md (where we left off, open items with owner) and the latest Last-Session card. Read-only."),
    "kokpit_capture": _s("kokpit_capture", "Append to the Kokpit vault: a note (new file in inbox/), a task (checkbox under ## Open in To-Do.md) or a journal line ('- HH:MM text' in daily/YYYY-MM-DD.md). Append-only; needs [brain] quick_capture = true. kind receipt closes a meaningful result of this session (a decision, a finished fix, a note written): it updates this session's Last-Session card and writes one short receipt that refers to the notes touched. Do not use it for lookups or plain chat.",
                         {"text": {"type": "string", "description": "Note, task or journal text, at most 2000 characters (task: 300). For a receipt: one or two lines, at most 300 characters."},
                          "kind": {"type": "string", "enum": ["note", "task", "journal", "receipt"], "description": "note (default), task, journal or receipt."},
                          "due": {"type": "string", "description": "Optional due date for a task, YYYY-MM-DD."}}, ["text"]),
    "kokpit_night": _s("kokpit_night", "Night pass proposals from the vault (todo, improve, link, idea, news), each with its sources quoted word for word. action=list (default) shows open ones with the last run time and counts; run makes a new pass; accept or dismiss takes an id. Accept writes only through append-only capture: todo adds a To-Do line, idea creates a new file in ideas/. Notes are never edited.",
                       {"action": {"type": "string", "enum": ["list", "run", "accept", "dismiss"], "description": "list (default), run, accept or dismiss."},
                        "id": {"type": "string", "description": "Proposal id or its first 6 characters, for accept and dismiss."},
                        "kind": {"type": "string", "enum": ["todo", "improve", "link", "idea", "news"], "description": "Only this kind, for list."},
                        "limit": _LIMIT}),
    "kokpit_events": _s("kokpit_events", "Upcoming local events stored by Kokpit. Read-only.",
                        {"days": {"type": "integer", "description": "Days ahead, default 7."}, "limit": _LIMIT}),
    "kokpit_jobs": _s("kokpit_jobs", "Open job listings stored by Kokpit with score and the reasons behind it. Read-only.",
                      {"limit": _LIMIT, "min_score": {"type": "integer", "description": "Only listings with at least this score."}}),
    "kokpit_ai_usage": _s("kokpit_ai_usage", "AI token usage per tool and model from Kokpit. Cost is a number only when a price is known, otherwise unknown. Read-only.",
                          {"days": {"type": "integer", "description": "Days back, default 7."}}),
    "kokpit_health": _s("kokpit_health", "Kokpit job health: last run per job, ok or failed, record counts (0 is flagged), expected jobs that never ran. Read-only."),
    "kokpit_training": _s("kokpit_training", "Optional module (off by default). Training runs from the configured JSONL/CSV log files: last step, last loss and metrics, steps per second, ETA, minutes since the last update and a may-have-stopped warning. Read-only, never touches processes.",
                          {"stall_minutes": {"type": "integer", "description": "Minutes without a new step before a run is flagged, default from settings (30)."}}),
    "kokpit_server": _s("kokpit_server", "Optional module (off by default). Local machine status: disk usage for the configured paths, CPU temperature, load average, memory, uptime and whether the listed systemd --user services are active. Read-only."),
    "kokpit_briefing": _s("kokpit_briefing", "Optional module (off by default). Personal news briefing from the RSS/Atom feeds the user configured per topic: title, url, short summary, headline per topic, source (Kokpit app database or the plugin store) and freshness. action=list (default) only reads. action=collect fetches the configured feed URLs now (the only network access of the plugin) and stores the items in the plugin's own database, never in the vault or the Kokpit app database.",
                          {"action": {"type": "string", "enum": ["list", "collect"], "description": "list (default) reads the day's items; collect fetches the configured feeds now."},
                           "day": {"type": "string", "description": "Day to list, YYYY-MM-DD, default today."},
                           "topic": {"type": "string", "description": "Only this topic, for list."},
                           "limit": {"type": "integer", "description": "Items to list, default 20, max 100."}}),
}
