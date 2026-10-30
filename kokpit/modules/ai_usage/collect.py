"""The ai_usage job: run the enabled readers, store their rows, optionally refresh prices.

A reader whose file is missing or broken becomes a Health warning; the other readers still run and the
rows of the failed one stay as they were.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from ...config import Config
from . import pricing, readers, store


def paths(cfg: Config) -> dict[str, Path]:
    return {tool: cfg.resolve(cfg.ai_usage[key]) for tool, (key, _) in readers.READERS.items()}


def collect(cfg: Config, conn: sqlite3.Connection, get=None) -> tuple[int, dict, list[str]]:
    """Returns (rows, info, warnings) in the job contract."""
    if not cfg.modules.get("ai_usage", False):
        return 0, {}, ["ai_usage module is off: set ai_usage = true under [modules]"]
    enabled = cfg.ai_usage["readers"]
    warnings: list[str] = []
    per: dict[str, int] = {}
    total = 0
    if not enabled:
        warnings.append("no reader enabled: set [ai_usage] readers in kokpit.toml, e.g. [\"hermes\", \"claude_code\", \"codex\"]")
    src = paths(cfg)
    for tool in enabled:
        if tool not in readers.READERS:
            warnings.append(f"{tool}: unknown reader (known: {', '.join(readers.READERS)})")
            continue
        try:
            rows, meta = readers.READERS[tool][1](src[tool])
        except readers.ReaderError as exc:
            store.set_reader_error(conn, tool, "missing", str(exc))
            warnings.append(f"{tool}: {exc}")
            per[tool] = 0
            continue
        except Exception as exc:  # a parser bug or a changed format must not stop the other readers
            store.set_reader_error(conn, tool, "error", f"{type(exc).__name__}: {str(exc)[:120]}")
            warnings.append(f"{tool}: reader failed ({type(exc).__name__}: {str(exc)[:120]})")
            per[tool] = 0
            continue
        store.replace_tool(conn, tool, rows, meta.get("source_mtime"), "ok" if rows else "empty")
        per[tool] = len(rows)
        total += len(rows)
        if not rows:
            warnings.append(f"{tool}: 0 usage rows found (check the path in [ai_usage])")
    info: dict = {"readers": per}
    if cfg.ai_usage["price_refresh"]:
        state, warn = pricing.refresh(cfg.resolve(cfg.ai_usage["prices_path"]), get=get)
        info["price_refresh"] = state
        if warn:
            warnings.append(warn)
    return total, info, warnings
