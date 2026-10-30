"""Polling, morning message and the job functions. One allowed chat; everything else is counted and ignored."""
from __future__ import annotations

import time
from datetime import datetime

from ...config import Config
from ...db import connect
from ..health import store as health_store
from . import callbacks, commands, instagram, store
from .client import BotApiError, Client
from .token import chat_id, missing, resolve


def _client(cfg: Config, opener=None) -> Client:
    return Client(resolve(cfg), opener)


def handle_update(cfg: Config, conn, client, upd: dict) -> str:
    """Returns handled | foreign | skipped. A foreign chat is never answered, not even a button press."""
    allowed = chat_id(cfg)
    cb = upd.get("callback_query")
    if isinstance(cb, dict):
        msg = cb.get("message") or {}
        cid = str((msg.get("chat") or {}).get("id", ""))
        if not cid or cid != allowed:
            store.bump(conn, "foreign")
            return "foreign"
        ctx = callbacks.Context(cfg, conn, client, cid, msg.get("message_id"))
        answer = callbacks.dispatch(ctx, str(cb.get("data") or ""))
        client.answer_callback_query(str(cb.get("id") or ""), answer)
        return "handled"
    msg = upd.get("message")
    if isinstance(msg, dict):
        cid = str((msg.get("chat") or {}).get("id", ""))
        if not cid or cid != allowed:
            store.bump(conn, "foreign")
            return "foreign"
        text = str(msg.get("text") or "")
        client.send_message(cid, commands.reply(cfg, text))
        if commands.command(text) == "/plan":
            instagram.send_ready_cards(cfg, conn, client, cid)
        return "handled"
    return "skipped"


def poll_once(cfg: Config, conn, client, timeout: int = 0) -> dict:
    """One getUpdates pass. The offset moves after every update, so a restart never replays a command."""
    store.ensure_schema(conn)
    counts = {"updates": 0, "handled": 0, "foreign": 0, "skipped": 0}
    for upd in client.get_updates(store.offset(conn), timeout):
        uid = upd.get("update_id")
        if not isinstance(uid, int):
            continue
        store.put(conn, "offset", uid + 1)
        counts["updates"] += 1
        try:
            counts[handle_update(cfg, conn, client, upd)] += 1
        except BotApiError:
            counts["skipped"] += 1
    store.put(conn, "last_poll", datetime.now().isoformat(timespec="seconds"))
    store.put(conn, "last_outcome", "ok")
    return counts


def _fail(conn, exc: Exception) -> None:
    store.ensure_schema(conn)
    store.put(conn, "last_poll", datetime.now().isoformat(timespec="seconds"))
    store.put(conn, "last_outcome", f"error ({getattr(exc, 'kind', type(exc).__name__)})")


def _off(cfg: Config):
    return None if cfg.modules.get("bot", False) else (1, {"enabled": False}, [])


def job_poll(cfg: Config, opener=None):
    """Telegram bot: one getUpdates pass (cron friendly); answers commands from the allowed chat only."""
    off = _off(cfg)
    if off:
        return off
    gaps = missing(cfg)
    if gaps:
        return 1, {"configured": False}, [f"bot: {g}" for g in gaps]
    conn = connect(cfg.db)
    try:
        try:
            c = poll_once(cfg, conn, _client(cfg, opener), 0)
        except BotApiError as exc:
            _fail(conn, exc)
            raise
        return 1, c, ["bot: ignored %d update(s) from other chats" % c["foreign"]] if c["foreign"] else []
    finally:
        conn.close()


def job_morning(cfg: Config, opener=None, now: datetime | None = None):
    """Telegram bot: sends the morning line to the allowed chat."""
    off = _off(cfg)
    if off:
        return off
    gaps = missing(cfg)
    if gaps:
        return 1, {"configured": False}, [f"bot: {g}" for g in gaps]
    now = now or datetime.now()
    line = commands.morning_line(cfg, now)
    _client(cfg, opener).send_message(chat_id(cfg), line)
    conn = connect(cfg.db)
    try:
        store.ensure_schema(conn)
        store.put(conn, "last_morning", now.date().isoformat())
    finally:
        conn.close()
    return 1, {"sent": True, "chars": len(line)}, []


def send_test(cfg: Config, opener=None) -> None:
    """Sends one fixed test message. Raises ValueError when the token or chat id is missing."""
    gaps = missing(cfg)
    if gaps:
        raise ValueError("; ".join(gaps))
    _client(cfg, opener).send_message(chat_id(cfg), "Kokpit test message. The bot is connected.")


def morning_due(cfg: Config, conn, now: datetime) -> bool:
    hh, _, mm = cfg.bot["morning_time"].partition(":")
    return (now.hour, now.minute) >= (int(hh), int(mm)) and store.get(conn, "last_morning") != now.date().isoformat()


def loop(cfg: Config, opener=None, sleep=time.sleep, max_passes: int | None = None, now=datetime.now) -> None:
    """Long poll until interrupted. Records a health run when the outcome changes; sends the morning line once a day."""
    gaps = missing(cfg)
    if gaps:
        raise ValueError("; ".join(gaps))
    client = _client(cfg, opener)
    conn = connect(cfg.db)
    last, passes = None, 0
    try:
        while max_passes is None or passes < max_passes:
            passes += 1
            started = health_store.now_iso()
            try:
                c = poll_once(cfg, conn, client, cfg.bot["poll_timeout"])
                ok, info, err = True, c, None
            except BotApiError as exc:
                _fail(conn, exc)
                ok, info, err = False, {"error": repr(exc)}, exc
            if ok != last:
                health_store.record(conn, "bot-poll", started, ok, rows=1, info=info)
                last = ok
            if err is not None:
                sleep(min(60, 5 * passes))
                continue
            if cfg.modules.get("bot", False) and morning_due(cfg, conn, now()):
                try:
                    job_morning(cfg, opener, now())
                except BotApiError:
                    pass
    finally:
        conn.close()
