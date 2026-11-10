# kokpit: your personal cockpit for Hermes

One plugin for your own day and your own notes, instead of five single-purpose
ones. Kokpit keeps a plain Markdown vault, captures into it from any Hermes
chat, leaves a short card and receipt when a session produced something, and
proposes next steps at night with the exact lines they came from.

Why this one:

- One personal cockpit instead of many single-purpose plugins: setup, capture,
  morning brief, night pass, brain context, dashboard tab and desktop pane share
  one vault and one set of settings.
- Cross-agent continuity: a session card in `Last-Session.md` and a short
  receipt in `receipts/`, plain Markdown that Claude Code, Codex and Hermes all
  read. The next session starts from the open items and the latest result.
- A night pass you can check: every proposal (todo, next step, link, idea, news
  match) quotes its sources word for word, and the quote is verified against
  the file before it is stored.
- Capture from any Hermes gateway, Telegram included: `/kokpit note`, `todo`,
  `j` append to your vault from your phone. No extra bot.
- Local first: standard library only. By default no network and no model.
  Opt-in news briefing fetches only the feeds you list; opt-in job listings
  search one source you switch on. The vault is only appended to, never edited.

## Install

```
hermes plugins install sahindemirbas/kokpit#integrations/hermes/kokpit --enable
```

## 60-second quick start

```
hermes chat
/kokpit today                       # demo data first: a synthetic vault, nothing of yours
/kokpit setup                       # your own vault in ~/Kokpit (or /kokpit setup ~/Notes)
/kokpit note Ask about the hedge trimmer
/kokpit done Decided to water at 06:00, note saved
                                    # next session: the brain block shows that card and receipt
/kokpit night run                   # proposals with quoted sources; /kokpit add <id> to accept
/kokpit brief                       # the morning brief
/kokpit news on                     # optional: your own news briefing
/kokpit news add AI https://hnrss.org/frontpage llm
/kokpit news run                    # fetches only the feeds you added
```

A session with only chat and lookups writes nothing. A session that captured,
wrote files, accepted a proposal or ran `/kokpit done` gets one card (what was
done, where it lives, what is open) and one receipt of one or two lines that
points at the notes touched.

## What needs the full Kokpit app

The vault features above work with the plugin alone, and so do the opt-in
news briefing, the opt-in job listings and the opt-in podcast of the briefing.
Calendar and weather, events, AI usage and the app's job health come from the
[Kokpit app](../../../README.md) database; the plugin reads it read-only when
you point `db_path` at it, and says so plainly when it is not there.

## News briefing

