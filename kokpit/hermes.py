"""Connect Kokpit and a local Hermes agent: ``kokpit hermes status`` and ``kokpit hermes setup``.

Four pieces, each optional:
  model    [model] provider = "hermes": model calls run ``hermes chat --oneshot``, no API key in Kokpit
  usage    the ai_usage reader "hermes": token counts from Hermes's state.db, opened read-only
  plugin   integrations/hermes/kokpit-brain linked into ~/.hermes/plugins: brain context on every Hermes turn
  vault    the Markdown folder both sides share (Brain tab, ``kokpit brain receipt``)

``status`` reads only: no network, no writes. ``setup`` asks before each change, writes kokpit.toml through the
same helpers as the setup wizard, and creates the plugin link only after an explicit yes. It never edits Hermes's
own config and never replaces something that already exists at the link path.
"""
from __future__ import annotations

import shutil
from pathlib import Path

from . import tomledit, wizard
from .config import Config

INSTALL_URL = "https://hermes-agent.nousresearch.com/docs"
PLUGIN = "kokpit-brain"


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def plugin_source() -> Path:
    return repo_root() / "integrations" / "hermes" / PLUGIN


def plugin_link() -> Path:
    return Path.home() / ".hermes" / "plugins" / PLUGIN


def hermes_bin(cfg: Config) -> str:
    return cfg.model.get("hermes_bin") or "hermes"


def hermes_path(cfg: Config) -> str:
    return shutil.which(hermes_bin(cfg)) or ""


def state_db(cfg: Config) -> Path:
    return cfg.resolve(cfg.ai_usage["hermes_db"])


def plugin_state() -> tuple[str, str]:
    """('ok' | 'missing' | 'other' | 'no-source', detail)."""
    src, link = plugin_source(), plugin_link()
    if not src.is_dir():
        return "no-source", str(src)
    if not (link.exists() or link.is_symlink()):
        return "missing", ""
    try:
        same = link.resolve() == src.resolve()
    except OSError:
        same = False
    if same:
        return "ok", ""
    target = str(link.readlink()) if link.is_symlink() else "a folder or file that is not this repo's plugin"
    return "other", target


def env_lines(cfg: Config) -> list[str]:
    return [f"KOKPIT_CONFIG={cfg.path}", f"KOKPIT_SRC={repo_root()}"]


def status(cfg: Config) -> list[dict]:
    """One dict per piece: id, ok, what (short), fix (exact command or toml lines, empty when ok)."""
    out = []
    path = hermes_path(cfg)
    out.append({"id": "command", "ok": bool(path),
                "what": f"hermes command: {path}" if path else f"hermes command '{hermes_bin(cfg)}' not found on PATH",
                "fix": "" if path else f"Install Hermes: {INSTALL_URL} (or set [model] hermes_bin = \"/full/path/to/hermes\")"})
    db = state_db(cfg)
    out.append({"id": "state_db", "ok": db.is_file(), "what": f"Hermes usage database: {db}" + ("" if db.is_file() else " (not found)"),
                "fix": "" if db.is_file() else "Run Hermes once so it creates the file, or set [ai_usage] hermes_db = \"/path/to/state.db\""})
    m = cfg.model
    model_ok = bool(m.get("enabled")) and m.get("provider") == "hermes"
    out.append({"id": "model", "ok": model_ok,
                "what": "model calls go through Hermes" if model_ok else f"model provider: {m.get('provider') if m.get('enabled') else 'off'}",
                "fix": "" if model_ok else "python -m kokpit hermes setup   (or in kokpit.toml: [model] enabled = true, provider = \"hermes\", timeout = 120)"})
    usage_ok = bool(cfg.modules.get("ai_usage")) and "hermes" in cfg.ai_usage["readers"]
    out.append({"id": "usage", "ok": usage_ok,
                "what": "AI usage reads Hermes's state.db (read-only)" if usage_ok else "AI usage reader for Hermes is off",
                "fix": "" if usage_ok else "python -m kokpit hermes setup   (or in kokpit.toml: [modules] ai_usage = true and [ai_usage] readers = [\"hermes\"])"})
    st, detail = plugin_state()
    src, link = plugin_source(), plugin_link()
    fix = {"ok": "",
           "missing": f"python -m kokpit hermes setup   (or: mkdir -p {link.parent} && ln -s {src} {link})",
           "other": f"{link} already exists and points to {detail}. Kokpit does not replace it. Remove or rename it yourself, then run python -m kokpit hermes setup",
           "no-source": f"The plugin folder {detail} is missing. Use a git checkout of Kokpit (the plugin is not part of a plain pip install)."}[st]
    out.append({"id": "plugin", "ok": st == "ok",
                "what": f"kokpit-brain plugin: {link}" + {"ok": "", "missing": " (not linked)", "other": " (points elsewhere)", "no-source": " (no source)"}[st],
                "fix": fix})
    v = cfg.vault
    out.append({"id": "vault", "ok": v.is_dir(), "what": f"shared vault: {v}" + ("" if v.is_dir() else " (folder not found)"),
                "fix": "" if v.is_dir() else "Set [brain] vault_path = \"/path/to/your/notes\" in kokpit.toml (a starter structure is made if the folder is empty)"})
    return out


