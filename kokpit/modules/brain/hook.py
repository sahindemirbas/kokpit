"""`kokpit brain hook`: the agent-independent context hook.

An agent (Claude Code, Codex, Antigravity, or anything that runs a command and
reads its stdout) starts this as a separate process, sends a JSON payload on
stdin and gets the read-only brain context back in the envelope it expects.

Read-only: no model, no network, no file writes. It never breaks the agent:
whatever goes wrong, stdout stays empty, one line goes to stderr and the exit
code is 0. Only `hook_event_name` and `prompt` are read from the payload.

Imports are kept to the config and brain modules so a call stays well inside
an agent's hook timeout.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

HARNESSES = ("claude", "codex", "antigravity", "generic")
MODES = ("turn", "session")
CONTEXT_EVENTS = ("SessionStart", "UserPromptSubmit")
MAX_STDIN = 1_000_000


class HookError(Exception):
    """A reason to stay silent; reported as one stderr line."""


def read_payload(raw: bytes | str) -> dict:
    """Parse the hook payload. Empty input is `{}`; anything but a JSON object raises HookError."""
    if isinstance(raw, bytes):
        raw = raw[:MAX_STDIN].decode("utf-8-sig", errors="replace")
    text = raw.lstrip("\ufeff").strip()
    if not text:
        return {}
    try:
        data = json.loads(text)
    except ValueError:
        raise HookError("stdin is not valid JSON (or over 1 MB); nothing injected") from None
    if not isinstance(data, dict):
        raise HookError("stdin is not a JSON object; nothing injected")
    return data


def event_name(payload: dict, event: str | None = None) -> str:
    """`--event` wins when given (Antigravity sends no event name), else the payload's hook_event_name."""
    if event:
        return event
    name = payload.get("hook_event_name")
    return name if isinstance(name, str) else ""


def prompt_of(payload: dict) -> str:
    prompt = payload.get("prompt")
    return prompt if isinstance(prompt, str) else ""


def wants_context(event: str, prompt: str, mode: str = "turn") -> bool:
    """SessionStart always; UserPromptSubmit only in turn mode and with a non-empty prompt; nothing else."""
    if event == "SessionStart":
        return True
    if event == "UserPromptSubmit":
        return mode == "turn" and bool(prompt.strip())
    return False


def envelope(harness: str, event: str, text: str) -> str:
    """The stdout each harness expects. JSON is ASCII-escaped so a non-UTF-8 terminal locale cannot break it."""
    if harness in ("claude", "codex"):
        return json.dumps({"hookSpecificOutput": {"hookEventName": event, "additionalContext": text}})
    if harness == "antigravity":
        return json.dumps({"injectSteps": [{"ephemeralMessage": text}]})
    return text


def resolve_vault(config: str | None = None) -> Path:
    """KOKPIT_VAULT first (no config needed), then --config / KOKPIT_CONFIG / the default locations."""
    env = os.environ.get("KOKPIT_VAULT")
    if env:
        vault = Path(env).expanduser()
    else:
        from ...config import find_config, load
        path = find_config(config)
        if path is None or not Path(path).exists():
            raise HookError("no kokpit.toml found and KOKPIT_VAULT is not set")
        vault = load(path).vault
    if not vault.is_dir():
        raise HookError(f"vault folder not found: {vault}")
    return vault


def max_chars_from(value: int | None) -> int | None:
    if value is not None:
        return value
    env = os.environ.get("KOKPIT_CONTEXT_MAX_CHARS")
    if env:
        try:
            return int(env)
        except ValueError:
            raise HookError("KOKPIT_CONTEXT_MAX_CHARS is not a number") from None
    return None


def run(raw: bytes | str, harness: str, event: str | None = None, mode: str = "turn",
        max_chars: int | None = None, config: str | None = None) -> str:
    """Return what goes to stdout ('' for nothing). Raises HookError for a reportable reason."""
    if harness not in HARNESSES:
        raise HookError(f"unknown --harness '{harness}' (use {', '.join(HARNESSES)})")
    if mode not in MODES:
        raise HookError(f"unknown --mode '{mode}' (use turn or session)")
    payload = read_payload(raw)
    name = event_name(payload, event)
    if not name:
        raise HookError("no event name (payload hook_event_name or --event)")
    prompt = prompt_of(payload) if name == "UserPromptSubmit" else ""
    if not wants_context(name, prompt, mode):
        return ""
    limit = max_chars_from(max_chars)
    vault = resolve_vault(config)
    from .context import DEFAULT_MAX_CHARS, build_context
    text = build_context(vault, prompt, max_chars=limit if limit is not None else DEFAULT_MAX_CHARS)
    return envelope(harness, name, text) if text else ""


def main(stdin, stdout, stderr, harness: str, event: str | None = None, mode: str = "turn",
         max_chars: int | None = None, config: str | None = None) -> int:
    """CLI entry: always exit 0. stdin/stdout are binary-capable text streams (sys.stdin, sys.stdout)."""
    try:
        reader = getattr(stdin, "buffer", stdin)
        out = run(reader.read(MAX_STDIN + 1), harness, event, mode, max_chars, config)
    except HookError as exc:
        stderr.write(f"kokpit hook: {exc}\n")
        return 0
    except Exception as exc:  # never break the agent's turn
        stderr.write(f"kokpit hook: {exc.__class__.__name__}: {exc}\n".replace("\n", " ").rstrip() + "\n")
        return 0
    if out:
        data = (out + "\n").encode("utf-8")
        writer = getattr(stdout, "buffer", None)
        if writer is not None:
            stdout.flush()
            writer.write(data)
            writer.flush()
        else:
            stdout.write(out + "\n")
    return 0
