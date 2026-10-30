# Modules

Status legend: **working** = runs daily in the reference instance; **generalise** = works, but hard-wired to the author's city, language or accounts; **planned** = designed, not implemented.

## Today

Default tab. Assembles one screen from the other modules.

- Inputs: calendar (connection), open tasks and upcoming dates from the brain module, items waiting on you, recent activity receipts, counts from events/career/digest/social, system health, weather for one or two cities.
- Connections: calendar (optional), weather (none needed, Open-Meteo), everything else via other modules.
- Rules: a date on a task is kept as its due date; a time is never invented from a date. Weather shows today's max/min and the next four days; stale data (max 6 h, same day) is labelled stale, missing measurements stay null.
- Status: working; two-city weather is a setting in the target (reference has the two cities fixed).

## Brain (second brain)

Reads a folder of Markdown files. Ships an empty starter structure, never the author's vault.

- Starter structure: `Threads.md` (active threads with "where we left off" and open items tagged with who owes the next step), `To-Do.md` (`## Open` with checkbox lines, optional `due: YYYY-MM-DD`), `notes/` by area, `projects/<name>/`.
- Dashboard: one card per thread, lanes "on you / on me / on someone else", session timeline, quick capture of a note or task.
- Rules: the web app writes only through quick capture (append a line). It never rewrites or deletes notes. Date detection scans thread titles, open items, task text and the notes folders you choose, not whole logs.
- Agent protocol: several AI tools (reference: Hermes, Claude Code, Codex, Antigravity) share the same vault. A read-only context hook injects open threads and the last session into every agent; at the end of a work session the agent writes a `Last-Session.md` card, updates `Threads.md` and the relevant note, and leaves a receipt. Full contract in `docs/brain-protocol.md`.
- Night organiser (**planned** module): scheduled job that files `inbox/` notes into folders, adds reciprocal links with a stated reason, appends dated status blocks when a condition in an older note has come true, folds new information into the note it belongs to, and separates ideas from projects. Uses a model, so it is opt-in and listed in Connections. A deterministic morning report (no model call) summarises the run.
- Connections: a local folder path. Optional: the Hermes agent integration (see `integrations/hermes`) which can fill the same files; not required. Night organiser: model provider, opt-in.
- Status: dashboard working with the author's vault; the empty starter structure and the folder-path setting are **generalise**; the read-only context hook is in this repository (`kokpit context`, M1); the night organiser runs in the reference instance but is not yet here (**planned**).

## Career

- Profile: skills, experience, languages extracted from the CV (local PDF or text) and corrected by the user; target roles, locations, remote preference, must-have and nice-to-have keywords. The CV history does not cap the target career; the user's target list is the primary input.
- Listings: LinkedIn guest search (7-day window, no login), company career sites through adapters (reference adapters: Deloitte/Avature, Accenture, BIP/Oracle HCM). Adding a source means adding an adapter under `adapters/jobs/`.
- Scoring: rule-based keyword score with a written reason per listing ("matches Python, SQL; location Milan; remote not stated"). A number without a reason is not shown.
- Lifecycle: a listing not seen in search is not closed; closure is read from the posting page. Career-site listings close after 3 days absent and reopen if seen again. Weekly trend from listing dates.
- Market view: company cards, weekly counts, CSV export.
- Privacy: matching uses skills and targets only; name, phone, e-mail are not needed and are not sent anywhere. Any external model call for CV parsing is opt-in with the exact text shown first.
- Status: profile form, local CV extraction (pdftotext, .txt, .md; CLI `kokpit career profile`) and scoring with reasons **working** (M3 step 1); listings (job adapters, optional LinkedIn guest search, the `career` job, dedupe, freshness) **working** (M3 step 2); lifecycle (open, closed after N days missing, reopened; LinkedIn closes only from the posting page), market view (`kokpit career market`, Career tab) and CSV export (`kokpit career export`, `/api/career/export.csv`) **working** (M3 step 3).

## Events

