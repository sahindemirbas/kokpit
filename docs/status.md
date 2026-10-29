# Status: what exists today

Last reviewed: 29 October 2026.

Kokpit is extracted from a working single-user instance (Python 3, Starlette, SQLite, cron, Telegram bot) running on a Raspberry Pi 5 since 2025. This repository does not contain that code yet; the code will be moved in module by module, stripped of personal data, once the design below is settled.

## Working in the reference instance

- Today tab with calendar, tasks, upcoming dates, decisions waiting, weather for two cities, counts from other modules, system health.
- Brain tab over a local Markdown vault: threads, lanes, timeline, quick capture.
- Events: multi-source collection, dedupe, category rules, translation, list/calendar/map views, `.ics` per event, optional public static page.
- Career: LinkedIn guest search plus three company-site adapters, rule-based scoring with reasons, lifecycle tracking, market view, CSV export.
- Social: Instagram insights (account, media, stories) through the official API, approved publishing, content calendar with reminders, TikTok read-only stats from a local collector.
- AI usage: Hermes `state.db` reader, per-model/session view, rule-based advice.
- Agenda: daily digest with scored items and audio summary.
- System health: per-job status, morning warnings.
- Telegram bot: morning line, commands, approval buttons.

## Needs generalisation (hard-wired today)

- City, country, neighbouring provinces, map centre, weather cities.
- Translation target (Turkish) and classification keyword language (Italian).
- Vault path and vault structure assumptions.
- Account names and chat ids.
- Flat code layout; no module boundaries; no `kokpit init`.
- Scheduler lines written by hand.

## Planned, not started

- `kokpit init` / `kokpit run` / `kokpit schedule` / `kokpit doctor` commands.
- Connections screen with per-connection read/permission/last-update panel.
- CV extraction and target-profile form.
- Adapter contract and loader for `adapters/events/<city>/` and `adapters/jobs/<source>/`.
- Language packs.
- Docker image.
- Licence decision and public release checklist.

## What is deliberately out of scope for the first release

Hosted multi-user service, mobile app, cloud sync. Each user runs their own instance.