Off by default. `/kokpit news on`, then `/kokpit news add <topic> <feed-url>
[keywords...]` per topic (http and https feeds only), then `/kokpit news run`
or schedule `hermes kokpit-collect` (crontab: `15 6 * * * hermes kokpit-collect
>/dev/null 2>&1`). Under `plugins.isolation: host` that CLI command does not
exist (`hermes plugins show kokpit` says `Runs in: plugin host`); turn on
`collect_auto` instead (collects in the background at session start when the
last run is older than 20 hours; off by default) or use an agent-mode cron job
that calls the `kokpit_briefing` tool with `action: collect`. `/kokpit news` and the `kokpit_briefing` tool list the day's
items with one headline per topic; `/kokpit brief` gets a short News block.
Summaries are the feed's own first sentences unless you turn on
`briefing_model` (one bounded host model call per collection, output checked).
Items are stored in `collector.db` in the plugin data dir, pruned after 30
days; delete that file to remove them. When the Kokpit app database has items
for the day, those are shown instead. Details:
[docs/hermes-plugin.md](../../../docs/hermes-plugin.md#news-briefing).

## Job listings and market

Off by default, and off twice: the `jobs` module and its only source,
`jobs_linkedin`.

```
/kokpit jobs on
/kokpit jobs profile set target_roles Data Analyst, BI Developer
/kokpit jobs profile set locations Example City, Example Country; Other City
/kokpit jobs profile set languages English:C1, Italian:B1
/kokpit jobs linkedin on            # read the risk statement below first
/kokpit jobs run                    # search now; /kokpit jobs to see the top listings
/kokpit jobs market                 # companies, weekly trend, skills asked for
```

List fields are comma separated, but `locations` is separated by `;` (or a
new line) so that a comma stays inside one location: `Milan, Italy; Turin` is
two locations.

The source is LinkedIn's public guest job search: logged-out pages, no login.
LinkedIn's User Agreement restricts automated access, so use is at your own
risk; it can break or be blocked at any time, and the plugin does not work
around a block. It keeps the app's politeness rules: one request per page, a
pause between requests, the last 7 days only, at most 6 searches of 3 pages,
and the first HTTP 429 or 999 stops the whole run with a warning. No retries,
no proxy, no cookies. Without target roles nothing is fetched.

Profile: a JSON file `profile.json` in the plugin data dir, not in Hermes
config. `/kokpit jobs profile` shows it, `profile set <field> <values...>`
edits one field. `/kokpit jobs profile cv <path>` reads a `.txt` or `.md` CV
(`.pdf` only when `pdftotext` is installed) and proposes skills and languages
with rules, no model; it saves them only when you repeat the command with
`apply`. The CV text is never stored, logged, sent to a model or sent over the
network, and e-mail addresses, phone numbers and links are removed before
anything is shown.

Listings are scored against the profile with reasons; a listing that cannot be
judged shows `unknown`, never 0. Parts that cannot be judged are left out of the
maximum, and when fewer than 50 of the 100 points could be judged the score is
scaled to 60 percent of itself, so the order is kept: a listing where role and
location match (raw 100 with 36 points judged) shows `[60, judged 36/100]`, the same
role in another city (83) shows `[50, judged 36/100]`. Guest search listings
have no description, so most scores stay scaled until a description source
exists.
A profile location matches when its city (the part before the first comma) is
found in the listing location; same-named cities in different countries are
not told apart. A listing not seen for `jobs_close_days`
(default 3) is marked closed, but only after a run in which the source returned
results. They live in `collector.db` next to the news items; delete that file
(and `profile.json`) to remove everything, `/kokpit jobs off` to stop. When the
Kokpit app database has job listings, those are shown instead. Schedule: the
same as the news briefing (`collect_auto`, or an agent-mode cron job calling
`kokpit_jobs` with `action: collect`). Details:
[docs/hermes-plugin.md](../../../docs/hermes-plugin.md#job-listings-and-market).

## Podcast

Off by default. It turns the day's briefing items into audio, only when you
ask: `/kokpit podcast on`, then `/kokpit podcast make` (`collect_auto` never
makes one). `/kokpit podcast script` shows the script as text, `/kokpit
podcast` the status. The script is built by rule from the stored titles and
summaries (no model, no network); `podcast_model` lets the host model write a
two-speaker script instead, one bounded call per topic. Pick the engine with
`/kokpit podcast engine tts|notebooklm`; there is no fallback between them.

- `tts` (default): the host's own `text_to_speech` tool with your Hermes TTS
  voice and provider; the text goes wherever that provider sends it. One voice
  reads everything, speaker labels are not read aloud. Wav pieces are joined in
  Python, other formats with `ffmpeg` when it is installed, otherwise you get
  the parts plus an `episode.m3u` playlist.
- `notebooklm`: always writes a source pack, `source.md` and `focus.txt`. By
  hand: open NotebookLM and create a notebook; add `source.md` as a source;
  open Audio Overview, Customize, paste `focus.txt` and generate. With
  `podcast_notebooklm_cli` on, the plugin runs your own `notebooklm` command
  (the unofficial notebooklm-py project, installed and logged in by you) to do
  those steps and download the audio. It uses your own Google session, is not
  supported by Google and can break at any time: use is at your own risk. The
  plugin does not log in for you, never reads the tool's login files and does
  not retry around a failure.

Files live in `podcast/<YYYY-MM-DD>/` in the plugin data dir, never in the
vault; folders older than `podcast_keep_days` (default 14) are removed at the
next make. Delete the `podcast/` folder to remove everything. Details:
[docs/hermes-plugin.md](../../../docs/hermes-plugin.md#podcast).

## Privacy

By default nothing leaves your machine: no network calls and no model calls.
Four opt-ins change that, and only as far as you set them: the news briefing
fetches only the feed URLs you configured, the job search (`jobs` plus
`jobs_linkedin`) fetches LinkedIn's public guest search pages for your target
roles, `night_model`, `briefing_model` or `podcast_model` call the host model
you already use in Hermes, and a podcast `make` hands the script to the host's
`text_to_speech` tool (engine `tts`) or to your own `notebooklm` command
(engine `notebooklm` with `podcast_notebooklm_cli` on). It opens the Kokpit
database read-only and keeps collected news, job listings and podcast runs in
its own `collector.db`.
It writes to the vault only through capture, `/kokpit setup` (new files only),
accepted night proposals, the session card and receipts. It never edits, moves
or deletes your notes, and refuses receipt text that looks like a key.

Off by default: `night_model` sends vault excerpts to the host model you
already use in Hermes; `briefing_model` sends feed titles and short feed
excerpts (never vault text). The `server` module reads disk, load, memory and
temperature from the local system and runs `systemctl --user is-active <unit>`
for the units you list in `server_units` (names are validated, no shell).

Settings, vault layout, tools, hooks, the dashboard tab and the desktop pane:
[docs/hermes-plugin.md](../../../docs/hermes-plugin.md). MIT license.
