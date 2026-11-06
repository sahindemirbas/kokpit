# Kokpit plugin for Hermes Agent

`integrations/hermes/kokpit/` is a Hermes Agent plugin (name `kokpit`, version
0.3.0). It gives Hermes nine Kokpit tools plus two optional ones, a `/kokpit`
command that captures notes, tasks and journal lines from any Hermes chat
(Telegram included), a rule-based night pass that proposes todos, next steps,
links, ideas and news matches with quoted sources, the brain context on every turn, a session card plus receipt in the
vault when a session ends, a Kokpit tab in the web dashboard and a right pane
with a status chip in the desktop app. Claude Code, Codex and any other agent
that follows [brain-protocol.md](brain-protocol.md) continue from the same vault.

It replaces the older `integrations/hermes/kokpit-brain/` plugin (context only).
That folder stays for existing links; do not enable both, each adds a context
block. `python -m kokpit hermes status` points out an old link.

## Try it in one minute (demo mode)

With no Kokpit config and no vault, the plugin runs on bundled synthetic data:
a small vault (`demo/vault/`, same shape as `docs/demo-vault/`), a SQLite
database built from `demo/seed.json`, three synthetic training logs and fake
server numbers. Dates are relative, so the demo always shows "today",
"tomorrow", an overdue task, a failed job, a running, a stalled and a finished
training run. Every answer says `"mode": "demo"` and the panels show a
"Demo data" badge.

```
git clone <this repository> kokpit
ln -s "$PWD/kokpit/integrations/hermes/kokpit" ~/.hermes/plugins/kokpit
hermes plugins validate ~/.hermes/plugins/kokpit
hermes plugins enable kokpit
hermes chat      # then: /kokpit today, "what is open in my brain?", "is my training run stuck?"
hermes dashboard # the Kokpit tab sits after Skills
```

In the desktop app the Kokpit pane opens on the right and a chip in the status
bar shows job health and the items on you.

The demo copy lives in Hermes's plugin data folder
(`~/.hermes/plugin-data/kokpit/demo/`). Captures in demo mode go there too.
`demo: off` in the plugin settings turns the demo off; `demo: on` forces it.

## Connect your own Kokpit

```
python -m kokpit hermes setup     # links the plugin (after a yes) and prints the next lines
hermes plugins enable kokpit
```

The plugin finds `kokpit.toml` in this order: the `kokpit_config` setting,
`KOKPIT_CONFIG` in Hermes's environment, `./kokpit.toml`, then
`~/.config/kokpit/kokpit.toml`. Settings live in `~/.hermes/config.yaml`:

```
plugins:
  entries:
    kokpit:
      settings:
        kokpit_config: /path/to/kokpit.toml
        context_max_chars: 1200     # 0 turns the context block off
        session_card: true          # Last-Session card + receipt at session end
        vault_path: ""              # empty: [brain] vault_path from kokpit.toml
        demo: auto                  # auto, on, off
```

One setting is also one command, for example
`hermes config set plugins.entries.kokpit.settings.server true`.

