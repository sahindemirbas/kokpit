"""JSON views for the Social tab and the CLI summary. Read-only: reads the tables the instagram job fills."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from ...config import Config
from ...db import connect
from ..health import api as health_api
from . import store
from .token import configured

SNAP_JOIN = ("SELECT m.media_id, m.media_type, m.product, m.posted_at, m.permalink, s.views, s.reach, s.saved, s.shares, s.likes, s.comments, s.interactions, s.replies, s.avg_watch_ms "
             "FROM ig_media m LEFT JOIN ig_media_snap s ON s.media_id = m.media_id AND s.day = (SELECT MAX(day) FROM ig_media_snap WHERE media_id = m.media_id) ")


def view(cfg: Config, now: datetime | None = None) -> dict:
    if not cfg.modules.get("instagram", False):
        return {"enabled": False}
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    out = {"enabled": True, "analyse": cfg.instagram["analyse"], "publish": cfg.instagram["publish"], "configured": configured(cfg),
           "freshness": health_api.freshness(cfg, "instagram"), "followers": [], "posts": [], "reels": [], "stories": [],
           "best_30d": None, "totals_30d": {}, "errors": [], "last_collect": None, "last_success": None, "available": False}
    if not cfg.db.exists():
        return out
    conn = connect(cfg.db, readonly=True)
    try:
        if not store.has_tables(conn):
            return out
        out["available"] = True
        meta = store.meta(conn)
        out["last_collect"], out["last_success"] = meta.get("last_collect"), meta.get("last_success")
        out["followers"] = [dict(r) for r in conn.execute("SELECT day, followers FROM ig_account WHERE followers IS NOT NULL ORDER BY day DESC LIMIT 60")][::-1]
        since = (now - timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%S")
        for key, cond in (("posts", "m.product NOT IN ('REELS','STORY')"), ("reels", "m.product = 'REELS'"), ("stories", "m.product = 'STORY'")):
            out[key] = [dict(r) for r in conn.execute(f"{SNAP_JOIN} WHERE {cond} ORDER BY m.posted_at DESC LIMIT 10")]
        recent = [dict(r) for r in conn.execute(f"{SNAP_JOIN} WHERE m.posted_at >= ? ", (since,))]
        feed = [r for r in recent if r["product"] != "STORY"]
        ranked = [r for r in feed if r["reach"] is not None]
        out["best_30d"] = max(ranked, key=lambda r: r["reach"]) if ranked else None
        out["totals_30d"] = {"posts": sum(1 for r in feed if r["product"] != "REELS"), "reels": sum(1 for r in feed if r["product"] == "REELS"),
                             "stories": sum(1 for r in recent if r["product"] == "STORY"),
                             **{k: sum(r[k] or 0 for r in feed) for k in ("views", "reach", "likes", "comments", "saved", "shares")}}
        out["errors"] = [dict(r) for r in conn.execute("SELECT scope, name, message FROM ig_errors ORDER BY scope, name")]
    finally:
        conn.close()
    return out


def summary_text(cfg: Config, now: datetime | None = None) -> str:
    """Aggregates only: counts and totals, no media ids, links or captions."""
    d = view(cfg, now)
    if not d["enabled"]:
        return "instagram: module off (set instagram = true under [modules])"
    lines = [f"analysis: {'on' if d['analyse'] else 'off'}, publishing: {'on' if d['publish'] else 'off'}, token configured: {'yes' if d['configured'] else 'no'}"]
    if not d["available"]:
        lines.append("no data yet: run `kokpit instagram collect`")
        return "\n".join(lines)
    lines.append(f"last collect: {d['last_collect'] or 'never'}; last success: {d['last_success'] or 'never'}")
    f = d["followers"]
    if f:
        change = f"{f[-1]['followers'] - f[0]['followers']:+d} across {len(f)} daily snapshots" if len(f) > 1 else "one snapshot"
        lines.append(f"followers: {f[-1]['followers']} ({change})")
    t = d["totals_30d"]
    lines.append(f"last 30 days: {t.get('posts', 0)} posts, {t.get('reels', 0)} reels, {t.get('stories', 0)} stories seen")
    lines.append("feed totals 30 days: " + ", ".join(f"{k} {t.get(k, 0)}" for k in ("views", "reach", "likes", "comments", "saved", "shares")))
    b = d["best_30d"]
    lines.append(f"best by reach in 30 days: a {b['product'].lower()} with reach {b['reach']}" if b else "best by reach in 30 days: none")
    names = sorted({e["scope"] + ":" + e["name"] for e in d["errors"]})
    lines.append(f"errors in the last run: {len(d['errors'])}" + (" (" + ", ".join(names[:8]) + ")" if names else ""))
    return "\n".join(lines)
