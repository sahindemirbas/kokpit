# Status: what exists today

Last reviewed: 31 October 2026.

Kokpit is extracted from a working single-user instance (Python 3, Starlette, SQLite, cron, Telegram bot) running on a small home server since 2025. That reference instance is a separate, private code base. This repository does not use Starlette: the dashboard is served by the standard library (`http.server.ThreadingHTTPServer` in `kokpit/web.py`).

Every module of the reference instance that is in the first public scope is now in this repository under `kokpit/modules/`: today, brain, events, career, briefing, instagram (Social), ai_usage, organiser, health, connections, model and bot. Each was moved in stripped of personal data and is tested on synthetic fixtures. Optional modules stay off until you switch them on.

## In this repository

- Today tab with calendar, tasks, upcoming dates, decisions waiting, weather, counts from other modules, freshness per card.
- Brain tab over a local Markdown vault: projects, threads, quick capture (append-only), Meetings; session receipts and the context hook for agents (`kokpit brain`, `kokpit context`).
- Events: multi-source collection, dedupe, category language packs, local adapters, optional translation through the model connection, per-event `.ics`.
- Career: profile and CV import, LinkedIn guest search (off by default), Indeed and Jooble through an optional local headless browser, local job adapters, rule-based scoring with reasons, lifecycle, market view, skills plan, CSV export.
- Social: Instagram analysis (account, posts, reels, stories) through the official API, content calendar, publishing only after approval, with separate analyse and publish switches, both off by default. `kokpit instagram setup|check`.
- AI usage: readers for Hermes, Claude Code and Codex, per-model and per-hour views, limits, rule-based advice.
- Briefing: daily digest from your feeds with rule-based scoring and brain matching; optional model summaries, TTS and a two-voice podcast.
- Organiser: opt-in night job that only proposes changes.
- System health and Connections: per-job status, "0 records" and "never run" warnings.
- Telegram bot: one allowed chat, `/help /today /health /plan`, the morning line, Instagram approval buttons and reminders. Off by default.

Not carried over from the reference instance: TikTok stats, the version of the night organiser that writes to notes, and the personal extras listed in docs/modules.md.

## Rule-based baseline per module (no model key)

Collection never uses a model in any module; fetch, parse and store are plain Python. This table is about the layer on top.

| Module | Without a model today | Model adds | Baseline status |
|---|---|---|---|
| Today | Full: calendar, tasks, weather, counts, health | nothing | working |
| Brain | Full: threads, lanes, timeline, capture | nothing (agents write to the vault, the tab only reads) | working |
| Events | Collection, dedupe, keyword category rules, views, `.ics` | Turkish translation, "other" reclassification, description enrichment | working; untranslated items show the original text |
| Career | Collection, keyword scoring with reasons, lifecycle, market view, weekly counts, skills plan, CSV | translated titles for high-score jobs, weekly note | working |
| Social | Insights, approved publishing, calendar | caption/visual drafts | working for stats; drafting is model-only, no template path yet |
| AI usage | Reader, per-model view, rule-based advice | nothing | working |
| Briefing (digest) | Item scoring and listing | summary text, item selection, audio | scoring is rule-based; summary without a model falls back to the plain list, to be formalised |
| Morning report | Template text with verified times and temperatures | free-form wording | working; the template is also the safety fallback when model output fails verification |
| System health | Full | nothing | working |
| Telegram bot | Full | nothing | working |

## Generalised since the reference instance

The reference instance hard-wires its city, language, vault path, accounts and schedule. In this repository each of these is a setting: `[place]` and `kokpit place`, `[ui] language` and locale files, category language packs, `[events] translate_to`, `vault_path`, the bot `chat_id`, module directories under `kokpit/modules/`, `kokpit init` and the setup screen, and `kokpit schedule`.

## Change log of checks (checked live on 29 Oct 2026 on one configured Italian city unless noted)