| Setting | Type | Default | Meaning |
|---|---|---|---|
| `kokpit_config` | str | `""` | Path to `kokpit.toml`; empty uses the search order above |
| `vault_path` | str | `""` | Markdown vault; empty: `[brain] vault_path` |
| `db_path` | str | `""` | Kokpit SQLite database (read-only); works without `kokpit.toml` |
| `threads_file` | str | `""` | Threads file, vault-relative or absolute |
| `todo_file` | str | `""` | To-Do file, vault-relative or absolute |
| `last_session_file` | str | `""` | Where session cards go |
| `receipts_dir` | str | `""` | Receipt folder; empty: `receipts/` |
| `inbox_dir` | str | `""` | Capture folder; empty: `inbox/` |
| `context_max_chars` | int | `1200` | Brain block budget per turn, 0 is off |
| `expected` | list | `[]` | Job names (`runs.job`) `kokpit_health` expects, e.g. `["weather", "calendar"]`; empty: `[health] expected` |
| `session_card` | bool | `true` | Card + receipt at session end |
| `demo` | str | `auto` | `auto`, `on`, `off` |
| `training` | bool | `false` | Turns on `kokpit_training` |
| `training_logs` | list | `[]` | Globs of JSONL or CSV training logs |
| `training_stall_minutes` | int | `30` | Minutes without a new step before "may have stopped" |
| `server` | bool | `false` | Turns on `kokpit_server` |
| `server_paths` | list | `["/"]` | Paths for disk usage |
| `server_units` | list | `[]` | `systemd --user` units to check |
| `journal_dir` | str | `daily` | Vault folder for journal lines, one `YYYY-MM-DD.md` per day |
| `news_dir` | str | `news` | Vault folder of Markdown news items, used when the database has no briefing table |
| `stale_days` | int | `7` | Days without change before the night pass proposes a next step for a thread |
| `news_days` | int | `2` | Days back the night pass reads news items, inbox notes and journal lines |
| `night_auto` | bool | `false` | Run the night pass when a session starts, at most once every 20 hours |
| `night_model` | bool | `false` | Also ask the host model (`ctx.llm`) for proposals; every one is checked in code |

A plugin setting wins over the same value in `kokpit.toml`. Without
`kokpit.toml`, `vault_path` plus `db_path` is a full setup.

### Vault layout

Real vaults rarely keep `Threads.md`, `To-Do.md` and `Last-Session.md` at the
root. Point at them directly:

```
      settings:
        vault_path: ~/Notes
        threads_file: Companion/Threads.md
        todo_file: notes/Tasks/To-Do.md
        last_session_file: "🧭 Companion/Last-Session.md"
        receipts_dir: Companion/receipts
```

Left empty, the plugin first looks at the vault root, then searches the vault
by file name (case-insensitive): at most four folders deep, symbolic links are not followed, dot
folders are skipped, and a single match is used. Two or more matches are not
guessed; the tool answers with the paths found and the setting to fill in.
`kokpit_health` lists which file it uses for each role and how it was found
(setting, root, discovered, ambiguous or default) and whether it exists. Task headings may be local:
`## Open`, `## Açık`, `## To do`, `## Aperti` count as open, `## Done`,
`## Bitti`, `## Fatto` as done. Capture appends under the vault's own open
heading.

When a table or column the plugin expects is missing, the tool answers
`unknown / table X missing` or `unknown / table X missing columns: ...` instead
of failing; the dashboard and desktop cards shorten this to "not collected
here" (no table) or "unknown format" (another shape). "never run" means the
table is there but holds no run. Some columns are optional: an older Kokpit
`runs` table without `rows` still gives job health, with `records: null`
(shown as "—", never as 0 records).

In `Threads.md` an owner with an empty value (`Sende: yok`, `on you: none`,
`-`, `n/a`, `nessuno` ...) is not an open item. A note in brackets after the
owner (`Bende (Fri): ...`) is kept as `note` and shown as "Me · Fri"; an ISO
date there also fills `due`.

When a config is set but cannot be used (file missing, TOML error, vault
missing), the plugin does not go quiet: the first tool call returns
`{"ok": false, "error": ..., "fix": ...}` with the exact setting to change.
The hooks never break a turn; they return nothing in that case.

## Install model (decision)

Hermes catalog installs clone the repository at a pinned commit and load one
folder (`subdir: integrations/hermes/kokpit`). Three ways to reach Kokpit's
code were considered:

- Import the `kokpit` package (relative to the clone or through
  `python_dependencies`). Rejected: a catalog install would pull Kokpit and its
  dependencies into Hermes's environment, and the import path would depend on
  where the clone sits. Changing `sys.path` at load time is the kind of host
  change the catalog rules forbid.
- Call the `kokpit` CLI as a subprocess. Rejected: needs Kokpit on `PATH` for
  every user, costs a process per tool call and would break the zero-setup demo.
