# Agent hooks: Claude Code, Codex, Antigravity

Kokpit's brain context reaches an agent through `kokpit brain hook`. The agent
runs the command on `SessionStart` and `UserPromptSubmit`, sends its hook JSON
on stdin and gets the read-only context block back in the envelope it
expects. The full contract is in `docs/brain-protocol.md` (section 2,
"Context hook" and "Connecting an agent").

## Set up

```
kokpit brain agents init                      # into the vault from kokpit.toml
kokpit brain agents init --target ~/my-project --harness claude
kokpit brain agents init --dry-run            # report only
```

| File written | For | What it does |
|---|---|---|
| `AGENTS.md` | every agent | how to behave in the vault (load, update never rewrite, session close, receipt) |
| `CLAUDE.md` | Claude Code | `@AGENTS.md`: Claude Code reads the shared protocol |
| `.claude/settings.json` | Claude Code | hooks on `SessionStart` and `UserPromptSubmit` |
| `.codex/hooks.json` | Codex | the same two hooks |
| `.codex/config.toml` | Codex | `[features] hooks = true`; without it Codex ignores the hooks |
| `.agents/hooks.json` | Antigravity | one `PreInvocation` hook (`--event SessionStart`) |

Existing files are never overwritten. If you already have one of the settings
files, it is listed as `skipped` and the filled snippet is printed: merge it by
hand. The templates themselves live in `kokpit/modules/brain/agents/`.

## Check it

```
echo '{"hook_event_name":"SessionStart"}' | kokpit brain hook --harness claude
echo '{"hook_event_name":"UserPromptSubmit","prompt":"garden timer"}' | kokpit brain hook --harness codex
echo '{}' | kokpit brain hook --harness claude --event PostToolUse    # prints nothing
```

The first two print one line of JSON. If something is wrong (no config, no
vault), stdout stays empty and stderr says why in one line; the exit code is
always 0 so the agent keeps working.

## Boundaries

- Read-only: the hook writes no files, calls no model, uses no network.
- Only `hook_event_name` and `prompt` are read from the payload.
- Writing the session card, Threads update and receipt is the agent's job,
  following `AGENTS.md`.
- Antigravity: the file shape follows a working instance's config, not
  Antigravity documentation.
- Windows: the commands use POSIX quoting; adapt the command path by hand
  (not tested).
