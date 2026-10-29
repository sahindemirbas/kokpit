"""Local event adapters: discovery and loading.

An adapter is a directory ``<root>/<name>/`` holding ``collect.py``. Roots are the
repository's ``adapters/events/`` plus any ``[events] adapter_dirs`` from the
config (user roots are searched first, so a user adapter can shadow a shipped one).

Contract of ``collect.py`` (see adapters/README.md):

    NAME = "my-city-theatre"        # optional; defaults to the directory name; becomes ``source``
    CITY = "My City"                # optional, shown by ``kokpit adapters``
    COUNTRY = "IT"                  # optional
    ENABLED_BY_DEFAULT = False      # informational; only ``[events] adapters`` enables an adapter
    def collect(settings: dict) -> list[dict]: ...

Discovery never imports anything. Only adapters named in ``[events] adapters``
are imported (importlib, by file path). Every failure (import error, missing
``collect``, exception, wrong return type) becomes a short message for the
health table and never stops the other sources.
"""
from __future__ import annotations

import ast
import importlib.util
from collections.abc import Callable
from pathlib import Path

from ...config import Config

REPO_ROOT = Path(__file__).resolve().parents[3] / "adapters" / "events"
ENTRY = "collect.py"


class AdapterError(RuntimeError):
    """A short, health-table friendly reason why an adapter produced nothing."""


def roots(cfg: Config) -> list[Path]:
    out = [cfg.resolve(d) for d in cfg.events.get("adapter_dirs", [])]
    out.append(REPO_ROOT)
    return out


def _constants(path: Path) -> dict:
    """Read NAME/CITY/COUNTRY/ENABLED_BY_DEFAULT without importing (listing must be safe)."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError, ValueError):
        return {}
    found = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            key = node.targets[0].id
            if key in ("NAME", "CITY", "COUNTRY", "ENABLED_BY_DEFAULT") and isinstance(node.value, ast.Constant):
                found[key] = node.value.value
    return found


def _key(name: str) -> str:
    name = name.strip().strip("/")
    return name[len("events/"):] if name.startswith("events/") else name


def enabled_names(cfg: Config) -> set[str]:
    return {_key(n) for n in cfg.events.get("adapters", []) if str(n).strip()}


def discover(cfg: Config) -> list[dict]:
    """Every adapter directory found, with its enabled flag. Imports nothing."""
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
            seen[d.name] = {
                "id": d.name, "name": name, "path": d, "city": meta.get("CITY"), "country": meta.get("COUNTRY"),
                "enabled": d.name in on or name in on, "user": root != REPO_ROOT,
            }
    return list(seen.values())


def missing(cfg: Config) -> list[str]:
    """Names enabled in the config that no root provides."""
    known = {a["id"] for a in discover(cfg)} | {a["name"] for a in discover(cfg)}
    return sorted(n for n in enabled_names(cfg) if n not in known)


def settings(cfg: Config) -> dict:
    p = cfg.place
    langs = p.get("languages") or ["en"]
    return {"city": p.get("city"), "country": p.get("country"), "lat": p.get("lat"), "lon": p.get("lon"),
            "radius_km": p.get("radius_km"), "province": p.get("province"), "provinces": p.get("provinces", []),
            "languages": list(langs), "language": langs[0]}


def run_one(adapter: dict, ctx: dict) -> list[dict]:
    """Import and call one adapter. Raises AdapterError with a short reason."""
    path = adapter["path"] / ENTRY
    modname = "kokpit_adapter_" + "".join(c if c.isalnum() else "_" for c in adapter["id"])
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
    out = []
    for r in got:
        r = dict(r)
        r["source"] = name
        r["source_id"] = str(r.get("source_id") or r.get("url") or f"{r.get('title')}|{r.get('start')}")
        r.setdefault("price", "unknown")
        r.setdefault("language", ctx.get("language"))
        out.append(r)
    return out


def plan(cfg: Config) -> list[tuple[str, Callable[[], list[dict]]]]:
    """(source name, thunk) for every enabled adapter, in the shape events.api.collect uses."""
    ctx = settings(cfg)
    return [(a["name"], (lambda a=a: run_one(a, ctx))) for a in discover(cfg) if a["enabled"]]