- Sources with a city parameter: AllEvents (`allevents.in/<slug>`, JSON-LD), Eventbrite city pages (return neighbouring-city noise, a distance filter is required). Italy-wide: Sagramap (food fairs). Weather: Open-Meteo by lat/lon.
- Place setup: user enters a city; geocode to lat/lon, country, province; choose radius or neighbouring provinces. Country decides which generic sources are enabled (Sagramap only for Italy). AllEvents/Eventbrite slugs are proposed and the user confirms them.
- Silent-empty trap: a wrong slug returns an empty list without error. The Connections screen must show "last successful update: 0 records" so this is visible.
- Local adapters: theatres, municipalities, tourism offices have no city parameter. They live under `adapters/events/<city>/` with the documented JSON contract. The repository ships one offline template (`example-city`), disabled by default; real city adapters stay with whoever runs them.
- Categories: English ids (food, concert, opera, theatre, dance, exhibition, cinema, fair, kids, sport, talk, other) with keywords from language packs (`en` default, `it` built in, your own via `category_pack_dirs`; see category-packs.md), order: source label > title > description > venue name last. Leftover "other" may be classified by a model during translation if that connection is enabled.
- Dedupe: same title, or cross-source same province + date + overlapping meaningful words; an official source wins over an aggregator.
- Views: list, calendar, map; filters by province, category, free entry, kids; per-event `.ics` download.
- Optional public page: a static Italian/your-language events page generated from the same data, publishable to GitHub Pages. Separate from the private dashboard.
- Status: pipeline **working** for the author's area; place setup, country-based source selection, language packs and the adapter contract are **generalise**; non-Italian cities get AllEvents/Eventbrite + weather only in the first version (honest limit).

## Social media

