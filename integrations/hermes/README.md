# Hermes integration (optional)

Kokpit does not depend on the [Hermes agent](https://hermes-agent.nousresearch.com/). If you run Hermes, this directory will hold the pieces that connect the two. Nothing here is required for any module.

## What the integration adds

| Direction | What | Module |
|---|---|---|
| Hermes -> Kokpit | Hermes writes to the same Markdown vault (threads, to-do, session logs); Kokpit renders it | Brain |
| Hermes -> Kokpit | Kokpit reads `state.db` usage rows (tool, model, tokens per session) | AI usage |
| Kokpit -> Hermes | A Hermes skill that lets the agent query the dashboard API ("what is on today", "events this weekend", "open tasks") | all |

## Planned contents

```
integrations/hermes/
  README.md                this file
  skill/SKILL.md           Hermes skill: how to call the local Kokpit API
  usage_reader.py          read-only reader for Hermes state.db (schema documented here)
  vault_conventions.md     file and heading conventions both sides agree on
```

## Relation to the brain protocol

Hermes is one of several agents that follow `docs/brain-protocol.md`. The
reference instance runs the same hook script from Hermes, Claude Code, Codex
and Antigravity; nothing here is Hermes-only except the `state.db` usage
reader and the Kokpit skill.

## Boundaries

- Kokpit opens `state.db` read-only and never modifies Hermes configuration.
- The vault conventions are a contract, not a dependency: a vault maintained by hand or by another tool renders the same.
- If Hermes is not installed, the integration is simply absent from the Connections screen.
