# Architecture

This describes the shape of the reference instance and the target shape of the generalised product. Where they differ, the difference is marked.

## Runtime shape

```
+---------------------------+        +--------------------------+
|  scheduled jobs (cron /   | write  |  SQLite  kokpit.db       |
|  systemd timers)          | -----> |  (events, jobs, digest,  |
|  collect, translate,      |        |   social stats, health)  |
|  score, digest, stats     |        +-----------+--------------+
+---------------------------+                    | read-only
                                                 v
+---------------------------+        +--------------------------+
|  local Markdown vault     | read   |  web app (Starlette)     |
|  (your notes, tasks)      | -----> |  JSON API + one static   |
+---------------------------+ write  |  HTML page               |
        ^   quick capture only       +-----------+--------------+
        |                                        |
+---------------------------+                    v
|  optional Telegram bot    |        browser on your LAN / VPN
|  (morning line, buttons)  |
+---------------------------+
```

- **Jobs** do all network work and all writes. They run on a schedule and each one records its result in a `health` table (job name, last run, ok/error, row count).
- **Web app** opens the database read-only. The only writes it performs are quick capture into the Markdown vault (a new note or task line) and reminder state changes that the user triggers with a button.
- **One page**: the dashboard is a single static HTML file with client-side rendering over a small JSON API. No build step, no Node toolchain.
- **Bot** is optional. It sends one line in the morning and carries approval buttons (e.g. "publish this post"). Nothing is published without a button press or an explicit scheduled approval.

## Storage

| Store | Content | Who writes | Who reads |
|---|---|---|---|
| `kokpit.db` (SQLite) | events, jobs, scores, digest, social stats, reminders, health | jobs, bot | web app (ro) |
| Markdown vault | notes, threads, to-do, project logs | you, quick capture | web app |
| `.env` / `config.toml` | API tokens, city, paths, enabled modules | you | everything |
| `data/` | caches, audio files, generated images | jobs | web app |

Concurrency rule learned the hard way: SQLite writers must not overlap. The reference instance chains the morning jobs sequentially and opens connections with a 30 s busy timeout. Any generalised scheduler must keep that.

## Modules

Each module is a directory with the same shape:

```
modules/<name>/
  collect.py      scheduled job(s): fetch, normalise, upsert
  api.py          JSON routes mounted under /api/<name>
  view.js         the tab's client-side renderer (kept in the single HTML page today)
  schema.sql      tables owned by the module
  connection.md   what the module needs: accounts, tokens, scopes, data read
  tests/
```

Modules do not import each other. Cross-module data (e.g. "today" showing event count and open tasks) is assembled by the `today` module through the API of the others, not by reaching into their tables.

Reference instance vs target: the reference code is flat (one `web.py`, one `db.py`, one collector file per source). This repository uses the module layout above from M1 on; the reference modules are ported into it one by one (see roadmap).

## Location and language

Two settings drive most of the generalisation work:

- **Place**: a city name geocoded once (OpenStreetMap Nominatim) to lat/lon, country and province, plus a radius or an explicit list of neighbouring provinces. Weather, event sources and the map centre derive from it. See `modules.md` > Events for the source selection rules.
- **Language**: UI language and translation target. The reference instance translates everything to Turkish and classifies events with Italian keyword rules; both become language packs.

## Scheduling

The reference instance uses user crontab plus `systemd-run --user` for long one-off jobs. Kokpit ships `kokpit schedule`: a foreground loop (stdlib only) that runs the jobs of the enabled modules one after another, so SQLite writers never overlap. A failing job is recorded in Health and the loop goes on; SIGTERM stops it cleanly; a lock file in the data folder stops a second copy. `bot-poll` is not scheduled: `kokpit bot poll` runs it. `kokpit run-job <name>` stays for any other scheduler (cron, systemd, launchd, Task Scheduler).

Table (local time, set `TZ` to change it; intervals are counted from midnight):

- weather every 30 min, calendar every 15 min (module today)
- brain-check every 60 min (brain), ai_usage every 60 min (ai_usage)
- events-collect and career daily at 06:30 (events, jobs)
- instagram at 07:00 and 19:00, instagram-plan at 07:00 (instagram)
- bot-morning at 07:30 or the `[bot]` morning time, bot-instagram-remind every 15 min (bot)

A job that never ran is run once at start. The loop touches `.scheduler-heartbeat` in the data folder every 15 s; the Docker healthcheck fails if it is older than 90 s.

## Network exposure

The dashboard binds to localhost by default. The reference instance binds to a Tailscale address; that is one option among several (LAN IP, reverse proxy, VPN) and is not a requirement.
