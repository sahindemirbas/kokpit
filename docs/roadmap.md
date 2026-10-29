# Roadmap

Milestones are ordered; each one is usable on its own. No dates: the author works on this between other commitments.

## M0: design (this repository today)

- README, architecture, modules, connections, privacy, status, adapter contract, example config.
- Decision: repository first, Hermes integration optional (done).
- Decision: city is a user setting, sources chosen by country, local adapters per city (done).

## M1: core skeleton

- Package layout `kokpit/` with `today`, `brain`, `events`, `health` modules and the single-page dashboard.
- `kokpit init` (city, vault path, modules), `kokpit run`, `kokpit run-job <name>`.
- Empty starter vault structure and `docs/brain-protocol.md` conventions; packaged context hook script that any agent can call.
- Health table and Connections screen (read-only view first).
- Synthetic fixtures and tests for date detection, dedupe, category rules.

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
- [ ] Every fixture reviewed as synthetic.
- [ ] Third-party code and data-source terms reviewed; licence chosen (must be compatible with the MIT notice of Avenox Beyin).
- [ ] Every file derived from Avenox Beyin carries the MIT copyright line and a header stating the modification.
- [ ] Docs re-read for anything that describes a non-existent feature as existing.
- [ ] Author's explicit go decision recorded.
