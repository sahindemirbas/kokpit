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

## Rule-based baseline per module (no model key)

Collection never uses a model in any module; fetch, parse and store are plain Python. This table is about the layer on top.

| Module | Without a model today | Model adds | Baseline status |
|---|---|---|---|
| Today | Full: calendar, tasks, weather, counts, health | nothing | working |
| Brain | Full: threads, lanes, timeline, capture | nothing (agents write to the vault, the tab only reads) | working |
| Events | Collection, dedupe, keyword category rules, views, `.ics` | Turkish translation, "other" reclassification, description enrichment | working; untranslated items show the original text |
| Career | Collection, keyword scoring with reasons, lifecycle, market view, CSV | translated titles for high-score jobs | working |
| Social | Insights, approved publishing, calendar | caption/visual drafts | working for stats; drafting is model-only, no template path yet |
| AI usage | Reader, per-model view, rule-based advice | nothing | working |
| Agenda (digest) | Item scoring and listing | summary text, item selection, audio | scoring is rule-based; summary without a model falls back to the plain list, to be formalised |
| Morning report | Template text with verified times and temperatures | free-form wording | working; the template is also the safety fallback when model output fails verification |
| System health | Full | nothing | working |
| Telegram bot | Full | nothing | working |

## Needs generalisation (hard-wired today)

- City, country, neighbouring provinces, map centre, weather cities.
- Translation target (Turkish) and classification keyword language (Italian).
- Vault path and vault structure assumptions.
- Account names and chat ids.
- Flat code layout; no module boundaries; no `kokpit init`.
- Scheduler lines written by hand.

## Working in the M1 skeleton (checked live on 29 Oct 2026, Cremona)

- `kokpit init --city` geocodes via Nominatim, tries slug candidates against the real sites, writes `kokpit.toml` + starter vault. Events stay `false` in `[modules]` until you switch them on.
- Calendar on Today: read-only ICS feeds (`calendar` job, today plus 6 days, recurring events, masked addresses). Google read-only calendar is still planned.
- Jobs: `weather` (Open-Meteo), `events-collect` (AllEvents 24 + Eventbrite 18 rows for Cremona), `brain-check`.
- Dashboard tabs: Today, Events (category chips, per-event `.ics`), Brain, Health, Connections (reads / writes / permission / last run per connection; a connection with 0 records today is flagged).
- `kokpit context "<message>"` prints the read-only brain block (threads, last session, upcoming dates, related notes); `integrations/hermes/kokpit-brain/` wraps it as a Hermes `pre_llm_call` plugin. Tests: 18.

## Planned, not started

- `kokpit schedule` / `kokpit doctor` commands (`init --city` and `run` exist; init verifies the AllEvents/Eventbrite slugs live and prints the record count per source).
- CV extraction and target-profile form.
- Adapter contract and loader for `adapters/events/<city>/` and `adapters/jobs/<source>/`.
- Language packs.
- Docker image.
- Public release checklist (licence is MIT, see `LICENSE`).

## What is deliberately out of scope for the first release

Hosted multi-user service, mobile app, cloud sync. Each user runs their own instance.
