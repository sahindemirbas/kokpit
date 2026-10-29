# Roadmap

Milestones are ordered; each one is usable on its own. No dates: the author works on this between other commitments.

## M0: design (this repository today)

- README, architecture, modules, connections, privacy, status, adapter contract, example config.
- Decision: repository first, Hermes integration optional (done).
- Decision: city is a user setting, sources chosen by country, local adapters per city (done).

## M1: core skeleton (in progress)

Done:
- Package layout `kokpit/` with `today`, `brain`, `health` modules; single static page over a JSON API; standard library only (`http.server`, `sqlite3`, `tomllib`).
- `./run`, `kokpit init` (vault path, db path), `kokpit run`, `kokpit run-job <name>`, `kokpit jobs`.
- Empty starter vault; brain reader for Threads / To-Do / Last-Session with the owner lanes (on you / on me / on someone else), upcoming dates from `due:` tags only, quick capture (append-only).
- Health table (`runs`) with "0 records" and "did not run today" warnings; `heartbeat` and `brain-check` jobs.
- Tests on synthetic fixtures (brain parsing, capture, health warnings) and one end-to-end test that starts the server.

Open:
- `events` module skeleton and `kokpit init` asking for the city.
- Packaged context hook script that any agent can call (Beyin MIT notice).
- Connections screen (read-only view).
- Weather and calendar on the Today tab.

## M2: events for any city

- Geocode and place settings; country-based source enabling; slug confirmation.
- Adapter loader for `adapters/events/<city>/`; Cremona examples ported and disabled by default.
- Language pack for categories (Italian first, English second) and translation target setting.
- "Last successful update: 0 records" surfaced everywhere.

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

- [ ] Secret scan of the whole history.
- [ ] Every fixture reviewed as synthetic (`docs/demo-vault/` and `docs/media/` were written as synthetic on 2026-10-29; re-check before going public).
- [x] Licence chosen: MIT (`LICENSE`), compatible with the MIT notice of Avenox Beyin; third-party review in `THIRD_PARTY_NOTICES.md`. Data-source terms are reviewed per module as modules land.
- [ ] Every file derived from Avenox Beyin carries the MIT copyright line and a header stating the modification (none exist yet; re-check when the hook script is packaged).
- [ ] Docs re-read for anything that describes a non-existent feature as existing.
- [ ] Author's explicit go decision recorded.
