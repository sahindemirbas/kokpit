"""Instagram approval through the bot: reminders for slots that are due soon and the Approve / Not now buttons.

Button data is ``ig:approve:<slot_id>`` and ``ig:later:<slot_id>``. Approve calls the same function as the web Approve
(``planapi.approve``), so there is one publish path and the same checks. Messages carry ids, kinds, times and a caption
excerpt only; the token never appears in them.
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

from ...config import Config
from ...db import connect
from ..instagram import plan, planapi, store as ig_store
from ..instagram.token import resolve as ig_token
from . import callbacks, store
from .client import BotApiError, Client
from .token import chat_id, missing, resolve

PREFIX = "ig"
CAPTION_CHARS = 200
OPENER = None  # Instagram HTTP opener; tests replace it with a stub
SLEEP = time.sleep


def buttons(slot_id: int) -> dict:
    return {"inline_keyboard": [[{"text": "Approve", "callback_data": f"{PREFIX}:approve:{slot_id}"},
                                 {"text": "Not now", "callback_data": f"{PREFIX}:later:{slot_id}"}]]}


def _fmt_due(due_at: str) -> str:
    return due_at.replace("T", " ")[:16] + " UTC"


def card_text(slot: dict, lead: str = "Instagram slot") -> str:
    cap = " ".join(str(slot.get("caption") or "").split())
    if len(cap) > CAPTION_CHARS:
        cap = cap[:CAPTION_CHARS].rstrip() + "..."
    lines = [f"{lead} #{slot['id']}: {slot['kind']}, due {_fmt_due(slot['due_at'])}",
             f"Owner: {slot.get('owner') or 'not set'}",
             f"Media: {len(slot.get('media_urls') or [])}",
             f"Caption: {cap or '(empty)'}"]
    return "\n".join(lines)


def _result_text(slot: dict) -> str:
    if slot["status"] == "published":
        return card_text(slot, "Published") + ("\n" + slot["permalink"] if slot.get("permalink") else "")
    if slot["status"] == "failed":
        return card_text(slot, "Failed") + f"\nError: {slot.get('error') or 'unknown'}"
    return card_text(slot, "Slot") + f"\nStatus: {slot['status']}"


def _slot_id(rest: str) -> int | None:
    kind, _, sid = rest.partition(":")
    return int(sid) if sid.isdigit() and kind else None


def _edit(ctx, text: str, markup: dict | None = None) -> None:
    if ctx.message_id is None:
        return
    try:
        ctx.client.edit_message_text(ctx.chat_id, ctx.message_id, text, markup)
    except BotApiError:
        pass  # the toast already carries the outcome


def handle(ctx: callbacks.Context, rest: str) -> str:
    action, _, _ = rest.partition(":")
    sid = _slot_id(rest)
    if sid is None or action not in ("approve", "later"):
        return callbacks.UNKNOWN
    if action == "later":
        if ctx.message_id is not None:
            try:
                ctx.client.edit_message_reply_markup(ctx.chat_id, ctx.message_id)
            except BotApiError:
                pass
        return "Left as it is"
    cfg = ctx.cfg
    if not cfg.modules.get("instagram", False):
        return "Instagram module is off"
    if not cfg.instagram["publish"]:
        return "Publishing is off (publish = false)"
    if not ig_token(cfg):
        return "No Instagram token"
    ig_store.ensure_schema(ctx.conn)
    slot = plan.get(ctx.conn, sid)
    if slot is None:
        return "No such slot"
    if slot["status"] not in ("ready", "failed"):
        _edit(ctx, _result_text(slot))
        return f"Already {slot['status']}; nothing was published again"
    try:
        slot = planapi.approve(cfg, sid, opener=OPENER, sleep=SLEEP)
    except (PermissionError, LookupError, ValueError) as exc:
        return str(exc)[:190]
    _edit(ctx, _result_text(slot))
    return "Published" if slot["status"] == "published" else "Publishing failed"


callbacks.register(PREFIX, handle)


def _due_slots(conn, now: datetime, hours: int) -> list[dict]:
    limit = (now.astimezone(timezone.utc) + timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%S")
    rows = conn.execute("SELECT id FROM ig_plan WHERE status IN ('planned','ready') AND due_at<=? ORDER BY due_at, id", (limit,)).fetchall()
    return [s for s in (plan.get(conn, r[0]) for r in rows) if s]


def _reminded(conn, slot_id: int, mode: str) -> bool:
    return conn.execute("SELECT 1 FROM bot_reminder WHERE slot_id=? AND mode=?", (slot_id, mode)).fetchone() is not None


def remind(cfg: Config, conn, client, now: datetime | None = None) -> dict:
    """Sends one reminder per slot and mode. Ready slots get buttons, planned slots a plain note."""
    now = now or datetime.now(timezone.utc)
    store.ensure_schema(conn)
    ig_store.ensure_schema(conn)
    sent = {"buttons": 0, "plain": 0}
    for slot in _due_slots(conn, now, cfg.bot["remind_hours"]):
        mode = "buttons" if slot["status"] == "ready" else "plain"
        if _reminded(conn, slot["id"], mode):
            continue
        if mode == "buttons":
            res = client.send_message(chat_id(cfg), card_text(slot, "Ready to publish"), buttons(slot["id"]))
        else:
            res = client.send_message(chat_id(cfg), card_text(slot, "Planned, not ready yet"))
        mid = res.get("message_id") if isinstance(res, dict) else None
        with conn:
            conn.execute("INSERT OR REPLACE INTO bot_reminder(slot_id, mode, message_id, sent_at) VALUES (?,?,?,?)",
                         (slot["id"], mode, mid, now.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")))
        sent[mode] += 1
    return sent


def send_ready_cards(cfg: Config, conn, client, cid: str, limit: int = 5) -> int:
    """/plan: one card with buttons for each ready slot (not stored as a reminder)."""
    if not cfg.modules.get("instagram", False):
        return 0
    ig_store.ensure_schema(conn)
    rows = conn.execute("SELECT id FROM ig_plan WHERE status='ready' ORDER BY due_at, id LIMIT ?", (limit,)).fetchall()
    for r in rows:
        slot = plan.get(conn, r[0])
        if slot:
            client.send_message(cid, card_text(slot, "Ready to publish"), buttons(slot["id"]))
    return len(rows)


def job_remind(cfg: Config, opener=None, now: datetime | None = None):
    """Telegram bot: reminds once about Instagram slots due within [bot] remind_hours."""
    if not cfg.modules.get("bot", False) or not cfg.modules.get("instagram", False):
        return 1, {"enabled": False}, []
    gaps = missing(cfg)
    if gaps:
        return 1, {"configured": False}, [f"bot: {g}" for g in gaps]
    conn = connect(cfg.db)
    try:
        return 1, remind(cfg, conn, Client(resolve(cfg), opener), now), []
    finally:
        conn.close()
