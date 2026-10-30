"""Local job adapters: discovery and loading, mirroring the events loader.

An adapter is a directory ``<root>/<name>/`` holding ``collect.py``. Roots are the repository's
``adapters/jobs/`` plus any ``[career] adapter_dirs`` (user roots first). Only ids listed in
``[career] adapters`` are imported; discovery reads constants with ``ast`` and imports nothing.
Every failure becomes a short message for the health table and never stops the other sources.

    NAME = "example-careers"     # optional; becomes ``source``
    COMPANY = "Example Corp"     # optional, shown by ``kokpit adapters``
    def collect(settings: dict) -> list[dict]: ...
"""
from __future__ import annotations

import ast
import importlib.util
from collections.abc import Callable
from pathlib import Path

from ...config import Config
from ..events.adapters import AdapterError

REPO_ROOT = Path(__file__).resolve().parents[3] / "adapters" / "jobs"
ENTRY = "collect.py"
REMOTE_VALUES = ("remote", "hybrid", "onsite", "unknown")

__all__ = ["AdapterError", "roots", "discover", "missing", "enabled_names", "plan", "run_one", "settings", "normalise"]


def roots(cfg: Config) -> list[Path]:
    return [cfg.resolve(d) for d in cfg.career.get("adapter_dirs", [])] + [REPO_ROOT]


def _constants(path: Path) -> dict:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError, ValueError):
        return {}
    found = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            if node.targets[0].id in ("NAME", "COMPANY", "ENABLED_BY_DEFAULT") and isinstance(node.value, ast.Constant):
                found[node.targets[0].id] = node.value.value
    return found


def _key(name: str) -> str:
    name = str(name).strip().strip("/")
    return name[len("jobs/"):] if name.startswith("jobs/") else name


def enabled_names(cfg: Config) -> set[str]:
    return {_key(n) for n in cfg.career.get("adapters", []) if str(n).strip()}


def discover(cfg: Config) -> list[dict]:
    on = enabled_names(cfg)
    seen: dict[str, dict] = {}
    for root in roots(cfg):
        if not root.is_dir():
            continue
        for d in sorted(p for p in root.iterdir() if p.is_dir() and (p / ENTRY).is_file()):
            if d.name in seen:
                continue
            meta = _constants(d / ENTRY)
            name = str(meta.get("NAME") or d.name)
            seen[d.name] = {"id": d.name, "name": name, "path": d, "company": meta.get("COMPANY"),
                            "enabled": d.name in on or name in on, "user": root != REPO_ROOT}
    return list(seen.values())


def missing(cfg: Config) -> list[str]:
    found = discover(cfg)
    known = {a["id"] for a in found} | {a["name"] for a in found}
    return sorted(n for n in enabled_names(cfg) if n not in known)


def settings(cfg: Config) -> dict:
    from . import profile
    prof = profile.load(cfg)
    return {"target_roles": prof["target_roles"], "locations": prof["locations"], "remote_pref": prof["remote_pref"],
            "languages": [x["language"] for x in prof["languages"]], "country": cfg.place.get("country")}


def normalise(r: dict, source: str) -> dict | None:
    """One record in the job contract, or None when it has no title."""
    title = " ".join(str(r.get("title") or "").split())
    if not title:
        return None
    ext = str(r.get("external_id") or r.get("source_id") or r.get("url") or f"{title}|{r.get('company')}")
    remote = str(r.get("remote") or "unknown").lower()
    posted = str(r.get("posted_at") or r.get("posted") or "")[:10] or None

    def opt(k):
        v = r.get(k)
        return " ".join(str(v).split()) if v not in (None, "") else None
    return {"source": source, "external_id": ext, "title": title[:300], "company": opt("company"), "location": opt("location"),
            "remote": remote if remote in REMOTE_VALUES else "unknown", "url": opt("url"), "posted_at": posted,
            "description": str(r["description"])[:20000] if r.get("description") else None, "language": opt("language")}


def run_one(adapter: dict, ctx: dict) -> list[dict]:
    path = adapter["path"] / ENTRY
    modname = "kokpit_jobs_adapter_" + "".join(c if c.isalnum() else "_" for c in adapter["id"])
    try:
        spec = importlib.util.spec_from_file_location(modname, path)
        if spec is None or spec.loader is None:
            raise ImportError(f"cannot load {path.name}")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    except (Exception, SystemExit) as exc:
        raise AdapterError(f"import failed: {type(exc).__name__}: {str(exc)[:120]}") from None
    fn = getattr(mod, "collect", None)
    if not callable(fn):
        raise AdapterError("no collect(settings) function")
    name = str(getattr(mod, "NAME", "") or adapter["name"])
    try:
        got = fn(dict(ctx))
    except (Exception, SystemExit) as exc:
        raise AdapterError(f"{type(exc).__name__}: {str(exc)[:120]}") from None
    if not isinstance(got, list) or not all(isinstance(r, dict) for r in got):
        raise AdapterError(f"collect() must return list[dict], got {type(got).__name__}")
    return [n for n in (normalise(r, name) for r in got) if n]


def plan(cfg: Config) -> list[tuple[str, Callable[[], list[dict]]]]:
    ctx = settings(cfg)
    return [(a["name"], (lambda a=a: run_one(a, ctx))) for a in discover(cfg) if a["enabled"]]
