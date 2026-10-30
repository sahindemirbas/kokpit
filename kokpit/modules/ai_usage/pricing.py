"""Prices per 1M tokens. Unknown is None, never 0.

Sources, in order of priority:
1. the local file ``[ai_usage] prices_path`` (yours; always wins),
2. a cache of https://models.dev/api.json, only when ``price_refresh = true`` (one GET, nothing sent, refreshed at most every 24 h).
File format: {"models": {"<model id>": {"input": 3.0, "output": 15.0, "cache_read": 0.3, "cache_write": 3.75}}}
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ... import fetch
from .normalize import alt_forms, normalize_model

URL = "https://models.dev/api.json"
KEYS = ("input", "output", "cache_read", "cache_write")
CACHE_HOURS = 24
PREFERRED = ("anthropic", "openai", "google")


def _num(v):
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) and v >= 0 else None


def _clean(entry: dict) -> dict:
    return {k: _num(entry.get(k)) for k in KEYS}


def _key(model: str) -> str:
    """One spelling per model so a local entry replaces a cached one: 4.5 and 4-5 are the same model."""
    return alt_forms(normalize_model(model))[-1]


def _read(path: Path) -> dict:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    models = data.get("models") if isinstance(data, dict) else None
    return {_key(k): _clean(v) for k, v in (models or {}).items() if isinstance(v, dict)}


def cache_path(prices_path: Path) -> Path:
    return Path(prices_path).with_name("ai-usage-models-cache.json")


def load(prices_path: Path) -> dict:
    """model -> {input, output, cache_read, cache_write}; cache first, the local file overrides it."""
    merged = {}
    cp = cache_path(prices_path)
    if cp.is_file():
        merged.update(_read(cp))
    merged.update(_read(prices_path))
    return merged


def lookup(prices: dict, model: str) -> dict | None:
    for form in alt_forms(model):
        if form in prices:
            return prices[form]
    return None


def cost(row: dict, price: dict | None) -> float | None:
    """List-price equivalent in the price currency (USD per 1M tokens); None when a needed price is unknown."""
    if price is None:
        return None
    total = 0.0
    for tok, key in (("input_tokens", "input"), ("output_tokens", "output"), ("cache_read_tokens", "cache_read"), ("cache_write_tokens", "cache_write")):
        n = row.get(tok) or 0
        if not n:
            continue
        if price.get(key) is None:
            return None
        total += n * price[key] / 1_000_000
    return total


def _parse_models_dev(data: dict) -> dict:
    out: dict = {}
    order = sorted((k for k in data if isinstance(data[k], dict)), key=lambda k: (k not in PREFERRED, PREFERRED.index(k) if k in PREFERRED else 0, k))
    for prov in order:
        for mid, m in (data[prov].get("models") or {}).items():
            c = m.get("cost") if isinstance(m, dict) else None
            if isinstance(c, dict):
                out.setdefault(_key(mid), _clean(c))
    return out


def refresh(prices_path: Path, get=None, now: datetime | None = None) -> tuple[str, str | None]:
    """-> (state, warning). state: 'fresh' (cache younger than 24 h, nothing fetched), 'updated' or 'failed'."""
    now = now or datetime.now(timezone.utc)
    cp = cache_path(prices_path)
    if cp.is_file():
        try:
            at = datetime.fromisoformat(json.loads(cp.read_text(encoding="utf-8")).get("fetched_at", ""))
            if now - at < timedelta(hours=CACHE_HOURS):
                return "fresh", None
        except (OSError, ValueError, TypeError):
            pass
    try:
        data = json.loads((get or fetch.get)(URL, timeout=30))
        models = _parse_models_dev(data)
        if not models:
            return "failed", "price refresh: models.dev answer had no prices"
        cp.parent.mkdir(parents=True, exist_ok=True)
        cp.write_text(json.dumps({"fetched_at": now.isoformat(timespec="seconds"), "models": models}), encoding="utf-8")
        return "updated", None
    except Exception as exc:  # offline, blocked, or a changed format: keep the old cache
        return "failed", f"price refresh failed: {type(exc).__name__}: {str(exc)[:100]}"
