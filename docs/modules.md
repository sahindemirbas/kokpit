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
- Status: dashboard working with the author's vault; the empty starter structure and the folder-path setting are **generalise**; the hook scripts and the night organiser run in the reference instance but are not yet in this repository (**planned**).

## Career

- Profile: skills, experience, languages extracted from the CV (local PDF or text) and corrected by the user; target roles, locations, remote preference, must-have and nice-to-have keywords. The CV history does not cap the target career; the user's target list is the primary input.
- Listings: LinkedIn guest search (7-day window, no login), company career sites through adapters (reference adapters: Deloitte/Avature, Accenture, BIP/Oracle HCM). Adding a source means adding an adapter under `adapters/jobs/`.
- Scoring: rule-based keyword score with a written reason per listing ("matches Python, SQL; location Milan; remote not stated"). A number without a reason is not shown.
- Lifecycle: a listing not seen in search is not closed; closure is read from the posting page. Career-site listings close after 3 days absent and reopen if seen again. Weekly trend from listing dates.
- Market view: company cards, weekly counts, CSV export.
- Privacy: matching uses skills and targets only; name, phone, e-mail are not needed and are not sent anywhere. Any external model call for CV parsing is opt-in with the exact text shown first.
- Status: listings, scoring, lifecycle, market view **working**; CV extraction and the target-profile form **planned** (reference has the profile in a config file).

## Events

- Sources with a city parameter: AllEvents (`allevents.in/<slug>`, JSON-LD), Eventbrite city pages (return neighbouring-city noise, a distance filter is required). Italy-wide: Sagramap (food fairs). Weather: Open-Meteo by lat/lon.
- Place setup: user enters a city; geocode to lat/lon, country, province; choose radius or neighbouring provinces. Country decides which generic sources are enabled (Sagramap only for Italy). AllEvents/Eventbrite slugs are proposed and the user confirms them.
- Silent-empty trap: a wrong slug returns an empty list without error. The Connections screen must show "last successful update: 0 records" so this is visible.
- Local adapters: theatres, municipalities, tourism offices have no city parameter. They live under `adapters/events/<city>/` with the documented JSON contract. The reference instance's Cremona adapters (opera house season, municipality agenda, tourism office) ship as examples, disabled by default.
- Categories: keyword rules per language (sagra, concert, opera, theatre, dance, exhibition, cinema, fair, kids, sport, talk, other), order: source label > title > description > venue name last. Leftover "other" may be classified by a model during translation if that connection is enabled.
- Dedupe: same title, or cross-source same province + date + overlapping meaningful words; an official source wins over an aggregator.
- Views: list, calendar, map; filters by province, category, free entry, kids; per-event `.ics` download.
- Optional public page: a static Italian/your-language events page generated from the same data, publishable to GitHub Pages. Separate from the private dashboard.
- Status: pipeline **working** for the author's area; place setup, country-based source selection, language packs and the adapter contract are **generalise**; non-Italian cities get AllEvents/Eventbrite + weather only in the first version (honest limit).

## Social media

- Instagram: official Instagram API with Instagram Login (business/creator account, user's own Meta app, token stored locally). Account and media insights, stories twice a day because they expire. Publishing via container API, only after an approval button; collab invites supported; library music, stickers and pins are not possible via API and the docs say so.
- TikTok: read-only statistics from a local analytics database produced by a separate collector (no official analytics API for personal accounts; the reference uses a browser-based collector). Marked as **generalise**, likely to remain optional.
- Content calendar: planned slots with due time, kind (story/post/reel), who prepares it, caption, media link; reminders through the bot; a daily guard warns when nothing is planned.
- Permissions: analysis and publishing are separate toggles. A publish failure stores the platform's message and never retries silently.
- Status: Instagram stats and approved publishing **working**; TikTok **generalise**; a generic "add a platform" interface **planned**.

## AI usage

- Reads local usage data from tools that store it (reference: the Hermes agent's `state.db`, Claude Code and Codex local logs). Shows usage per tool, model, session; provider limits and reset time when exposed; data freshness.
- Three numbers kept apart: real API spend (from provider billing), subscription fee, and list-price equivalent of subscription usage. Unknown price is shown as unknown.
- Advice: rule-based, no model call. Examples: long sessions, background jobs that ran during limits, model choice vs task size. Each advice shows its evidence and is labelled as an estimate; settings are never changed automatically.
- Status: Hermes reader **working**; other readers and the limits panel **generalise**; advice engine partly **working**, generic rule set **planned**.

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