- Instagram: official Instagram API with Instagram Login (business/creator account, user's own Meta app, token stored locally). Account and media insights, stories twice a day because they expire. Publishing via container API, only after an approval button; collab invites supported; library music, stickers and pins are not possible via API and the docs say so.
- TikTok: read-only statistics from a local analytics database produced by a separate collector (no official analytics API for personal accounts; the reference uses a browser-based collector). Marked as **generalise**, likely to remain optional.
- Content calendar: planned slots with due time, kind (story/post/reel), who prepares it, caption, media link; reminders through the bot; a daily guard warns when nothing is planned.
- Permissions: analysis and publishing are separate toggles. A publish failure stores the platform's message and never retries silently.
- Instagram analysis in this repository (M4 step 2a): account fields and insights, recent posts and reels with their insights, stories with theirs, stored as snapshots per day in SQLite; per-metric errors recorded and shown; token refresh on demand; `instagram collect` and `instagram summary` commands; a Social tab (follower trend, latest posts, reels and stories, best by reach in 30 days, last collect, errors); a Connections entry. Setup and schedule: `docs/connections.md`.
- Content calendar and approved publishing in this repository (M4 step 2b): planned slots (due time in UTC, kind story/post/reel/carousel, owner, caption, public media URLs, optional collaborator usernames, status planned/ready/approved/published/failed) in the table `ig_plan`, created, edited and deleted through `/api/instagram/plan` and the Social tab. The job `instagram-plan` adds a Health warning when nothing is planned for the next `plan_guard_days` days (default 3, 0 turns it off), when a slot failed, or when publishing is on without a token. Publishing uses the container API (create container, poll until FINISHED, then `media_publish`; carousels create one child container per item) and happens only when you press Approve on that slot in the web tab and `publish = true`; nothing publishes from a timer. Before each publish the app reads `content_publishing_limit` and refuses when the quota is used up. A failure stores the platform's message on the slot, marks it failed and is never retried silently; Retry in the tab is a manual approval. Setup, permission `instagram_business_content_publish` and the limits of the API: `docs/connections.md`.
- Status: Instagram analysis **working** here; content calendar and approved publishing **working** and covered by synthetic tests (stubbed HTTP, no network; no real post has been published from this repository's tests), bot approval **planned**; TikTok **generalise**; a generic "add a platform" interface **planned**.

## Telegram bot

- Off by default (`bot = false` under `[modules]`). One allowed chat (`[bot] chat_id`); updates from any other chat are ignored and only counted, never answered. The bot token comes from `KOKPIT_TELEGRAM_TOKEN` or `[bot] token` and is never logged or shown, and it is scrubbed from error text.
- Commands: `/help`, `/today` (the morning line on demand), `/health` (current warnings or "All good."), `/plan` (next Instagram slots, or "Instagram module is off."). Anything else gets a short help line.
- Morning line: one deterministic line from data Kokpit already has (weather and calendar from Today, overdue tasks, upcoming events), plus Health warnings only when something is wrong. It works when the other modules are off. Job `bot-morning` sends it; `kokpit bot poll` also sends it once a day after `[bot] morning_time`.
- Runner: `python -m kokpit bot poll --once` (one getUpdates pass, cron friendly, also `run-job bot-poll`), `python -m kokpit bot poll` (long poll loop, Ctrl-C to stop), `python -m kokpit bot send-test` (one test message). The update offset is stored in `kokpit.db`, so a restart never replays a command.
- Callbacks: inline-button data is `prefix:rest`; `kokpit/modules/bot/callbacks.py` keeps a registry from prefix to handler, and unknown data answers "unknown action". Instagram approval buttons plug into this registry in a later step.
- A missing token or chat id with the module on is a Health warning, not a crash. The Connections tab shows whether each is configured (yes or no) and the last poll outcome.
- Status: core **working** and covered by synthetic tests (stubbed HTTP, no network); not yet run against the live Telegram API in this repository.

## AI usage

- Reads local usage data from tools that store it: the Hermes agent's `state.db`, Claude Code project logs and Codex session logs. Each reader is a separate switch (`[ai_usage] readers`), all off by default; the module itself is off until `ai_usage = true` under `[modules]`. Shows usage per tool, model and session, and data freshness per reader (newest row, file modification time).
- Three numbers kept apart: real API spend (entered by you, otherwise "not configured"), subscription fee (entered per tool, monthly), and the list-price equivalent of usage, always labelled "estimate, not a bill". Prices come from your local prices file; an optional, opt-in refresh reads the public models.dev price list. A model without a price is shown as unknown and left out of cost figures, never counted as zero: if no token in a window, tool, model or session has a price the figure is "unknown"; if only some do, the priced amount is shown as "partial" together with the unpriced token count and model names.
- Limits panel: you enter limit windows per tool (hours, token cap, optional reset time); it shows used/limit and the next reset. Without entries it says "not configured". Provider websites are not scraped, so the figure is a rolling approximation from local logs, not the provider's own counter.
- Time method: usage is stored per session, model and UTC hour (table `ai_usage_hours`). Claude Code and Codex lines carry per-event timestamps, so each event lands in its own hour and a session that crosses midnight is split between Today and yesterday. Hermes only keeps session totals: those are spread evenly over the hours between the session's first and last activity and marked "approximate" in the API (`approximate`) and the tab. Windows and limit windows sum the hour rows from the window start rounded down to the hour, so the edge can be off by under one hour. Upgrade: the old per-session table `ai_usage_rows` is left untouched and no longer read; the next collect fills the new table, so history reappears after one run.
- Advice: rule-based, no model call, nothing is changed automatically. Rules: very long sessions, cache writes that are large against cache reads, background (cron) sessions on premium models, models without a price, and a reader without recent data. Each item shows its evidence (numbers and short session hashes) and is labelled; gains are never added together.
- Session ids are stored only as a short sha256 hash. Source files are opened read-only.
- Status: all three readers, pricing, the three numbers, limits and the five advice rules are **working** and covered by synthetic tests. Not done: provider billing import, weekly message caps counted per message (only token windows), advice on model choice against task size.

## Agenda (digest)

- Daily digest of items in the areas you follow (reference: AI/tech releases, repos, security notes). Each item has a one-sentence summary, a "why it matters" line and a score; the top item per section is the headline.
- Optional audio summary (TTS) that keeps playing while you switch tabs.
- Brain-driven matching: active threads, project names and current blockers from the vault steer what is selected. Items that clearly relate to an active project are tagged with the project and a stated reason; tagged items come first in the digest and the audio summary. Only titles, tags and short summaries leave the vault. A rule-based fallback works without any model. Details in `docs/brain-protocol.md`, section 4.
- Connections: a model API key for summaries (opt-in, stated), a TTS provider (optional), the brain vault (read-only).
- Status: digest **working** for the author's topic set; brain-driven matching **working** in a first version tied to the author's vault paths (**generalise**); topic configuration **planned**.

## System health

- Every job records last run, outcome and row count. The Today tab and the morning bot line show warnings only when something is wrong. A job that must finish before the morning report is listed in an expected set.
- Status: **working**.

## Optional modules in the reference instance (not in the first public scope)

Chess training tracker, model-training watch, pharmacy-on-duty lookup. They stay out until the core is generalised.
