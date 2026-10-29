# Roadmap

Milestones are ordered; each one is usable on its own. No dates: the author works on this between other commitments.

## M0: design (this repository today)

- README, architecture, modules, connections, privacy, status, adapter contract, example config.
- Decision: repository first, Hermes integration optional (done).
- Decision: city is a user setting, sources chosen by country, local adapters per city (done).

## M1: core skeleton (done)

Done:
- Package layout `kokpit/` with `today`, `brain`, `health` modules; single static page over a JSON API; standard library only (`http.server`, `sqlite3`, `tomllib`).
- `./run`, `kokpit init` (vault path, db path), `kokpit run`, `kokpit run-job <name>`, `kokpit jobs`.
- Empty starter vault; brain reader for Threads / To-Do / Last-Session with the owner lanes (on you / on me / on someone else), upcoming dates from `due:` tags only, quick capture (append-only).
- Health table (`runs`) with "0 records" and "did not run today" warnings; `heartbeat` and `brain-check` jobs.
- Tests on synthetic fixtures (brain parsing, capture, health warnings) and one end-to-end test that starts the server.
- `events` module skeleton; `kokpit init` asks for the city and geocodes it. `events` stays `false` in `[modules]` after init until a source is enabled.
- Packaged context hook script that any agent can call (Beyin MIT notice).
- Connections screen (read-only view).
- Weather on the Today tab: Open-Meteo, cached by the `weather` job, today plus the next 4 days, stale cache shown with its age.

Moved to M2:
- Calendar on the Today tab (ICS URL or Google read-only). The Today payload carries `calendar.status = "not connected"` until then.

## M2: events for any city

- Calendar connection for Today (ICS URL first, Google read-only second), read-only scope only. Status: ICS URL done (`calendar_ics_urls`, `calendar` job); Google read-only still open.
- Geocode and place settings; country-based source enabling; slug confirmation. Status: done (`python -m kokpit place "<city>"`: Open-Meteo geocoder candidates, `--pick N` prints the `[place]`/`[events]` block with a per-country source table, `--write` edits kokpit.toml in place with a `.bak` copy, slugs stay commented until `--confirm-slug`).
- Adapter loader for `adapters/events/<city>/`; Cremona examples ported and disabled by default.
- Language pack for categories (Italian first, English second) and translation target setting.
- "Last successful update: 0 records" surfaced everywhere. Status: done (`freshness` field on Today weather/calendar/brain/events and `/api/events`; never run, 0 records and last-run-failed shown on each card).

## M3: career

- Profile form (skills, targets, locations, remote) and local CV text extraction.
- LinkedIn guest search and the adapter contract for `adapters/jobs/`.
- Scoring with reasons, lifecycle, market view, CSV export.

## M4: social and AI usage

- Instagram connection flow with separate analyse/publish switches; content calendar; approval through the bot.
- AI usage readers (Hermes, Claude Code, Codex) and the limits panel; advice rules with evidence.

## M5: agenda, bot, Docker

- Topic configuration for the digest; optional TTS.
- Brain-driven agenda matching with a rule-based fallback and a model opt-in.
- Night organiser as an opt-in module: categorise, link, conditions, enrich, sync; deterministic morning report.
- Telegram bot as an optional module.
- `docker compose up` path.

## Pre-public checklist (blocks any visibility change)

- [x] Secret scan of the whole history (gitleaks 8.30 over all commits and the working tree plus a regex pass, 2026-10-29, issue #4; repeated right before the visibility change).
- [x] Every fixture reviewed as synthetic (`docs/demo-vault/`, `docs/media/`, `tests/` re-read on 2026-10-29, issue #5: irrigation timer and Italian lessons only, vault path `/tmp/kokpit-demo/vault`; the city name in tests and the example config is a public slug example).
- [x] Licence chosen: MIT (`LICENSE`), compatible with the MIT notice of Avenox Beyin; third-party review in `THIRD_PARTY_NOTICES.md`. Data-source terms are reviewed per module as modules land.
- [x] Every file derived from Avenox Beyin carries the MIT copyright line and a header stating the modification (checked 2026-10-29: no file is derived from Beyin; `kokpit/modules/brain/context.py` implements the contract itself and says so, notice in `THIRD_PARTY_NOTICES.md`).
- [x] Docs re-read for anything that describes a non-existent feature as existing (2026-10-29, issue #6: README status line and "Try it" brought up to the M1 tab set; brain-protocol, modules, architecture wording fixed).
- [ ] Author's explicit go decision recorded.
