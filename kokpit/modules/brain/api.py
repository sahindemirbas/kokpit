"""Brain module JSON views and the starter vault."""
from __future__ import annotations

import shutil
from pathlib import Path

from ...config import Config
from . import capture, reader

STARTER = Path(__file__).parent / "starter"


def view(cfg: Config) -> dict:
    data = reader.read_vault(cfg.vault)
    data["quick_capture"] = cfg.quick_capture
    return data


def init_vault(vault: Path, force: bool = False) -> list[str]:
    """Copy the starter structure into an empty (or missing) folder. Existing files are never overwritten."""
    vault = Path(vault)
    vault.mkdir(parents=True, exist_ok=True)
    created = []
    for src in sorted(STARTER.rglob("*")):
        rel = src.relative_to(STARTER)
        dst = vault / rel
        if src.is_dir():
            dst.mkdir(parents=True, exist_ok=True)
            continue
        if dst.exists() and not force:
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dst)
        created.append(str(rel))
    return created


def quick_capture(cfg: Config, kind: str, text: str, due: str = "") -> dict:
    if not cfg.quick_capture:
        raise PermissionError("quick capture is disabled in kokpit.toml")
    if kind == "task":
        return {"ok": True, "kind": "task", "line": capture.capture_task(cfg.vault, text, due)}
    if kind == "note":
        p = capture.capture_note(cfg.vault, text)
        return {"ok": True, "kind": "note", "path": str(p.relative_to(cfg.vault))}
    raise ValueError("kind must be 'note' or 'task'")


def complete_task(cfg: Config, text: str) -> dict:
    if not cfg.quick_capture:
        raise PermissionError("quick capture is disabled in kokpit.toml")
    return {"ok": True, "task": capture.complete_task(cfg.vault, text)}
