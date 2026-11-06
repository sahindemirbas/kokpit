# Changelog

All notable changes to Kokpit are listed here. Versions follow [Semantic Versioning](https://semver.org/); before 1.0, minor versions may change configuration and behaviour.

## Unreleased

- Hermes plugin 0.4.0: `/kokpit setup [path]` makes a starter vault (or adopts an existing one) and stores `vault_path` through Hermes; never overwrites a file. Demo answers end with one line pointing at setup. `/kokpit brief` (also `kokpit_today` with `brief: true` and `hermes kokpit-brief`) is a short morning brief. Brain growth follows the card and receipt model: a session writes one `Last-Session.md` card and one short receipt (at most 300 characters, refs = notes touched) only when it produced a result (capture, file written by a tool, accepted night proposal, or an explicit `/kokpit done <summary>` / `kokpit_capture` kind `receipt`); chat-only sessions write nothing. The first-turn context shows the latest card's open items and the latest receipt line, labelled as historical claims. No message buffer, no model for the card, no session log. Missing vault answers in `brief` and `today` carry a Fix line. Plugin README rewritten as the catalog front page.

- Night pass: inline `Açık: Sende: ...; Bende: ...` thread items are read with the contract parser, Turkish (also Italian/English) day-month dates feed the stale-thread check, and Turkish/Italian stop words plus a 30% document-frequency cut keep generic words out of link reasons.
- Hermes plugin 0.3.0: capture from anywhere. `/kokpit note|todo|j|journal <text>` works in the CLI and every Hermes gateway (Telegram through your own Hermes bot) and `kokpit_capture` has a `journal` kind (`- HH:MM text` in `daily/YYYY-MM-DD.md`, setting `journal_dir`). New night pass (`night.py`, tool `kokpit_night`, `/kokpit night [run]`, `/kokpit add|skip <id>`, CLI `hermes kokpit-night`): rule-based `todo`, `improve`, `link`, `idea` and `news` proposals, each quoting its sources word for word, stored outside the notes (plugin data dir, else `<vault>/.kokpit/night.json`). Notes are never edited; accept writes only through append-only capture. News from the Kokpit `briefing_items` table or a vault `news/` folder. Optional `night_auto` (session start, once per 20 h) and `night_model` (host `ctx.llm`, each proposal checked in code, rejected ones counted). Brain block line, Night card in the dashboard tab and desktop pane, demo vault with journal and news. New settings `journal_dir`, `news_dir`, `stale_days`, `news_days`, `night_auto`, `night_model`. Tested on synthetic vaults only.
- Kokpit bot: `/note`, `/todo` (with `due:YYYY-MM-DD`) and `/journal` (`/j`) capture into the vault; optional `[bot] plain_text = "journal"` or `"note"`; `[brain] journal_dir`. The dashboard capture API accepts kind `journal`.
- Hermes plugin 0.2.1: `Threads.md` owners with an empty value (`yok`, `none`, `-` ...) are no longer open items, bracket notes after an owner are kept (`note`, ISO date also `due`), and the panels show "You", "Me · Fri", "Someone". Older databases whose `runs` table has no `rows` column keep job health (`records: null`); a missing table reads "not collected here" and another shape "unknown format" on the cards. New `expected` setting for `kokpit_health`.
- Podcast: Gemini speech is planned for the whole episode, intro included, in at most `max_requests` requests (default 10; `max_turns_stretch` lets a request hold up to 16 lines, and extra lines are trimmed with a note). The `openai_compatible` path is unchanged.

## 0.1.0 (2026-10-31)

First public release. Kokpit runs on Python 3 with the standard library only and needs no AI key.

- Dashboard served by `http.server`, data in SQLite, configuration in `kokpit.toml`, keys in `.env`.
- Setup screen on first run (`/setup`) and `python -m kokpit init`; interface in English and Turkish.
- Modules: Today, Brain (Markdown vault, receipts, context hook for agents), Events (AllEvents, Eventbrite, local adapters, category language packs), Career, Briefing (digest, optional TTS and podcast), Social (Instagram analysis and approved publishing), AI usage (Hermes, Claude Code, Codex), Organiser (proposals only), Health and Connections, Telegram bot.
- Optional modules are off until switched on. A model connection (OpenAI-compatible or Hermes) is opt-in per use.
- `python -m kokpit run --port N` for a one-off port, `KOKPIT_NO_BROWSER=1` for headless machines, `docker compose up` path.
- Demo city is Ghent, Belgium; every fixture is synthetic.
- 567 tests on GitHub Actions.

Known gaps: no `pip install` yet, no `kokpit doctor`, the setup screen guesses event slugs from the English city name (see docs/add-your-city.md).
