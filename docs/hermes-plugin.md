# Kokpit plugin for Hermes Agent

`integrations/hermes/kokpit/` is a Hermes Agent plugin (name `kokpit`, version
0.2.0). It gives Hermes eight Kokpit tools, a `/kokpit` command, the brain
context on every turn, and a session card plus receipt in the vault when a
session ends. Claude Code, Codex and any other agent that follows
[brain-protocol.md](brain-protocol.md) continue from the same vault.

It replaces the older `integrations/hermes/kokpit-brain/` plugin (context only).
That folder stays for existing links; do not enable both, each adds a context
block. `python -m kokpit hermes status` points out an old link.

## Try it in one step (demo mode)

With no Kokpit config and no vault, the plugin runs on bundled synthetic data:
a small vault (`demo/vault/`, same shape as `docs/demo-vault/`) and a SQLite
database built from `demo/seed.json`. Dates are relative, so the demo always
shows "today", "tomorrow", an overdue task and a failed job. Every answer says
`"mode": "demo"`.

```
ln -s /path/to/kokpit/integrations/hermes/kokpit ~/.hermes/plugins/kokpit
hermes plugins enable kokpit
hermes chat      # then: /kokpit today, or "what is open in my brain?"
```

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
| `kokpit_health` | Last run per job, failures in a row, runs with 0 records, expected jobs that never ran |

A module that is off in `[modules]` answers
`module disabled, enable with [modules] <name> = true in kokpit.toml`.

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