- `kokpit init --city` geocodes via Nominatim, tries slug candidates against the real sites, writes `kokpit.toml` + starter vault. Events stay `false` in `[modules]` until you switch them on.
- Calendar on Today: read-only ICS feeds (`calendar` job, today plus 6 days, recurring events, masked addresses). Google read-only calendar is still planned.
- Jobs: `weather` (Open-Meteo), `events-collect` (AllEvents 24 + Eventbrite 18 rows for that city), `brain-check`.
- Dashboard tabs: Today, Events (category chips, per-event `.ics`), Brain, Health, Connections (reads / writes / permission / last run per connection; a connection with 0 records today is flagged).
- `kokpit context "<message>"` prints the read-only brain block (threads, last session, upcoming dates, related notes); `integrations/hermes/kokpit-brain/` wraps it as a Hermes `pre_llm_call` plugin. Tests: 18.
- Connect commands (31 Oct 2026): `kokpit hermes status|setup` (model provider, usage reader, plugin link, env lines; y/N per step, `--dry-run`, `--yes`) and `kokpit instagram setup|check` (Meta steps with links, hidden token prompt, one read-only `GET /me`, token file mode 600). The Social tab has the same steps and a token paste form (`POST /api/instagram/token`, the answer never contains the token).
- Freshness line on every data card (weather, calendar, tasks/brain-check, Events tab): last successful update, record count, "0 records", "never run" or "last run failed"; one `freshness` shape `{job, last_status, last_success, records}` from the health table. Tests: 44.
- `kokpit place "<city>"`: Open-Meteo geocoder candidates, per-country event source table (unknown countries marked unverified), accent-free slugs kept as comments until `--confirm-slug`, `--write` edits `kokpit.toml` in place with a `.bak` copy and refuses if any other setting would change. Tests: 54.
- Local event adapter loader: `adapters/events/<id>/collect.py` plus user folders from `[events] adapter_dirs`, imported only when listed in `[events] adapters`; import errors, missing `collect`, exceptions and wrong return types become short warnings on the `events-collect` run; records go through the same radius filter, categories and upsert with `source` = adapter name; `kokpit adapters` lists them; offline `example-city` template. Tests: 61.
- Category language packs: JSON keyword packs, English ids and labels, `[events] category_languages = ["en"]` by default, `it` built in but off, own packs via `category_pack_dirs`. Tests: 67.
- M2 (events for any city) complete on 29 Oct 2026. Moved out: translation target (M5, needs the model connection) and Google read-only calendar (Later; the secret ICS address already covers it). Tests: 76.
- Career web app (30 Oct 2026): three leaves, Jobs (profile, CV import, score check, listings), Market (per-week bars, weekly note, top companies and skills, CSV) and Skills (skills plan with learning / known / skip marks). Checked in a browser on a synthetic database at 1440 and 390 px.

## Planned, not started

- `kokpit doctor` command (`kokpit schedule` is done; `init --city` and `run` exist; init verifies the AllEvents/Eventbrite slugs live and prints the record count per source).
- `pip install kokpit` from PyPI. Until then, clone the repository and use `./run`.
- Google read-only calendar through OAuth (the secret ICS address already works for Google Calendar).

Done since the previous review: CV import and the profile form, the adapter loader for `adapters/jobs/`, language packs, `docker compose up` (docs/docker.md) and the public release checklist (docs/roadmap.md).

## What is deliberately out of scope for the first release

Hosted multi-user service, mobile app, cloud sync. Each user runs their own instance.

## Setup screen and languages

First-run setup in the browser (`/setup`) and in `kokpit init`, keys in `.env`, `[ui] language` with en and tr dictionaries. Every screen of the dashboard follows the language: labels, dates, numbers and short status words come from `kokpit/locales`, a missing key shows the English text. Data from sources and free-text messages from the server (score reasons, notes, error text) stay as received. Tests: `tests/test_locale.py`.