- Read Kokpit's files directly. Chosen. The plugin is standard library only and
  talks to Kokpit through its documented data: `kokpit.toml`, the SQLite
  database (opened with `mode=ro`, never written) and the Markdown vault.
  `contract.py` holds the few shapes it relies on (table and column names from
  `kokpit/modules/*/schema.sql`, the Last-Session card and receipt format from
  this protocol). `tests/test_hermes_plugin.py` builds the database from the
  same schema, so a schema change that breaks the plugin fails the suite.

No network calls, no model calls, no self-update, no Hermes core patching. Only
Kokpit's own jobs, which the user runs, go to the network.

## What it registers

Tools (toolset `kokpit`), each returns JSON. Success is
`{"ok": true, "mode": "live"|"demo", ...}`, failure is
`{"ok": false, "error": ..., "fix": ...}`. Missing data is reported as
`never run`, `unknown` or `no database yet`, never filled in.

| Tool | What it returns |
|---|---|
| `kokpit_today` | Calendar (today and the next days), weather, open and overdue tasks, upcoming `due:` dates, counts from the other modules, freshness per source |
| `kokpit_brain_search` | Vault notes matching every word, with path, line and snippet |
| `kokpit_threads` | Active threads with open items and owners, items on you, the last session card |
| `kokpit_capture` | Appends a note to `inbox/` (new file), a task to `To-Do.md` under `## Open`, or a journal line `- HH:MM text` to `daily/YYYY-MM-DD.md` (kind `journal`). The only tool that writes on its own |
| `kokpit_night` | Night pass proposals: `action` `list` (default, optional `kind` and `limit`), `run`, `accept` or `dismiss` with an `id`. Accept writes only through capture: a `todo` (or `improve`) becomes a To-Do line, an `idea` a new file in `ideas/` |
| `kokpit_events` | Stored events in the next N days; price, distance and venue say `unknown` when missing |
| `kokpit_jobs` | Open job listings with score and reasons; no score is `unknown` |
| `kokpit_ai_usage` | Tokens per tool and model; cost from `[ai_usage] prices_path`, an unpriced model is `unknown` and makes the total `unknown` |
| `kokpit_health` | Last run per job, failures in a row, runs with 0 records, expected jobs that never ran, and which vault files are used |
| `kokpit_training` | Optional. Per run: last step, last loss and metrics, steps per second, ETA, minutes since the last update, "may have stopped" warning |
| `kokpit_server` | Optional. Disk usage for set paths, CPU temperature, load, memory, uptime, state of the listed `systemd --user` units |

A module that is off in `[modules]` answers
`module disabled, enable with [modules] <name> = true in kokpit.toml`.

### Optional modules: training and server

Both are off by default and turn on with one setting (`training: true`,
`server: true`); while off they answer `module disabled, enable with hermes
config set plugins.entries.kokpit.settings.<name> true`. They only read.

`kokpit_training` reads the files matched by `training_logs`, one run per
file. JSONL rows or CSV lines are understood when they carry a step
(`step`, `global_step`, `iteration`, `iter`, `epoch`) and optionally a time
(`time`, `timestamp`, `ts`, `wall_time`, `datetime`; else the file's
modification time), a total (`total_steps`, `max_steps`, `num_steps`) and a
loss (`loss`, `train_loss`, `train/loss`). Other numeric columns come back as
metrics. A run with no new step for `training_stall_minutes` gets
"may have stopped"; a run at its total is `finished`. Nothing is started or
stopped.

```
        training: true
        training_logs: ["~/runs/*/metrics.jsonl", "~/exp/baseline.csv"]
        training_stall_minutes: 20
```

`kokpit_server` reads `/proc`, `/sys/class/thermal` and disk usage for
`server_paths`. Units in `server_units` are checked with
`systemctl --user is-active -- <unit>`: a list argument, no shell, a 3 second
timeout, and only names that match `[A-Za-z0-9][A-Za-z0-9@_.:-]*`; anything
else is reported as `invalid name` and never run. No temperature sensor, no
`systemctl` or a timeout reads `unknown`. Disks above 90% and temperatures
above 80 °C become warnings.

### Web dashboard tab

