# Hermes integration (optional)

Kokpit does not depend on the [Hermes agent](https://hermes-agent.nousresearch.com/). If you run Hermes, this directory will hold the pieces that connect the two. Nothing here is required for any module.

## What the integration adds

| Direction | What | Module |
|---|---|---|
| Hermes -> Kokpit | Hermes writes to the same Markdown vault (threads, to-do, session logs); Kokpit renders it | Brain |
| Hermes -> Kokpit | `kokpit brain receipt` at session close (run by the agent by hand; no hook yet) | Brain |
| Hermes -> Kokpit | Kokpit reads `state.db` usage rows (tool, model, tokens per session) | AI usage |
| Kokpit -> Hermes | A Hermes skill that lets the agent query the dashboard API ("what is on today", "events this weekend", "open tasks") | all |
| Kokpit -> Hermes | Model calls via `hermes chat --oneshot` when `[model] provider = "hermes"`; Hermes's connected accounts answer, no API key in Kokpit | all model uses |

## What exists today

```
integrations/hermes/
  README.md                this file
  kokpit-brain/            Hermes plugin: read-only brain context on every turn
    plugin.yaml
    __init__.py
```

### kokpit-brain plugin

On every user turn the plugin calls `kokpit.modules.brain.context.build_context`
and returns a block Hermes appends to the message: active threads with owners,
the latest Last-Session card, upcoming `due:` dates, and up to three notes whose
title or aliases share words with the message. No model, no network, no writes.
The same block is available from the shell for any other tool:

```
kokpit context "what is open on the garden timer"
```

Install:

```
ln -s /path/to/kokpit/integrations/hermes/kokpit-brain ~/.hermes/plugins/kokpit-brain
pip install -e /path/to/kokpit        # or export KOKPIT_SRC=/path/to/kokpit
export KOKPIT_CONFIG=/path/to/kokpit.toml   # or KOKPIT_VAULT=/path/to/vault
```

`KOKPIT_CONTEXT_MAX_CHARS` (default 3500) caps the block. If Kokpit or the
vault cannot be found the hook returns nothing and Hermes runs as usual.

## Planned contents

```
  skill/SKILL.md           Hermes skill: how to call the local Kokpit API
  usage_reader.py          read-only reader for Hermes state.db (schema documented here)
  vault_conventions.md     file and heading conventions both sides agree on
```

## Relation to the brain protocol

Hermes is one of several agents that follow `docs/brain-protocol.md`. The
Hermes-specific part is only the thin `kokpit-brain/__init__.py` adapter; the
block itself comes from the same `build_context` that `kokpit brain hook`
uses, so Claude Code, Codex, Antigravity or any tool with a per-message hook
gets the same block (see `integrations/agents/README.md`; the plugin itself is
unchanged). Only the planned `state.db`
usage reader and the Kokpit skill are Hermes-only.

## Boundaries

- Kokpit opens `state.db` read-only and never modifies Hermes configuration.
- The vault conventions are a contract, not a dependency: a vault maintained by hand or by another tool renders the same.
- If Hermes is not installed, the integration is simply absent from the Connections screen.
