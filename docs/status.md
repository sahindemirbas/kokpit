# Status: what exists today

Last reviewed: 30 October 2026.

Kokpit is extracted from a working single-user instance (Python 3, Starlette, SQLite, cron, Telegram bot) running on a small home server since 2025. The M1 to M3 modules and the AI usage readers are now in this repository. The rest (social, agenda digest, Telegram bot) still lives only in the reference instance and will be moved in module by module, stripped of personal data.

## Working in the reference instance

- Today tab with calendar, tasks, upcoming dates, decisions waiting, weather for two cities, counts from other modules, system health.
- Brain tab over a local Markdown vault: threads, lanes, timeline, quick capture.
- Events: multi-source collection, dedupe, category rules, translation, list/calendar/map views, `.ics` per event, optional public static page.
- Career: LinkedIn guest search plus three company-site adapters, rule-based scoring with reasons, lifecycle tracking, market view, CSV export.
- Social: Instagram analysis (account, posts, reels, stories insights) through the official API, working; content calendar with a Health guard and approved publishing (Approve button, publish switch, limit check) working, approval and reminders through the bot working (synthetic tests); TikTok read-only stats from a local collector generalise.
- AI usage: Hermes `state.db` reader, per-model/session view, rule-based advice.
- Agenda: daily digest with scored items and audio summary.
- System health: per-job status, morning warnings.
- Telegram bot: morning line, commands, approval buttons. In this repository (M5 step 1a): bot core with one allowed chat, `/help /today /health /plan`, morning line job, poll runner and callback registry, synthetic tests only; Instagram approval buttons and reminders in step 1b.

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
- Translation target (Turkish). Category keywords are generalised: language packs, English by default (docs/category-packs.md).
- Vault path and vault structure assumptions.
- Account names and chat ids.
- Flat code layout; no module boundaries; no `kokpit init`.
- Scheduler lines written by hand.

## Working in the M1 skeleton (checked live on 29 Oct 2026 on one configured Italian city)

- `kokpit init --city` geocodes via Nominatim, tries slug candidates against the real sites, writes `kokpit.toml` + starter vault. Events stay `false` in `[modules]` until you switch them on.
- Calendar on Today: read-only ICS feeds (`calendar` job, today plus 6 days, recurring events, masked addresses). Google read-only calendar is still planned.
- Jobs: `weather` (Open-Meteo), `events-collect` (AllEvents 24 + Eventbrite 18 rows for that city), `brain-check`.
- Dashboard tabs: Today, Events (category chips, per-event `.ics`), Brain, Health, Connections (reads / writes / permission / last run per connection; a connection with 0 records today is flagged).
- `kokpit context "<message>"` prints the read-only brain block (threads, last session, upcoming dates, related notes); `integrations/hermes/kokpit-brain/` wraps it as a Hermes `pre_llm_call` plugin. Tests: 18.
- Freshness line on every data card (weather, calendar, tasks/brain-check, Events tab): last successful update, record count, "0 records", "never run" or "last run failed"; one `freshness` shape `{job, last_status, last_success, records}` from the health table. Tests: 44.
- `kokpit place "<city>"`: Open-Meteo geocoder candidates, per-country event source table (unknown countries marked unverified), accent-free slugs kept as comments until `--confirm-slug`, `--write` edits `kokpit.toml` in place with a `.bak` copy and refuses if any other setting would change. Tests: 54.
- Local event adapter loader: `adapters/events/<id>/collect.py` plus user folders from `[events] adapter_dirs`, imported only when listed in `[events] adapters`; import errors, missing `collect`, exceptions and wrong return types become short warnings on the `events-collect` run; records go through the same radius filter, categories and upsert with `source` = adapter name; `kokpit adapters` lists them; offline `example-city` template. Tests: 61.
- Category language packs: JSON keyword packs, English ids and labels, `[events] category_languages = ["en"]` by default, `it` built in but off, own packs via `category_pack_dirs`. Tests: 67.
- M2 (events for any city) complete on 29 Oct 2026. Moved out: translation target (M5, needs the model connection) and Google read-only calendar (Later; the secret ICS address already covers it). Tests: 76.

## Planned, not started

- `kokpit schedule` / `kokpit doctor` commands (`init --city` and `run` exist; init verifies the AllEvents/Eventbrite slugs live and prints the record count per source).
- CV extraction and target-profile form.
- Adapter loader for `adapters/jobs/<source>/` (events loader is done).
- Language packs.
- Docker image.
- Public release checklist (licence is MIT, see `LICENSE`).

## What is deliberately out of scope for the first release

Hosted multi-user service, mobile app, cloud sync. Each user runs their own instance.
