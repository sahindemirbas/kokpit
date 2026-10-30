"""`kokpit brain agents init`: copy the agent templates into a vault or project root.

The templates live in `agents/` next to this file. Two placeholders are filled:
`{{KOKPIT}}` (the command that runs Kokpit) and `{{CONFIG}}` (the absolute path
of kokpit.toml). An existing file is never overwritten: it is reported as
skipped, and for a settings file the filled snippet is returned so the user can
merge it by hand.
"""
from __future__ import annotations

import os
import shlex
import shutil
import sys
import sysconfig
import tempfile
from pathlib import Path

TEMPLATES = Path(__file__).resolve().parent / "agents"
HARNESSES = ("claude", "codex", "antigravity")

# (template file, path written under the target, harness or None for all, is a settings file)
FILES = (
    ("AGENTS.md.tmpl", "AGENTS.md", None, False),
    ("CLAUDE.md.tmpl", "CLAUDE.md", "claude", False),
    ("claude-settings.json", ".claude/settings.json", "claude", True),
    ("codex-hooks.json", ".codex/hooks.json", "codex", True),
    ("codex-config.toml", ".codex/config.toml", "codex", True),
    ("antigravity-hooks.json", ".agents/hooks.json", "antigravity", True),
)


def kokpit_command() -> str:
    """Absolute command that runs Kokpit: the `kokpit` script if on PATH, else this Python with -m kokpit."""
    found = shutil.which("kokpit")
    if found:
        return shlex.quote(str(Path(found).resolve()))
    python = shlex.quote(sys.executable)
    root = Path(__file__).resolve().parents[3]
    site = {Path(p).resolve() for p in (sysconfig.get_paths().get("purelib"), sysconfig.get_paths().get("platlib")) if p}
    if root in site:
        return f"{python} -m kokpit"
    # running from a source checkout that is not installed: point Python at it
    return f"env PYTHONPATH={shlex.quote(str(root))} {python} -m kokpit"


def parse_harnesses(value: str | None) -> tuple[str, ...]:
    if not value:
        return HARNESSES
    picked = tuple(h.strip() for h in value.split(",") if h.strip())
    bad = [h for h in picked if h not in HARNESSES]
    if bad or not picked:
        raise ValueError(f"unknown harness {', '.join(bad) or '(empty)'}; use {', '.join(HARNESSES)}")
    return picked


def render(name: str, kokpit: str, config: Path) -> str:
    text = (TEMPLATES / name).read_text(encoding="utf-8")
    cfg = shlex.quote(str(config))
    if name.endswith(".json"):  # the command sits inside a JSON string
        import json
        kokpit, cfg = json.dumps(kokpit)[1:-1], json.dumps(cfg)[1:-1]
    return text.replace("{{KOKPIT}}", kokpit).replace("{{CONFIG}}", cfg)


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as fh:
            fh.write(text)
        if path.exists():  # appeared meanwhile: still never overwrite
            raise FileExistsError(str(path))
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def init_agents(target: Path, config: Path, harnesses=HARNESSES, dry_run: bool = False,
                kokpit: str | None = None) -> dict:
    """Write the templates under `target`. Returns {"written", "skipped", "snippets"} with target-relative paths.

    `kokpit` is a ready shell command (already quoted); default: kokpit_command().
    """
    target = Path(target)
    config = Path(config).resolve()
    kokpit = kokpit or kokpit_command()
    report: dict = {"written": [], "skipped": [], "snippets": {}}
    for tmpl, rel, harness, settings in FILES:
        if harness is not None and harness not in harnesses:
            continue
        text = render(tmpl, kokpit, config)
        dst = target / rel
        if dst.exists():
            report["skipped"].append(rel)
            if settings:
                report["snippets"][rel] = text
            continue
        if not dry_run:
            try:
                _atomic_write(dst, text)
            except FileExistsError:
                report["skipped"].append(rel)
                if settings:
                    report["snippets"][rel] = text
                continue
        report["written"].append(rel)
    if dry_run:
        report["dry_run"] = True
    return report
