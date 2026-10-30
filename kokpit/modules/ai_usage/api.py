"""JSON views for the AI usage tab and the CLI summary. Read-only: reads the table the ai_usage job fills.

Three numbers stay apart: real API spend (what you enter, "not configured" otherwise), subscription fee (what you
enter per tool) and the list-price equivalent of the usage (an estimate, not a bill). A usage row is one session
with one model; it counts in the window that contains its ``last_seen`` (a session that spans midnight is not split).
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

from ...config import Config
from ..health import api as health_api
from . import advice, pricing, store

LABEL = "estimate, not a bill"
TOP_N = 10


def _now(now: datetime | None) -> datetime:
    return (now or datetime.now(timezone.utc)).astimezone(timezone.utc)


def _stamp(d: datetime) -> str:
    return d.strftime("%Y-%m-%dT%H:%M:%S")


def window_starts(now: datetime) -> dict[str, str]:
    local = now.astimezone()
    midnight = local.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)
    return {"today": _stamp(midnight), "7d": _stamp(now - timedelta(days=7)), "30d": _stamp(now - timedelta(days=30))}


def enrich(rows: list[dict], prices: dict) -> list[dict]:
    out = []
    for r in rows:
        price = pricing.lookup(prices, r["model"])
        fresh = r["input_tokens"] + r["output_tokens"] + r["cache_write_tokens"]
        out.append({**r, "price": price, "cost": pricing.cost(r, price), "fresh": fresh, "all_tokens": fresh + r["cache_read_tokens"]})
    return out


def totals(rows: list[dict]) -> dict:
    """Sums for a set of rows. ``estimate_usd`` is the sum of the priced rows only; ``estimate_state`` says how far to trust it:
    'known' (every token priced, or no tokens at all), 'partial' (some tokens unpriced) or 'unknown' (tokens exist, none priced;
    then ``estimate_usd`` is None, never 0)."""
    t = {"sessions": len({(r["tool"], r["session_hash"]) for r in rows}), "input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0,
         "cache_write_tokens": 0, "estimate_usd": 0.0, "priced_tokens": 0, "unpriced_tokens": 0}
    unpriced: dict[str, int] = {}
    for r in rows:
        for k in ("input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens"):
            t[k] += r[k]
        if r["cost"] is None:
            t["unpriced_tokens"] += r["all_tokens"]
            unpriced[r["model"]] = unpriced.get(r["model"], 0) + r["all_tokens"]
        else:
            t["priced_tokens"] += r["all_tokens"]
            t["estimate_usd"] += r["cost"]
    t["tokens"] = t["input_tokens"] + t["output_tokens"] + t["cache_read_tokens"] + t["cache_write_tokens"]
    t["estimate_state"] = "known" if not t["unpriced_tokens"] else ("partial" if t["priced_tokens"] else "unknown")
    t["estimate_usd"] = None if t["estimate_state"] == "unknown" else round(t["estimate_usd"], 4)
    t["unpriced_models"] = sorted(unpriced)
    t["unpriced_by_model"] = dict(sorted(unpriced.items()))
    return t


def money(t: dict) -> str:
    """Text form of a totals dict for the CLI: '12.34 USD', '12.34 USD (partial, N unpriced tokens)' or 'unknown'."""
    if t["estimate_state"] == "unknown":
        return "unknown"
    s = f"{t['estimate_usd']:.2f} USD"
    return s + f" (partial, {t['unpriced_tokens']:,} unpriced tokens)".replace(",", " ") if t["estimate_state"] == "partial" else s


def _in_window(rows: list[dict], since: str) -> list[dict]:
    return [r for r in rows if (r["last_seen"] or "") >= since]


def _group(rows: list[dict], key) -> list[dict]:
    groups: dict = {}
    for r in rows:
        groups.setdefault(key(r), []).append(r)
    out = [{"name": k, **totals(v)} for k, v in groups.items()]
    return sorted(out, key=lambda x: (-x["tokens"], x["name"]))


def sessions(rows: list[dict], limit: int = TOP_N) -> list[dict]:
    per: dict = {}
    for r in rows:
        per.setdefault((r["tool"], r["session_hash"]), []).append(r)
    out = []
    for (tool, h), v in per.items():
        t = totals(v)
        out.append({"tool": tool, "session": h, "place": v[0]["place"], "models": sorted({r["model"] for r in v}), "started_at": min((r["started_at"] or "") for r in v) or None,
                    "last_seen": max((r["last_seen"] or "") for r in v) or None, **t})
    return sorted(out, key=lambda x: (-x["tokens"], x["session"]))[:limit]


def limits(cfg: Config, rows: list[dict], now: datetime) -> dict:
    cfgd = cfg.ai_usage["limits"]
    if not cfgd:
        return {"configured": False, "message": "not configured: add [[ai_usage.limits]] entries in kokpit.toml", "items": []}
    items = []
    for lim in cfgd:
        since = _stamp(now - timedelta(hours=lim["window_hours"]))
        used = sum(r["input_tokens"] + r["output_tokens"] for r in rows if r["tool"] == lim["tool"] and (r["last_seen"] or "") >= since)
        reset = None
        if lim.get("resets_at"):
            try:
                reset = datetime.fromisoformat(lim["resets_at"].replace("Z", "+00:00"))
                reset = reset.replace(tzinfo=timezone.utc) if reset.tzinfo is None else reset.astimezone(timezone.utc)
                while reset <= now:
                    reset += timedelta(hours=lim["window_hours"])
            except ValueError:
                reset = None
        items.append({"tool": lim["tool"], "name": lim["name"], "window_hours": lim["window_hours"], "limit_tokens": lim["tokens"], "used_tokens": used,
                      "percent": round(100 * used / lim["tokens"], 1) if lim["tokens"] else None,
                      "resets_at": _stamp(reset) if reset else None,
                      "note": "used = input + output tokens of sessions last active inside the window (cache excluded); a rolling approximation, not the provider's own counter"})
    return {"configured": True, "items": items}


def numbers(cfg: Config, windows: dict) -> dict:
    cur = cfg.ai_usage["currency"]
    api, sub = cfg.ai_usage["api_spend"], cfg.ai_usage["subscriptions"]
    return {
        "real_api_spend": {"configured": bool(api), "currency": cur, "monthly_by_tool": api, "total_monthly": round(sum(api.values()), 2) if api else None,
                           "note": "what you enter per month, or 'not configured'; Kokpit does not read provider bills"},
        "subscription_fee": {"configured": bool(sub), "currency": cur, "monthly_by_tool": sub, "total_monthly": round(sum(sub.values()), 2) if sub else None,
                             "note": "what you enter per month"},
        "list_price_equivalent": {"label": LABEL, "currency": "USD", **{k: windows[k]["estimate_usd"] for k in windows},
                                  "state": {k: windows[k]["estimate_state"] for k in windows},
                                  "unpriced_tokens_30d": windows["30d"]["unpriced_tokens"], "unpriced_models": windows["30d"]["unpriced_models"],
                                  "note": "usage at published per-token prices; null means no token had a price (unknown, not zero); partial means unpriced tokens are left out and counted separately"},
    }


def _context(cfg: Config, conn: sqlite3.Connection, now: datetime) -> tuple[list[dict], list[dict], dict]:
    prices = pricing.load(cfg.resolve(cfg.ai_usage["prices_path"]))
    rows = enrich(store.rows(conn), prices)
    return rows, store.readers(conn), prices


def _open(cfg: Config):
    from ...db import connect
    try:
        return connect(cfg.db, readonly=True)
    except FileNotFoundError:
        return None


def _data(cfg: Config, now: datetime):
    conn = _open(cfg)
    if conn is None:
        return [], [], {}
    try:
        return _context(cfg, conn, now)
    except sqlite3.OperationalError:
        return [], [], {}
    finally:
        conn.close()


def reader_status(cfg: Config, stored: list[dict]) -> list[dict]:
    by = {r["tool"]: r for r in stored}
    out = []
    for tool in ("hermes", "claude_code", "codex"):
        on = tool in cfg.ai_usage["readers"]
        r = by.get(tool)
        out.append({"tool": tool, "enabled": on, "status": (r["status"] if r else "never run") if on else "off", "rows": r["rows"] if r else None,
                    "last_data": r["last_data"] if r else None, "source_mtime": r["source_mtime"] if r else None, "last_run": r["last_run"] if r else None,
                    "error": r["error"] if r else None})
    return out


def advice_for(cfg: Config, rows: list[dict], stored: list[dict], now: datetime) -> list[dict]:
    recent = _in_window(rows, window_starts(now)["30d"])
    return advice.run({"rows": recent, "readers": stored, "enabled": cfg.ai_usage["readers"], "settings": cfg.ai_usage, "now": now})


def view(cfg: Config, now: datetime | None = None) -> dict:
    if not cfg.modules.get("ai_usage", False):
        return {"enabled": False}
    now = _now(now)
    rows, stored, _ = _data(cfg, now)
    starts = window_starts(now)
    in_win = {k: _in_window(rows, s) for k, s in starts.items()}
    windows = {k: totals(v) for k, v in in_win.items()}
    for k, v in in_win.items():
        windows[k]["by_tool"] = {x["name"]: {kk: x[kk] for kk in ("tokens", "estimate_usd", "estimate_state", "unpriced_tokens", "unpriced_models", "sessions")} for x in _group(v, lambda r: r["tool"])}
    d30 = in_win["30d"]
    return {"enabled": True, "generated": _stamp(now), "label": LABEL, "freshness": health_api.freshness(cfg, "ai_usage"),
            "readers": reader_status(cfg, stored), "windows": windows, "numbers": numbers(cfg, windows),
            "by_tool": _group(d30, lambda r: r["tool"]), "by_model": _group(d30, lambda r: f"{r['tool']} / {r['model']}"),
            "top_sessions": sessions(d30), "limits": limits(cfg, rows, now), "advice": advice_for(cfg, rows, stored, now),
            "notes": ["Rows are sessions; a session counts in the window of its last activity.",
                      "Advice items are independent estimates: do not add them together.",
                      "Session ids are stored only as a short hash."]}


def sessions_view(cfg: Config, days: int = 30, limit: int = TOP_N, now: datetime | None = None) -> dict:
    if not cfg.modules.get("ai_usage", False):
        return {"enabled": False}
    now = _now(now)
    rows, _, _ = _data(cfg, now)
    since = _stamp(now - timedelta(days=days))
    return {"enabled": True, "days": days, "sessions": sessions(_in_window(rows, since), limit)}


def advice_view(cfg: Config, now: datetime | None = None) -> dict:
    if not cfg.modules.get("ai_usage", False):
        return {"enabled": False}
    now = _now(now)
    rows, stored, _ = _data(cfg, now)
    return {"enabled": True, "advice": advice_for(cfg, rows, stored, now), "note": "independent estimates; never summed"}


def limits_view(cfg: Config, now: datetime | None = None) -> dict:
    if not cfg.modules.get("ai_usage", False):
        return {"enabled": False}
    now = _now(now)
    rows, _, _ = _data(cfg, now)
    return {"enabled": True, **limits(cfg, rows, now)}


def prices_view(cfg: Config, now: datetime | None = None) -> dict:
    if not cfg.modules.get("ai_usage", False):
        return {"enabled": False}
    now = _now(now)
    rows, _, prices = _data(cfg, now)
    seen = sorted({r["model"] for r in rows})
    return {"enabled": True, "price_refresh": cfg.ai_usage["price_refresh"], "known_prices": len(prices),
            "models": [{"model": m, "price": pricing.lookup(prices, m)} for m in seen],
            "unknown": [m for m in seen if pricing.lookup(prices, m) is None]}


def summary_text(cfg: Config, now: datetime | None = None) -> str:
    v = view(cfg, now)
    if not v["enabled"]:
        return "ai_usage module is off: set ai_usage = true under [modules]"
    lines = ["AI usage (list-price figures are estimates, not bills)"]
    n = lambda x: f"{x:,}".replace(",", " ")
    for k, label in (("today", "today"), ("7d", "7 days"), ("30d", "30 days")):
        w = v["windows"][k]
        lines.append(f"{label:8s} {w['sessions']:5d} sessions  {n(w['tokens']):>16s} tokens  list price {money(w)}  unpriced tokens {n(w['unpriced_tokens'])}")
    lines.append("\nper tool, 30 days:")
    for t in v["by_tool"]:
        lines.append(f"  {t['name']:12s} {n(t['tokens']):>16s} tokens  list price {money(t)}  unpriced {n(t['unpriced_tokens'])}")
    nm = v["numbers"]
    lines.append("\nreal API spend: " + (f"{nm['real_api_spend']['total_monthly']} {nm['real_api_spend']['currency']} per month (entered)" if nm["real_api_spend"]["configured"] else "not configured"))
    lines.append("subscription fee: " + (f"{nm['subscription_fee']['total_monthly']} {nm['subscription_fee']['currency']} per month (entered)" if nm["subscription_fee"]["configured"] else "not configured"))
    lines.append("\nreaders:")
    for r in v["readers"]:
        lines.append(f"  {r['tool']:12s} {r['status']:10s} rows {r['rows'] if r['rows'] is not None else '-'}  newest data {r['last_data'] or '-'}")
    lines.append("\nlimits: " + ("; ".join(f"{i['tool']} {i['name']} {n(i['used_tokens'])}/{n(i['limit_tokens'])}" for i in v["limits"]["items"]) if v["limits"]["configured"] else "not configured"))
    lines.append("advice: " + (", ".join(a["id"] for a in v["advice"]) or "none"))
    return "\n".join(lines)
