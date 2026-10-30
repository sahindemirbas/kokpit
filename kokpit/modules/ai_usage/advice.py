"""Rule-based advice. No model call, no setting is ever changed.

Every rule returns {id, title, evidence, estimate}: ``evidence`` holds the numbers and session hashes the rule
looked at, ``estimate`` says what kind of figure it is. Rules are independent; their numbers are never summed.
Input ``ctx``: rows (enriched: cost, fresh, total), readers (stored reader state), enabled (tool names),
settings (thresholds from config), now (aware UTC datetime).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

ESTIMATE = "estimate from list prices, not a bill"


def _item(rid: str, title: str, evidence: list[str], estimate: str = ESTIMATE) -> dict:
    return {"id": rid, "title": title, "evidence": evidence, "estimate": estimate}


def _fmt(n: int | float) -> str:
    return f"{int(n):,}".replace(",", " ")


def long_sessions(ctx: dict) -> list[dict]:
    limit = ctx["settings"]["long_session_tokens"]
    per: dict = {}
    for r in ctx["rows"]:
        s = per.setdefault((r["tool"], r["session_hash"]), {"fresh": 0, "cost": 0.0, "unpriced": False})
        s["fresh"] += r["fresh"]
        if r["cost"] is None:
            s["unpriced"] = True
        else:
            s["cost"] += r["cost"]
    big = sorted(((v["fresh"], k, v) for k, v in per.items() if v["fresh"] >= limit), reverse=True)[:5]
    if not big:
        return []
    ev = [f"{k[0]} session {k[1]}: {_fmt(f)} tokens (input + output + cache writes)" + (f", list price about {v['cost']:.2f} USD" if not v["unpriced"] else ", price unknown") for f, k, v in big]
    return [_item("long_sessions", f"{len(big)} very long session(s) (at least {_fmt(limit)} tokens): a fresh session with a short summary usually costs less", ev)]


def cache_write_heavy(ctx: dict) -> list[dict]:
    ratio, floor = ctx["settings"]["cache_write_ratio"], ctx["settings"]["cache_write_min_tokens"]
    per: dict = {}
    for r in ctx["rows"]:
        t = per.setdefault(r["tool"], [0, 0])
        t[0] += r["cache_write_tokens"]
        t[1] += r["cache_read_tokens"]
    ev = [f"{tool}: {_fmt(w)} cache-write vs {_fmt(rd)} cache-read tokens (ratio {w / rd:.2f})" if rd else f"{tool}: {_fmt(w)} cache-write tokens and no cache reads"
          for tool, (w, rd) in sorted(per.items()) if w >= floor and (rd == 0 or w / rd >= ratio)]
    if not ev:
        return []
    return [_item("cache_write_heavy", "Cache writes are large compared with cache reads: the cached prefix is rebuilt instead of reused (changing early context or long idle gaps do this)", ev)]


def background_premium(ctx: dict) -> list[dict]:
    st = ctx["settings"]
    bg = set(st["background_places"])
    threshold = st["premium_output_price"]
    per: dict = {}
    for r in ctx["rows"]:
        price = r.get("price")
        if r["place"] not in bg or not price or price.get("output") is None or price["output"] < threshold:
            continue
        k = (r["tool"], r["model"])
        e = per.setdefault(k, {"sessions": set(), "fresh": 0, "cost": 0.0, "unpriced": False})
        e["sessions"].add(r["session_hash"])
        e["fresh"] += r["fresh"]
        if r["cost"] is None:
            e["unpriced"] = True
        else:
            e["cost"] += r["cost"]
    if not per:
        return []
    ev = [f"{tool} / {model}: {len(e['sessions'])} background session(s) ({', '.join(sorted(bg))}), {_fmt(e['fresh'])} tokens"
          + ("" if e["unpriced"] else f", list price about {e['cost']:.2f} USD") + f"; e.g. {', '.join(sorted(e['sessions'])[:3])}" for (tool, model), e in sorted(per.items())]
    return [_item("background_premium", f"Background or scheduled sessions run on premium models (output price at least {threshold:g} USD per 1M tokens): check whether a cheaper model is enough for them", ev)]


def unpriced_models(ctx: dict) -> list[dict]:
    per: dict = {}
    for r in ctx["rows"]:
        if r["cost"] is None:
            per[(r["tool"], r["model"])] = per.get((r["tool"], r["model"]), 0) + r["fresh"] + r["cache_read_tokens"]
    if not per:
        return []
    ev = [f"{tool} / {model}: {_fmt(n)} tokens without a price" for (tool, model), n in sorted(per.items())]
    return [_item("unpriced_models", "Some models have no price: their usage is counted in tokens but missing from every cost figure. Add them to prices.json or enable price_refresh", ev,
                  "no figure: the price is unknown, not zero")]


def stale_reader(ctx: dict) -> list[dict]:
    days = ctx["settings"]["stale_days"]
    cutoff = ctx["now"] - timedelta(days=days)
    by = {r["tool"]: r for r in ctx["readers"]}
    ev = []
    for tool in ctx["enabled"]:
        r = by.get(tool)
        if r is None:
            ev.append(f"{tool}: enabled but never read; run `kokpit run-job ai_usage`")
        elif r["status"] not in ("ok", "empty"):
            ev.append(f"{tool}: last run {r['status']}" + (f" ({r['error']})" if r.get("error") else ""))
        elif not r["last_data"]:
            ev.append(f"{tool}: no usage rows found")
        elif datetime.fromisoformat(r["last_data"]).replace(tzinfo=timezone.utc) < cutoff:
            ev.append(f"{tool}: newest data is from {r['last_data'][:10]}, older than {days} day(s)")
    if not ev:
        return []
    return [_item("stale_reader", "A reader has no recent data: the totals may be missing real usage", ev, "no figure: data quality")]


RULES = (long_sessions, cache_write_heavy, background_premium, unpriced_models, stale_reader)


def run(ctx: dict) -> list[dict]:
    out = []
    for rule in RULES:
        out.extend(rule(ctx))
    return out
