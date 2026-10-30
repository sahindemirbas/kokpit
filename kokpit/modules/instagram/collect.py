"""The instagram job: read account fields, account insights, recent media and stories with their insights (GET only).

Rules: one failing metric or call is recorded in ``ig_errors`` and the rest goes on. An auth, rate-limit or network
error stops the run (no retry loop), keeps what was already read and becomes a Health warning. Run it twice a day,
because stories expire after 24 hours (see docs/connections.md). The token never appears in any message.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from ...config import Config
from . import store
from .client import ApiError, Client, values
from .token import resolve

ACCOUNT_METRICS = ("views", "reach", "profile_views", "accounts_engaged", "total_interactions")
STORY_METRICS = ("views", "reach", "replies", "shares", "total_interactions")
POST_METRICS = ("views", "reach", "saved", "shares", "likes", "comments", "total_interactions")
REEL_METRICS = POST_METRICS + ("ig_reels_avg_watch_time",)
RENAME = {"total_interactions": "interactions", "ig_reels_avg_watch_time": "avg_watch_ms"}
MEDIA_FIELDS = "id,media_type,media_product_type,timestamp,permalink"


def metrics_for(product: str) -> tuple[str, ...]:
    return STORY_METRICS if product == "STORY" else REEL_METRICS if product == "REELS" else POST_METRICS


def _rename(vals: dict) -> dict:
    return {RENAME.get(k, k): v for k, v in vals.items()}


def media_insights(client: Client, media_id: str, product: str, errors: list) -> tuple[dict, int]:
    """One batch call; if the API rejects it, ask metric by metric so a single bad metric costs only itself.

    Returns (values, number of metrics that failed)."""
    metrics = metrics_for(product)
    try:
        return _rename(values(client.get(f"{media_id}/insights", metric=",".join(metrics)))), 0
    except ApiError as exc:
        if exc.fatal:
            raise
    out: dict = {}
    failed = 0
    for m in metrics:
        try:
            out.update(_rename(values(client.get(f"{media_id}/insights", metric=m))))
        except ApiError as exc:
            if exc.fatal:
                raise
            failed += 1
            errors.append(("media", f"{product.lower()}:{m}", str(exc)))
    return out, failed


def collect(cfg: Config, conn: sqlite3.Connection, opener=None, now: datetime | None = None) -> tuple[int, dict, list[str]]:
    """Returns (rows, info, warnings) in the job contract."""
    ig = cfg.instagram
    if not cfg.modules.get("instagram", False):
        return 0, {}, ["instagram module is off: set instagram = true under [modules]"]
    if not ig["analyse"]:
        return 0, {}, ["instagram analysis is off: set analyse = true under [instagram]"]
    token = resolve(cfg)
    if not token:
        return 0, {"configured": False}, ["instagram: no token (set KOKPIT_INSTAGRAM_TOKEN or [instagram] token, see docs/connections.md)"]
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    day, at = now.strftime("%Y-%m-%d"), now.strftime("%Y-%m-%dT%H:%M:%SZ")
    store.ensure_schema(conn)
    client = Client(token, opener)
    errors: list[tuple[str, str, str]] = []
    warnings: list[str] = []
    info: dict = {"configured": True, "media": 0, "stories": 0, "metrics_ok": 0, "metrics_failed": 0}
    seen: set[str] = set()
    stopped = False
    try:
        me = client.get("me", fields="followers_count,media_count")
        acc: dict = {"followers": me.get("followers_count"), "media_count": me.get("media_count")}
        for metric in ACCOUNT_METRICS:
            try:
                acc.update(_rename(values(client.get("me/insights", metric=metric, period="day", metric_type="total_value"))))
            except ApiError as exc:
                if exc.fatal:
                    raise
                errors.append(("account", metric, str(exc)))
                info["metrics_failed"] += 1
        with conn:
            store.save_account(conn, day, acc, at)
        info["metrics_ok"] += sum(1 for k in ("reach", "views", "profile_views", "accounts_engaged", "interactions") if acc.get(k) is not None)
        lists: list[tuple[str, list]] = []
        for scope, path, params in (("media", "me/media", {"limit": ig["media_limit"]}), ("stories", "me/stories", {})):
            try:
                lists.append((scope, client.get(path, fields=MEDIA_FIELDS, **params).get("data", [])))
            except ApiError as exc:
                if exc.fatal:
                    raise
                errors.append((scope, "list", str(exc)))
        for scope, items in lists:
            for m in items:
                if not isinstance(m, dict) or not m.get("id") or m["id"] in seen:
                    continue
                seen.add(m["id"])
                product = "STORY" if scope == "stories" else (m.get("media_product_type") or "FEED")
                vals, failed = media_insights(client, m["id"], product, errors)
                info["metrics_failed"] += failed
                info["metrics_ok"] += sum(1 for v in vals.values() if v is not None)
                with conn:
                    store.save_media(conn, day, m, product, vals, at)
                info["stories" if scope == "stories" else "media"] += 1
    except ApiError as exc:
        stopped = True
        errors.append(("stop", exc.kind, str(exc)))
        warnings.append(f"instagram: stopped ({exc.kind}): {exc}")
    if errors and not warnings:
        names = sorted({f"{s}:{n}" for s, n, _ in errors})
        warnings.append(f"instagram: {len(errors)} call(s) failed ({', '.join(names[:6])}{', ...' if len(names) > 6 else ''})")
    with conn:
        store.replace_errors(conn, errors, at)
        store.set_meta(conn, "last_collect", at)
        if not stopped:
            store.set_meta(conn, "last_success", at)
    info["api_calls"] = client.calls
    return info["media"] + info["stories"] + 1, info, warnings