`dashboard/` adds a Kokpit tab (after Skills) to `hermes dashboard`: today,
open threads, upcoming dates, job health with coloured freshness, AI usage,
training and server cards when those modules are on, a module on/off list and a
quick capture form. The tab calls the plugin's own routes
(`/overview`, `/tool/<name>` for the read tools, `/capture`), which run the
same `tools.py` functions the agent calls, so the tab shows exactly the tool
JSON. The capture form goes through `kokpit_capture` only.

### Desktop pane and status chip

`desktop/plugin.js` registers a right pane (300 px) and a status bar chip. Both
share one poll of `/overview` every minute. The pane is the compact version of
the dashboard tab; the chip shows the worst job freshness, training or server
warnings and the number of items on you. Colours come only from the app's
`--ui-*` theme variables, so light and dark themes both work.

### Command and hooks

Command: `/kokpit today | brain [words] | note <text> | todo <text> | j <text> |
night [run|kind] | add <id> | skip <id> | health | help`. Short plain text, so it
reads the same in the CLI and in Telegram.

Hooks:

- `pre_llm_call`: the brain block (active threads, last session, upcoming
  dates, related notes), cut to `context_max_chars`. The full block goes on the
  first turn of a session; later turns get only the notes related to the
  message, or nothing. This keeps the token cost small.
- `post_compaction`: marks the session so the next `pre_llm_call` sends the full
  block again (the summary dropped it). The hook itself returns nothing, as the
  Hermes API defines.
- `post_tool_call`: counts tool calls and remembers the paths given to
  `write_file`, `patch` and `kokpit_capture`. Only in memory.
- `pre_llm_call` also adds one line when night proposals are open, inside the
  same budget and only with the full block: `Night: 2 todos, 1 idea, 1 news
  match. /kokpit night`.
- `on_session_start`: with `night_auto: true`, runs the night pass in a
  background thread when it last ran 20 or more hours ago. Off by default.
- `on_session_finalize`: writes one card at the top of `Last-Session.md` and one
  receipt in `receipts/` (format below), then tells the user where. Built from
  rules, no model: harness, turn count, tools used, files written, captures,
  and up to three open items from `Threads.md`. The title is the first user
  message, cut short, or "Hermes session" if it looks like it holds a key.
  A session with no turns writes nothing; `session_card: false` turns it off.

## Capture from anywhere

`/kokpit` is a normal Hermes plugin command, so it works wherever Hermes
answers: the CLI, the desktop app and every gateway. "Text your vault from
Telegram" means exactly this: your own Hermes Telegram bot, with this plugin
enabled, gets `/kokpit j ...` and the plugin appends to your vault. No Kokpit
app, no extra bot and no model are needed.

```
/kokpit note Ask the neighbour about the hedge trimmer
Saved note: inbox/2031-03-04-0712-ask-the-neighbour-about-the-hedge-trimmer.md
/kokpit todo Call the plumber due:2031-03-20
Added to To-Do.md: - [ ] Call the plumber (due: 2031-03-20)
/kokpit j Basil repotted, roots were circling
Added to daily/2031-03-04.md: - 07:14 Basil repotted, roots were circling
```

All three are append-only: a note is a new file, a task is one new line under
`## Open`, a journal line is added at the end of today's file (created with
`# YYYY-MM-DD` when missing). Empty text answers with the usage line; a missing
vault answers with what to set. `[brain] quick_capture = false` in
`kokpit.toml` turns all writes off.

The Kokpit app has its own Telegram bot with the same `/note`, `/todo` and
`/journal` (`/j`) commands, plus an optional `[bot] plain_text = "journal"` mode.
That bot needs the Kokpit app; the plugin command does not.

## Night pass

`/kokpit night run` (or `kokpit_night` with `action: run`) reads the vault and,
when present, the Kokpit database, and stores proposals. It never edits, moves,
renames or deletes a note. Only rules, no model, unless `night_model` is on.

