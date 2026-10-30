<p align="center">
  <img src="docs/media/logo.png" width="120" alt="Kokpit logo">
</p>

<h1 align="center">Kokpit</h1>

<p align="center">
  <b>Your morning on one page.</b><br>
  A self-hosted personal dashboard for a Raspberry Pi or any small server. No AI key needed.
</p>

<p align="center">
  <a href="#quick-start">Quick start</a> •
  <a href="docs/status.md">Status</a> •
  <a href="docs/roadmap.md">Roadmap</a> •
  <a href="docs/add-your-city.md">Add your city</a> •
  <a href="README.tr.md">Türkçe</a>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/python-3.11%2B-3776AB?style=flat-square&logo=python&logoColor=white" alt="Python 3.11+">
  <img src="https://img.shields.io/badge/dependencies-stdlib%20only-2ea44f?style=flat-square" alt="Standard library only">
  <img src="https://img.shields.io/badge/runs%20on-Raspberry%20Pi-C51A4A?style=flat-square&logo=raspberrypi&logoColor=white" alt="Runs on Raspberry Pi">
  <img src="https://img.shields.io/badge/license-MIT-blue?style=flat-square" alt="MIT licence">
  <a href="https://github.com/sahindemirbas/kokpit/actions/workflows/tests.yml"><img src="https://github.com/sahindemirbas/kokpit/actions/workflows/tests.yml/badge.svg" alt="Tests"></a>
</p>

<p align="center">
  <img src="docs/media/demo.gif" alt="Kokpit demo: Today with live weather, Events, Brain with quick capture, Health, Connections" width="900">
</p>

Kokpit gathers the things you check every morning (calendar, tasks, your notes, events in your city, job listings, the health of your own scripts) into one small web page that runs on your own device. It reads only the data you connect, and keeps everything in SQLite and plain Markdown.

> **Status:** early. Milestones M1 to M3 are done; from M4, the AI usage tab and Instagram analysis are done; the dashboard below runs today with `./run`. Social publishing, the daily digest, and the Telegram bot are planned; `docker compose up` works (see Docker below). The design comes from a single-user instance that has run every day on a small home server since 2025. Full breakdown: [docs/status.md](docs/status.md).

## Features

**Working now**

- **Today:** calendar (read-only ICS), open tasks, upcoming dates, live weather from Open-Meteo.
- **Brain:** your Markdown notes as projects and open threads, with quick capture that only ever appends.
- **Events:** what is on in your city from AllEvents and Eventbrite, plus local adapters and category rules in your language.
- **Career:** a local profile, job listings scored with a written reason ("Python, SQL; Milan; remote not stated"), listing lifecycle, market view, CSV export.
- **Health:** the last successful run of every job. A source that silently returns nothing shows "0 records", not "OK".
- **Connections:** one screen with every data source, what it reads and when it last worked. Everything is off until you switch it on.
- **Social:** your own Instagram account, posts, reels and stories with their insights, read through the official API and stored as daily snapshots. Read-only, off by default.
- **AI usage:** reads the local logs of Hermes, Claude Code and Codex (read-only, each reader its own switch). Tokens per model and hour, an approximate cost from models.dev list prices, and rule-based advice with its evidence. A model without a known price is shown as unknown, not as 0. Optional token limits that you enter yourself. The only network call is the opt-in price refresh.

**Planned** ([roadmap](docs/roadmap.md))

- Instagram publishing with approval, content calendar
- Daily news digest linked to your projects, optional audio
- Telegram bot with the morning line and approval buttons
- `pip install kokpit`

## Why Kokpit

- **One notes folder, many AI agents.** Hermes, Claude Code, Codex or Antigravity can all work in the same Markdown vault. Each one reads the last session and open threads, then leaves a session card and a receipt of what it changed. You can switch tools mid-project and still audit every step. See [docs/brain-protocol.md](docs/brain-protocol.md).
- **No model required.** Collection is plain code and every module has a rule-based path. A model is an opt-in connection that adds summaries and translation (done for event translation: `[model]` + `[events] translate_to`, key in the environment).
- **Unknown is not zero.** A price, quota or measurement that cannot be determined is shown as unknown, never as 0. Features are labelled working, needs generalisation or planned.
- **Your device, your data.** There is no hosted service. Delete the app and your notes and database are still there.

## How it works

