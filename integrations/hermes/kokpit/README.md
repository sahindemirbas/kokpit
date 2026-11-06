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
- Local first: standard library only, no network, no model by default. The
  vault is only appended to, never edited.

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
```

A session with only chat and lookups writes nothing. A session that captured,
wrote files, accepted a proposal or ran `/kokpit done` gets one card (what was
done, where it lives, what is open) and one receipt of one or two lines that
points at the notes touched.

## What needs the full Kokpit app

The vault features above work with the plugin alone. Calendar and weather,
job listings, events, news and podcast briefings, AI usage and job health come
from the [Kokpit app](../../../README.md) database; the plugin reads it
read-only when you point `db_path` at it, and says so plainly when it is not
there.

## Privacy

Nothing leaves your machine. The plugin makes no network calls and no model
calls unless you turn on `night_model`. It opens the Kokpit database read-only.
It writes to the vault only through capture, `/kokpit setup` (new files only),
accepted night proposals, the session card and receipts. It never edits, moves
or deletes your notes, and refuses receipt text that looks like a key.

Settings, vault layout, tools, hooks, the dashboard tab and the desktop pane:
[docs/hermes-plugin.md](../../../docs/hermes-plugin.md). MIT license.