| Kind | Rule |
|---|---|
| `todo` | An open item on you, on me or unowned in `Threads.md` with no matching line in `To-Do.md`; an open task whose `due:` date has passed; the same open task in two or more files; and "Check <title> for <project>" for each news match |
| `improve` | A thread whose date (or its source note) is older than `stale_days`: its first open item, quoted, as the next step. At most 3 |
| `link` | Two notes that share three or more rare words (stopwords removed) and do not link each other. The shared words are listed. At most 5 |
| `idea` | An inbox note, journal line or news item from the last `news_days` days that matches two active projects: "X connects A and B", template wording only. At most 5 |
| `news` | A news item matched to a project. Source: the Kokpit database `briefing_items` table (its stored project tag and reason, read-only) if it has rows, else Markdown files in `news_dir`, matched by thread title words and note aliases. No source: nothing, and the run says "no news source" |

Every proposal has `id` (a hash of kind, key and sources, so a rerun finds the
same ones), `kind`, `text`, `reason`, `sources` (`path` or database `item`, and
a `quote` copied word for word), `created` and `status` (`open`, `done`,
`dismissed`). Before a proposal is stored its quotes are looked up in the files
again; one that is not found is dropped. Done and dismissed proposals never
reopen; open ones whose cause is gone disappear on the next run.

Where they are stored: in the Hermes plugin data dir
(`plugin_data_dir("kokpit")/night/<hash of the vault path>.json`). When that
helper is not available (older Hermes), in `<vault>/.kokpit/night.json`, a
Kokpit-owned file outside the notes.

Review from any chat:

```
/kokpit night          top 5 open, one of each kind first, with short ids
/kokpit night todo     only one kind
/kokpit add 1094a6     accept: todo/improve -> To-Do line, idea -> new file in ideas/, link/news -> done
/kokpit skip 1094a6    dismiss, it will not come back
```

Accept checks again that the proposal is open and its quotes still match, and
never overwrites: an idea whose file name exists gets a number added. The Night
card in the dashboard tab and the desktop pane shows counts by kind and the top
three. If the Kokpit app's own organiser has open proposals in the database,
`/kokpit night` lists them too, read-only, marked "From the Kokpit app
organiser".

Running it every night, three ways:

- `night_auto: true`: at session start, at most once per 20 hours.
- `hermes kokpit-night run` (a CLI command the plugin registers; `list` shows
  the open ones). A crontab line: `30 3 * * * hermes kokpit-night run >/dev/null 2>&1`.
  Hermes wires plugin CLI commands only when plugins run in process
  (`plugins.isolation: in_process`); under `host` isolation use `night_auto`.
- By hand: `/kokpit night run`.

Model path (`night_model: true`, off by default): the plugin calls
`ctx.llm.complete_structured` on your active Hermes model with Threads, project
notes and a few recent notes (at most 10 files, 1500 characters each). A model
proposal is kept only if its kind is `todo`, `improve` or `idea`, its text is
at most 200 characters, its path is one of the files it was given and its quote
is found word for word in that file. The rest are counted ("1 kept, 6 rejected
by the checks") and never stored. A failed call leaves the rule results as
they are.

What needs the Kokpit app: news from `briefing_items` and the organiser list
(both read from its database). Everything else, the demo included, works with
the plugin alone on a plain Markdown vault.

## Limits

- The card says what happened, not why. Edit it, or let the agent write a
  better card through the protocol; the plugin replaces only its own card
  (same session id) and never touches other cards.
- Search is word matching over Markdown, not semantic search.
- The night pass rules are word overlap, not understanding. Proposals are tested
  on synthetic vaults (a 2000 note vault runs in a few seconds in the tests); it
  has not been measured on a real vault yet.
- Calendar and weather are what Kokpit's last `calendar` and `weather` runs
  stored. Older than 24 hours is marked `stale`.
- The plugin does not run Kokpit jobs. `kokpit_health` tells you which one to run.
- `kokpit_training` understands flat JSONL or CSV rows. TensorBoard event files
  and nested JSON are not read; log a flat line per step.
- `kokpit_server` reports this machine only and checks user units only, not
  system units.