- **Standard library only.** `dependencies = []` in `pyproject.toml`; the server is `http.server`, storage is `sqlite3`, config is `tomllib`.
- **SQLite store.** One local database file plus plain Markdown notes. No hosted service.
- **Jobs record their own freshness.** Collection is plain Python. Every run stores its status and record count, so a source that returns nothing shows "0 records", not "OK".
- **Career scoring is rule based.** Each listing gets a score and a written reason you can read and argue with.
- **AI usage readers dedupe.** One API message that appears on several log lines is counted once. Unknown prices stay unknown.
- **Privacy by default.** Everything is local and off until enabled. Session ids are stored only as a short sha256 prefix, and message text is never read into the database.
- **Tests run on CI.** GitHub Actions runs the unit tests on Python 3.11, 3.12 and 3.13.

More in [docs/architecture.md](docs/architecture.md).

## Quick start

Python 3.11 or newer. Nothing else to install.

```bash
git clone https://github.com/sahindemirbas/kokpit
cd kokpit
./run        # creates kokpit.toml and a starter vault, serves http://127.0.0.1:8501
```

Set your city and point Kokpit at your own notes:

```bash
python -m kokpit place "Bergamo"            # list candidates from the Open-Meteo geocoder
python -m kokpit place "Bergamo" --pick 1 --write
```

Then set `vault_path` in `kokpit.toml` to your Markdown folder.

### Docker

```bash
docker compose up -d      # dashboard on http://127.0.0.1:8501 plus a scheduler for the enabled modules
```

Data lives in the `kokpit-data` volume. The port is bound to loopback because Kokpit has no login. Telegram bot, LAN access, backup: [docs/docker.md](docs/docker.md).

<details>
<summary><b>More commands and settings</b></summary>

<br>

| Command | What it does |
|---|---|
| `python -m kokpit jobs` | list the scheduled jobs |
| `python -m kokpit run-job <name>` | run one job now (`weather`, `events-collect`, `calendar`, ...) |
| `python -m kokpit adapters` | list local event adapters and whether they are on |
| `python -m kokpit career` | career profile and scoring |
| `python -m kokpit context` | print the brain context an agent hook would receive |
| `python -m unittest discover -s tests` | run the tests |

- **Calendar:** add one or more ICS addresses to `calendar_ics_urls` (or `KOKPIT_CALENDAR_ICS_URLS`). Treat an ICS address like a password.
- **Events:** set `events = true` in `kokpit.toml`. Event page slugs are only written with `--confirm-slug`, after you have checked them on the site.
- **Demo data:** the GIF uses the synthetic vault in [docs/demo-vault/](docs/demo-vault/) with the city set to Lodi, Italy:
  `python -m kokpit init --vault <copy> --db /tmp/demo.db --city "Lodi, Italy"`.

</details>

## Add your city

Generic sources work for any city once you type its name. Local sources (theatres, town halls, tourism offices) are small adapters under `adapters/events/<city>/`. Copying the template and pointing it at your own town is the easiest way to contribute. Event categories can be matched in your language through a JSON keyword pack.

Guides: [add-your-city.md](docs/add-your-city.md) · [adapter contract](adapters/README.md) · [category-packs.md](docs/category-packs.md)

## Documentation

| | |
|---|---|
| [Architecture](docs/architecture.md) | runtime, data flow, storage |
| [Modules](docs/modules.md) | every module: purpose, inputs, status |
| [Brain protocol](docs/brain-protocol.md) | one vault shared by several AI agents |
| [Connections](docs/connections.md) | permissions and secret handling |
| [Privacy](docs/privacy.md) | what never leaves your device |
| [Status](docs/status.md) · [Roadmap](docs/roadmap.md) | what exists, what is next |
| [Hermes integration](integrations/hermes/README.md) | optional, not required |

## Who it is for

People who have a device that stays on and do not mind editing a config file. It is not a hosted service, a phone app or a team workspace; each person runs their own instance.

## Project status and support

Kokpit is a personal project, built and used by its author. Issues and pull requests are welcome, but there is no support and no response-time guarantee. Breaking changes are possible before 1.0.

## Licence and credits

MIT, see [LICENSE](LICENSE). The package uses only the Python standard library.

The second-brain conventions build on [Avenox Beyin](https://github.com/avenoxai/avenoxbeyin) (MIT, © Avenox), run here as a modified local fork. No Beyin code is copied into this repository; details in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) and [docs/brain-protocol.md](docs/brain-protocol.md) §0.

Built by [Sahin Demirbas](https://github.com/sahindemirbas), a data professional based in Italy, for his own mornings first.
