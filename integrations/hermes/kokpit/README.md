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
  Opt-in news briefing fetches only the feeds you list. The vault is only
  appended to, never edited.

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

The vault features above work with the plugin alone, and so does the opt-in
news briefing. Calendar and weather, job listings, events, the podcast, AI
usage and the app's job health come from the [Kokpit app](../../../README.md)
database; the plugin reads it read-only when you point `db_path` at it, and
says so plainly when it is not there. The plugin does not collect job listings
or a podcast.

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

## Privacy

By default nothing leaves your machine: no network calls and no model calls.
Two opt-ins change that, and only as far as you set them: the news briefing
fetches only the feed URLs you configured, and `night_model` or
`briefing_model` call the host model you already use in Hermes. It opens the
Kokpit database read-only and keeps collected news in its own `collector.db`.
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
