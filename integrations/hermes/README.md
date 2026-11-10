# Hermes integration (optional)

Kokpit does not depend on the [Hermes agent](https://hermes-agent.nousresearch.com/). If you run Hermes, this directory holds the pieces that connect the two. Nothing here is required for any module.

## Quick start

```
python -m kokpit hermes status     # what is connected, and the exact fix for each missing piece
python -m kokpit hermes setup      # asks y/N per step; --dry-run shows the changes, --yes says yes to all
hermes plugins enable kokpit
```

`setup` sets `[model] provider = "hermes"`, switches on the Hermes usage reader, links the `kokpit` plugin into `~/.hermes/plugins` (only after a yes, never over an existing path) and prints the lines that point the plugin at your `kokpit.toml`. It does not edit Hermes's own files. If `hermes` is not installed it says so, points to https://hermes-agent.nousresearch.com/docs and changes nothing. Background for each piece: [docs/connections.md, Hermes](../../docs/connections.md#hermes-off-by-default).

## What the integration adds

| Direction | What | Module |
|---|---|---|
| Hermes -> Kokpit | Hermes writes to the same Markdown vault (threads, to-do, session logs); Kokpit renders it | Brain |
| Hermes -> Kokpit | A session card and a receipt at session end, written by the `kokpit` plugin without a model | Brain |
| Hermes -> Kokpit | Kokpit reads `state.db` usage rows (tool, model, tokens per session) | AI usage |
| Kokpit -> Hermes | The `kokpit` plugin: tools for today, brain, events, jobs, AI usage and job health, quick capture, `/kokpit`, brain context on every turn | all |
| Kokpit -> Hermes | Model calls via `hermes chat --oneshot` when `[model] provider = "hermes"`; Hermes's connected accounts answer, no API key in Kokpit | all model uses |

## What exists today

```
integrations/hermes/
  README.md                this file
  kokpit/                  Hermes plugin "kokpit" 0.2.1 (tools, /kokpit, hooks, demo data)
  kokpit-brain/            older context-only plugin, superseded by kokpit/
```

### kokpit plugin

Standard library only; it reads `kokpit.toml`, the SQLite database (read-only)
and the vault, and writes only through `kokpit_capture` and the session card.
Without a Kokpit config it runs on bundled synthetic data (demo mode), so it can
be tried with one link. Full description, settings and the install model
decision: [docs/hermes-plugin.md](../../docs/hermes-plugin.md).

### kokpit-brain plugin (superseded)

Context only (`pre_llm_call`), and it imports the `kokpit` package
(`pip install -e /path/to/kokpit` or `KOKPIT_SRC`). It stays for existing
links. If you move to `kokpit`, disable and remove the old link so the context
block is not added twice:

```
hermes plugins disable kokpit-brain && rm ~/.hermes/plugins/kokpit-brain
```

The same context block is available from the shell for any other tool:

```
kokpit context "what is open on the garden timer"
```

## Relation to the brain protocol

Hermes is one of several agents that follow `docs/brain-protocol.md`. Claude
Code, Codex, Antigravity or any tool with a per-message hook gets the same
context through `kokpit brain hook` (see `integrations/agents/README.md`) and
writes receipts with `kokpit brain receipt`. The Hermes plugin writes the same
card and receipt shapes, so every agent continues from the same vault. Only the
`state.db` usage reader (in `kokpit/modules/ai_usage`) and the plugin are
Hermes-only.

## Boundaries

- Kokpit opens `state.db` read-only and never modifies Hermes configuration.
- The plugin never writes the Kokpit database. By default it makes no network or model calls; the opt-in news briefing fetches only the feed URLs you configured, and the host model is called only with `night_model` or `briefing_model` on.
- The vault conventions are a contract, not a dependency: a vault maintained by hand or by another tool renders the same.
- If Hermes is not installed, the integration is simply absent from the Connections screen.
