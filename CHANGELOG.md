# Changelog

All notable changes to Kokpit are listed here. Versions follow [Semantic Versioning](https://semver.org/); before 1.0, minor versions may change configuration and behaviour.

## Unreleased

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