def status_text(cfg: Config) -> str:
    rows = status(cfg)
    lines = [f"config: {cfg.path}"]
    for r in rows:
        lines.append(f"{'ok     ' if r['ok'] else 'missing'}  {r['what']}")
        if r["fix"]:
            lines.append(f"         fix: {r['fix']}")
    lines.append("Hermes needs these two lines in the environment it starts with (for example ~/.hermes/.env) for the plugin:")
    lines += ["         " + x for x in env_lines(cfg)]
    return "\n".join(lines)


def _set_usage(cfg: Config) -> None:
    text = cfg.path.read_text(encoding="utf-8")
    text = tomledit.set_value(text, "modules", "ai_usage", True)
    readers = list(cfg.ai_usage["readers"])
    if "hermes" not in readers:
        readers.append("hermes")
    text = tomledit.set_value(text, "ai_usage", "readers", readers)
    tomledit.write_atomic(cfg.path, text)
    wizard.reload(cfg)


def setup(cfg: Config, ask=input, say=print, yes: bool = False, dry_run: bool = False) -> int:
    """Interactive. Returns 0 when done (some steps may be skipped), 1 when Hermes is not installed."""
    if not hermes_path(cfg):
        say(f"Hermes is not installed (command '{hermes_bin(cfg)}' not found). Install it first: {INSTALL_URL}")
        say("Nothing was changed. If Hermes is installed under another name, set [model] hermes_bin = \"/full/path/to/hermes\" in kokpit.toml.")
        return 1

    def ok(question: str) -> bool:
        if dry_run:
            say(f"would ask: {question} [y/N]")
            return True
        if yes:
            say(f"{question} yes (--yes)")
            return True
        return ask(f"{question} [y/N] ").strip().lower() in ("y", "yes")

    pre = "would " if dry_run else ""
    by = {r["id"]: r for r in status(cfg)}
    say(f"Hermes found: {hermes_path(cfg)}. Config: {cfg.path}")

    if by["model"]["ok"]:
        say("1. Model calls already go through Hermes.")
    elif ok("1. Send Kokpit's model calls (summaries, translations) through Hermes? No API key is stored in Kokpit."):
        if not dry_run:
            wizard.apply(cfg, {"model": {"provider": "hermes", "model": cfg.model.get("model", "")}})
        say(f"   {pre}set [model] enabled = true, provider = \"hermes\", timeout at least 120. Uses under [model.uses] stay as they are.")

    if by["usage"]["ok"]:
        say("2. The AI usage reader for Hermes is already on.")
    elif ok("2. Read Hermes token counts from state.db for the AI usage tab (read-only, nothing leaves this machine)?"):
        if not dry_run:
            _set_usage(cfg)
        say(f"   {pre}set [modules] ai_usage = true and add \"hermes\" to [ai_usage] readers.")
        if not by["state_db"]["ok"]:
            say(f"   note: {state_db(cfg)} does not exist yet. {by['state_db']['fix']}.")

    st, detail = plugin_state()
    src, link = plugin_source(), plugin_link()
    if st == "ok":
        say("3. The kokpit-brain plugin is already linked.")
    elif st == "other":
        say(f"3. {link} already exists and points to {detail}. It is left alone. Remove or rename it yourself if you want this repo's plugin, then run this again.")
    elif st == "no-source":
        say(f"3. Skipped: the plugin folder {detail} is missing (needs a git checkout of Kokpit).")
    elif ok(f"3. Link the kokpit-brain plugin into Hermes ({link} -> {src})? It gives Hermes read-only brain context on every turn."):
        if not dry_run:
            link.parent.mkdir(parents=True, exist_ok=True)
            link.symlink_to(src, target_is_directory=True)
        say(f"   {pre}create the link {link}.")

    say("4. For the plugin, add these lines to the environment Hermes starts with (for example ~/.hermes/.env). Kokpit does not edit Hermes's files:")
    for x in env_lines(cfg):
        say("   " + x)
    if not by["vault"]["ok"]:
        say(f"   The shared vault folder {cfg.vault} does not exist yet. {by['vault']['fix']}.")
    say("Check the result with: python -m kokpit hermes status")
    return 0
