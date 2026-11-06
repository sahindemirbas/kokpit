"""/kokpit setup: your own vault in one command.

Creates a starter vault (Welcome.md, Threads.md, To-Do.md, Last-Session.md and the folders inbox/,
daily/, news/, notes/, receipts/) or adopts a folder that already is a vault, then stores the
plugin setting vault_path through the host (ctx.set_config). Existing files are never overwritten:
every starter file is created with O_EXCL and skipped when the name is taken.
"""
from __future__ import annotations

import os
from datetime import date
from pathlib import Path

from . import contract as c

STARTER = c.HERE / "starter"
MARKERS = ("Threads.md", "To-Do.md", "Last-Session.md")
FOLDERS = ("inbox", "daily", "news", "notes", "receipts")
DEFAULT = "~/Kokpit"
SET_CMD = "hermes config set plugins.entries.kokpit.settings.vault_path {path}"


def short(p: Path | str) -> str:
    """The path with the home folder shown as ~ (for replies)."""
    s, home = str(p), os.path.expanduser("~")
    return "~" + s[len(home):] if home and (s == home or s.startswith(home + os.sep)) else s


def target(raw: str = "") -> Path:
    """~ is expanded; a relative path is taken from the home folder (a gateway has no useful cwd)."""
    raw = (raw or "").strip().strip("\"'") or DEFAULT
    if c.CONTROL.search(raw) or len(raw) > 1024:
        raise c.NotReady("that path is not usable", f"pass a plain folder path, e.g. /kokpit setup {DEFAULT}")
    p = Path(os.path.expanduser(raw))
    if not p.is_absolute():
        p = Path.home() / p
    return Path(os.path.normpath(p))


def is_vault(p: Path) -> bool:
    return p.is_dir() and any((p / m).is_file() for m in MARKERS)


def create(dest: Path, today: date | None = None) -> dict:
    """-> {path, adopted, created}. Adopts an existing vault without writing; otherwise adds only missing files."""
    dest = Path(dest)
    if dest.exists() and not dest.is_dir():
        raise c.NotReady(f"{short(dest)} is a file, not a folder", f"pick a folder, e.g. /kokpit setup {DEFAULT}")
    if is_vault(dest):
        return {"path": dest, "adopted": True, "created": []}
    today = today or date.today()
    created: list[str] = []
    dest.mkdir(parents=True, exist_ok=True)
    for name in FOLDERS:
        d = dest / name
        if not d.exists():
            d.mkdir()
            created.append(name + "/")
    for src in sorted(STARTER.glob("*.md")):
        text = c.DEMO_DATE.sub(lambda m: date.fromordinal(today.toordinal() + int(m.group(1))).isoformat(),
                               src.read_text(encoding="utf-8"))
        try:
            fd = os.open(dest / src.name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
        except FileExistsError:
            continue  # never overwrite: the user's file wins
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        created.append(src.name)
    return {"path": dest, "adopted": False, "created": created}


def persist(env: c.Env, path: Path) -> dict:
    """Use the vault now (in this process) and store it for the next start when the host allows it."""
    env.override("vault_path", str(path))
    env.override("demo", "off")
    cmd = SET_CMD.format(path=short(path))
    if env.set_config is None:
        return {"saved": False, "reason": "this Hermes has no plugin settings writer", "command": cmd}
    try:
        env.set_config("vault_path", str(path))
        env.set_config("demo", "off")
    except Exception as e:  # managed install, administrator-managed key, unreadable config.yaml
        return {"saved": False, "reason": type(e).__name__, "command": cmd}
    return {"saved": True, "command": cmd}


def current(env: c.Env) -> Path | None:
    """The vault in use when it is the user's own (live mode), else None."""
    try:
        st = env.state()
    except c.NotReady:
        return None
    v = st.get("vault")
    return Path(v) if st["mode"] == "live" and v is not None and Path(v).is_dir() else None


def run(env: c.Env, raw: str = "") -> dict:
    """-> {ok, path, adopted, created, saved, command, ...} or {ok: False, error, fix}."""
    try:
        if not (raw or "").strip():
            cur = current(env)
            if cur is not None:
                return {"ok": True, "path": str(cur), "already": True, "created": [], "adopted": True,
                        "saved": True, "command": SET_CMD.format(path=short(cur))}
        dest = target(raw)
        out = create(dest)
    except c.NotReady as e:
        return {"ok": False, "error": e.error, "fix": e.fix}
    except OSError as e:
        return {"ok": False, "error": f"the folder could not be created ({type(e).__name__})",
                "fix": f"pick a folder you can write to, e.g. /kokpit setup {DEFAULT}"}
    c._FOUND.clear()  # layout discovery of the old vault must not leak into the new one
    return {"ok": True, "path": str(out["path"]), "adopted": out["adopted"], "created": out["created"],
            **persist(env, out["path"])}


def text(r: dict) -> str:
    if not r["ok"]:
        return f"Kokpit: {r['error']}. Fix: {r['fix']}"
    where = short(r["path"])
    if r.get("already"):
        return (f"Kokpit already uses your vault at {where}.\n"
                f"Switch to another folder with /kokpit setup <path>.")
    if r["adopted"]:
        lines = [f"Kokpit now uses your existing vault at {where}. Nothing was changed in it."]
    else:
        made = ", ".join(r["created"]) if r["created"] else "nothing new, every file was already there"
        lines = [f"Kokpit vault ready at {where}.", f"Created: {made}. Existing files were left as they are."]
    if r["saved"]:
        lines.append("Saved as plugins.entries.kokpit.settings.vault_path (demo is off).")
    else:
        lines.append(f"Using it now, but the setting was not saved ({r.get('reason', 'unknown')}). "
                     f"Keep it after a restart with: {r['command']}")
    lines.append("Next: /kokpit note <text>, /kokpit todo <text>, /kokpit night run, /kokpit brief")
    return "\n".join(lines)
