# Kokpit plugin for Hermes Agent

`integrations/hermes/kokpit/` is a Hermes Agent plugin (name `kokpit`, version
0.2.1). It gives Hermes eight Kokpit tools plus two optional ones, a `/kokpit`
command, the brain context on every turn, a session card plus receipt in the
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
| `kokpit_capture` | Appends a note to `inbox/` (new file) or a task to `To-Do.md` under `## Open`. The only tool that writes |
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

Command: `/kokpit today | brain [words] | health | help`. Short plain text, so it
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
- `on_session_finalize`: writes one card at the top of `Last-Session.md` and one
  receipt in `receipts/` (format below), then tells the user where. Built from
  rules, no model: harness, turn count, tools used, files written, captures,
  and up to three open items from `Threads.md`. The title is the first user
  message, cut short, or "Hermes session" if it looks like it holds a key.
  A session with no turns writes nothing; `session_card: false` turns it off.

## Limits

- The card says what happened, not why. Edit it, or let the agent write a
  better card through the protocol; the plugin replaces only its own card
  (same session id) and never touches other cards.
- Search is word matching over Markdown, not semantic search.
- Calendar and weather are what Kokpit's last `calendar` and `weather` runs
  stored. Older than 24 hours is marked `stale`.
- The plugin does not run Kokpit jobs. `kokpit_health` tells you which one to run.
- `kokpit_training` understands flat JSONL or CSV rows. TensorBoard event files
  and nested JSON are not read; log a flat line per step.
- `kokpit_server` reports this machine only and checks user units only, not
  system units.
