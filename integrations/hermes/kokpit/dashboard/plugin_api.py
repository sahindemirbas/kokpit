"""Dashboard routes for the Kokpit tab, mounted by Hermes at /api/plugins/kokpit/.

Thin on purpose: every route returns the same JSON the kokpit_* tools return
(tools.overview / tools.run), so the tab, the desktop panel and the agent never
disagree. Read-only except POST /capture, which goes through kokpit_capture.
Handlers are plain `def`, so FastAPI runs them in a worker thread and a slow
disk never blocks the dashboard's event loop.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from fastapi import APIRouter, Body, HTTPException

PLUGIN_DIR = Path(__file__).resolve().parent.parent
PACKAGE = "kokpit_hermes_dashboard_core"
READ_TOOLS = ("kokpit_today", "kokpit_threads", "kokpit_health", "kokpit_ai_usage", "kokpit_events",
              "kokpit_jobs", "kokpit_brain_search", "kokpit_training", "kokpit_server", "kokpit_briefing",
              "kokpit_market")


def _load_package():
    """Load this plugin folder as a package under a private name.

    Hermes loads this file on its own (spec_from_file_location), so the relative
    imports inside the plugin need the package registered first. The private name
    keeps it apart from the copy the agent loaded for the tools.
    """
    pkg = sys.modules.get(PACKAGE)
    if pkg is not None:
        return pkg
    spec = importlib.util.spec_from_file_location(PACKAGE, PLUGIN_DIR / "__init__.py",
                                                  submodule_search_locations=[str(PLUGIN_DIR)])
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load the kokpit plugin from {PLUGIN_DIR}")
    pkg = importlib.util.module_from_spec(spec)
    sys.modules.setdefault(PACKAGE, pkg)
    spec.loader.exec_module(pkg)
    return pkg


_pkg = _load_package()
tools = _pkg.tools
contract = _pkg.contract


def plugin_settings() -> dict:
    """plugins.entries.kokpit.settings, the same values ctx.get_config gives the tools."""
    try:
        from hermes_cli.config import load_config_readonly
        cfg = load_config_readonly() or {}
    except Exception:
        return {}
    entry = ((cfg.get("plugins") or {}).get("entries") or {}).get("kokpit") or {}
    for key in ("settings", "config"):
        if isinstance(entry.get(key), dict):
            return entry[key]
    return {}


def make_env(settings: dict | None = None) -> "contract.Env":
    """An Env that reads the settings fresh on every request (an edited setting applies at once)."""
    if settings is not None:
        return contract.Env(lambda key, default=None: settings.get(key, default))
    return contract.Env(lambda key, default=None: plugin_settings().get(key, default))


router = APIRouter()
ENV = make_env()


@router.get("/overview")
def overview():
    return tools.overview(ENV)


@router.get("/tool/{name}")
def tool(name: str, query: str = "", days: int = 0, limit: int = 0):
    if name not in READ_TOOLS:
        raise HTTPException(status_code=404, detail=f"{name} is not a read-only kokpit tool")
    args = {k: v for k, v in (("query", query), ("days", days), ("limit", limit)) if v}
    return tools.run(ENV, name, args)


@router.post("/capture")
def capture(body: dict = Body(...)):
    """The only write: the same path as the kokpit_capture tool (inbox note or To-Do task)."""
    args = {k: str(body.get(k) or "") for k in ("text", "kind", "due")}
    return tools.run(ENV, "kokpit_capture", args)
