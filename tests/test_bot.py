"""Telegram bot core. Synthetic data, stubbed HTTP, no network, no real tokens."""
from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
import urllib.error
from datetime import datetime
from pathlib import Path
from unittest import mock

from kokpit import config as cfgmod, jobs
from kokpit.db import connect
from kokpit.modules.bot import callbacks, commands, runner, store
from kokpit.modules.bot.client import BotApiError, Client, scrub
from kokpit.modules.connections import api as conn_api
from kokpit.modules.health import store as health_store

TOKEN = "123456789:" + "Z" * 2 + "FAKE" * 6
CHAT = "4242"
OTHER = "9999"


class Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakeTelegram:
    """Records (method, params); serves queued updates once."""

    def __init__(self, updates=None, error=None):
        self.updates, self.error, self.calls = list(updates or []), error, []

    def __call__(self, req, timeout=None):
        method = req.full_url.rsplit("/", 1)[1]
        params = json.loads(req.data.decode())
        self.calls.append((method, params))
        if self.error:
            raise self.error
        if method == "getUpdates":
            off = params.get("offset")
            res = [u for u in self.updates if off is None or u["update_id"] >= off]
            return Resp(json.dumps({"ok": True, "result": res}).encode())
        return Resp(json.dumps({"ok": True, "result": True}).encode())

    def sent(self, method="sendMessage"):
        return [p for m, p in self.calls if m == method]


def msg(uid, text, chat=CHAT):
    return {"update_id": uid, "message": {"message_id": uid, "chat": {"id": int(chat)}, "text": text}}


def cb(uid, data, chat=CHAT):
    return {"update_id": uid, "callback_query": {"id": f"q{uid}", "data": data, "message": {"message_id": 7, "chat": {"id": int(chat)}}}}


class BotBase(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)
        self.addCleanup(self._td.cleanup)
        env = mock.patch.dict(os.environ, {}, clear=False)
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop("KOKPIT_TELEGRAM_TOKEN", None)
        os.environ.pop("KOKPIT_INSTAGRAM_TOKEN", None)

    def cfg(self, bot=True, token=True, chat=True, mods="", extra=""):
        p = cfgmod.write_default(self.tmp / "kokpit.toml", vault=str(self.tmp / "vault"), db=str(self.tmp / "k.db"))
        text = p.read_text(encoding="utf-8")
        if bot:
            text = text.replace("bot = false", "bot = true", 1)
        for m in mods.split():
            text = text.replace(f"{m} = false", f"{m} = true", 1)
        text = text.replace('chat_id = ""', (f'token = "{TOKEN}"\n' if token else "") + (f'chat_id = "{CHAT}"' if chat else ""), 1)
        p.write_text(text + extra, encoding="utf-8")
        return cfgmod.load(p)

    def db(self, cfg):
        conn = connect(cfg.db)
        store.ensure_schema(conn)
        return conn


class ClientTest(BotBase):
    def test_scrub_removes_token_everywhere(self):
        self.assertNotIn(TOKEN, scrub(f"failed https://api.telegram.org/bot{TOKEN}/getUpdates", TOKEN))
        self.assertNotIn("ZZFAKE", scrub(f"x bot{TOKEN} y"))

    def test_http_error_message_has_no_token(self):
        err = urllib.error.HTTPError(f"https://api.telegram.org/bot{TOKEN}/getUpdates", 401, "x", {},
                                     io.BytesIO(json.dumps({"ok": False, "description": f"Unauthorized {TOKEN}"}).encode()))
        with self.assertRaises(BotApiError) as cm:
            Client(TOKEN, FakeTelegram(error=err)).get_updates()
        self.assertEqual(cm.exception.kind, "auth")
        self.assertNotIn(TOKEN, str(cm.exception))
        self.assertNotIn("ZZFAKE", str(cm.exception))

    def test_network_error_has_no_token(self):
        err = urllib.error.URLError(f"cannot reach https://api.telegram.org/bot{TOKEN}/x")
        with self.assertRaises(BotApiError) as cm:
            Client(TOKEN, FakeTelegram(error=err)).send_message(CHAT, "hi")
        self.assertEqual(cm.exception.kind, "network")
        self.assertNotIn(TOKEN, str(cm.exception))

    def test_ok_false_body_is_an_error(self):
        op = lambda req, timeout=None: Resp(json.dumps({"ok": False, "error_code": 429, "description": "slow down"}).encode())
        with self.assertRaises(BotApiError) as cm:
            Client(TOKEN, op).send_message(CHAT, "hi")
        self.assertEqual(cm.exception.kind, "rate")

    def test_methods_and_params(self):
        fake = FakeTelegram()
        c = Client(TOKEN, fake)
        c.get_updates(offset=5, timeout=7)
        c.send_message(CHAT, "x" * 5000, {"inline_keyboard": []})
        c.answer_callback_query("q1", "ok")
        c.edit_message_reply_markup(CHAT, 3)
        self.assertEqual([m for m, _ in fake.calls], ["getUpdates", "sendMessage", "answerCallbackQuery", "editMessageReplyMarkup"])
        self.assertEqual(fake.calls[0][1]["offset"], 5)
        self.assertEqual(fake.calls[0][1]["timeout"], 7)
        self.assertEqual(len(fake.calls[1][1]["text"]), 4096)


class PollTest(BotBase):
    def poll(self, cfg, fake, timeout=0):
        conn = self.db(cfg)
        try:
            return runner.poll_once(cfg, conn, Client(TOKEN, fake), timeout)
        finally:
            conn.close()

    def test_allowed_vs_foreign_chat(self):
        cfg = self.cfg()
        fake = FakeTelegram([msg(1, "/help"), msg(2, "/help", OTHER), cb(3, "x:1", OTHER)])
        c = self.poll(cfg, fake)
        self.assertEqual((c["handled"], c["foreign"]), (1, 2))
        self.assertEqual([p["chat_id"] for p in fake.sent()], [CHAT])
        self.assertEqual(fake.sent("answerCallbackQuery"), [])
        conn = self.db(cfg)
        self.assertEqual(store.get(conn, "foreign"), "2")
        conn.close()

    def test_offset_persists_and_is_not_replayed(self):
        cfg = self.cfg()
        fake = FakeTelegram([msg(10, "/help"), msg(11, "/health")])
        self.poll(cfg, fake, timeout=3)
        self.assertEqual(fake.calls[0][1].get("offset"), None)
        self.assertEqual(fake.calls[0][1]["timeout"], 3)
        conn = self.db(cfg)
        self.assertEqual(store.offset(conn), 12)
        conn.close()
        n = len(fake.sent())
        c = self.poll(cfg, fake)  # a fresh connection, as after a restart
        self.assertEqual(c["updates"], 0)
        self.assertEqual(len(fake.sent()), n)
        self.assertEqual([p for m, p in fake.calls if m == "getUpdates"][-1]["offset"], 12)

    def test_commands(self):
        cfg = self.cfg()
        fake = FakeTelegram([msg(1, "/help"), msg(2, "/health"), msg(3, "/plan"), msg(4, "/today"), msg(5, "/nope"), msg(6, "hello"), msg(7, "/HELP@some_bot")])
        self.poll(cfg, fake)
        out = [p["text"] for p in fake.sent()]
        self.assertTrue(out[0].startswith("Commands:"))
        self.assertEqual(out[1], "All good.")
        self.assertEqual(out[2], "Instagram module is off.")
        self.assertTrue(out[3].startswith("Good morning."))
        self.assertEqual(out[4], commands.UNKNOWN)
        self.assertEqual(out[5], commands.UNKNOWN)
        self.assertEqual(out[6], out[0])

    def test_health_command_lists_warnings(self):
        cfg = self.cfg()
        conn = self.db(cfg)
        health_store.record(conn, "weather", health_store.now_iso(), False, info={"error": "boom"})
        conn.close()
        fake = FakeTelegram([msg(1, "/health")])
        self.poll(cfg, fake)
        self.assertIn("weather: finished with an error", fake.sent()[0]["text"])

    def test_plan_with_instagram_on(self):
        cfg = self.cfg(mods="instagram")
        from kokpit.modules.instagram import plan, store as igs
        conn = connect(cfg.db)
        igs.ensure_schema(conn)
        plan.create(conn, {"due": "2099-01-02T10:00:00", "kind": "reel", "status": "ready"})
        conn.close()
        fake = FakeTelegram([msg(1, "/plan")])
        self.poll(cfg, fake)
        self.assertIn("2099-01-02 10:00 reel (ready)", fake.sent()[0]["text"])

    def test_api_error_on_poll_is_scrubbed_and_recorded(self):
        cfg = self.cfg()
        err = urllib.error.URLError(f"bot{TOKEN}")
        with mock.patch.object(runner, "_client", lambda c, o=None: Client(TOKEN, FakeTelegram(error=err))):
            self.assertFalse(jobs.run(cfg, "bot-poll"))
        conn = connect(cfg.db)
        row = conn.execute("SELECT info FROM runs WHERE job='bot-poll'").fetchone()[0]
        conn.close()
        self.assertNotIn(TOKEN, row)
        self.assertIn("error (network)", store.get(self.db(cfg), "last_outcome"))


class CallbackTest(BotBase):
    def test_registry_dispatch_and_unknown(self):
        cfg = self.cfg()
        seen = []
        reg = {}
        callbacks.register("t1", lambda ctx, rest: seen.append((ctx.chat_id, ctx.message_id, rest)) or "yes")
        self.addCleanup(callbacks.REGISTRY.pop, "t1", None)
        fake = FakeTelegram([cb(1, "t1:42"), cb(2, "zzz:1"), cb(3, "")])
        conn = self.db(cfg)
        runner.poll_once(cfg, conn, Client(TOKEN, fake))
        conn.close()
        self.assertEqual(seen, [(CHAT, 7, "42")])
        answers = [p["text"] for p in fake.sent("answerCallbackQuery")]
        self.assertEqual(answers, ["yes", "unknown action", "unknown action"])

    def test_handler_exception_is_generic(self):
        def boom(ctx, rest):
            raise RuntimeError(TOKEN)
        self.assertEqual(callbacks.dispatch(None, "a:b", {"a": boom}), "action failed")

    def test_bad_prefix_rejected(self):
        with self.assertRaises(ValueError):
            callbacks.register("a:b", lambda c, r: None)


class MorningTest(BotBase):
    def test_with_modules_off(self):
        cfg = self.cfg(mods="")
        cfg.modules.update({"brain": False, "health": False, "today": False, "events": False})
        line = commands.morning_line(cfg, datetime(2026, 3, 10, 7, 0))
        self.assertEqual(line, "Good morning. Tuesday 10 March.")

    def test_with_data_and_warning(self):
        cfg = self.cfg()
        conn = self.db(cfg)
        payload = {"current": {"temp": 5, "code": 1, "text": "x"}, "days": [{"date": "2026-03-10", "code": 61, "text": "light rain", "max": 12.4, "min": 3.2, "rain_mm": 2, "rain_pct": 60}]}
        conn.execute(
            "CREATE TABLE IF NOT EXISTS weather(id INTEGER PRIMARY KEY AUTOINCREMENT, fetched TEXT, lat REAL, lon REAL, payload TEXT)")
        conn.execute("INSERT INTO weather(fetched, lat, lon, payload) VALUES (?,?,?,?)", (datetime.now().isoformat(timespec="seconds"), 45.0, 9.0, json.dumps(payload)))
        conn.commit()
        health_store.record(conn, "calendar", health_store.now_iso(), False, info={"error": "x"})
        conn.close()
        cfg.place.update({"city": "Testville", "lat": 45.0, "lon": 9.0})
        line = commands.morning_line(cfg, datetime(2026, 3, 10, 7, 0))
        self.assertIn("Weather: light rain, 3 to 12 C, rain 60%.", line)
        self.assertIn("Warnings: 1.", line)

    def test_job_sends_to_allowed_chat(self):
        cfg = self.cfg()
        fake = FakeTelegram()
        rows, info, warns = runner.job_morning(cfg, fake, datetime(2026, 3, 10, 7, 0))
        self.assertEqual((rows, warns, info["sent"]), (1, [], True))
        self.assertEqual(fake.sent()[0]["chat_id"], CHAT)
        self.assertTrue(fake.sent()[0]["text"].startswith("Good morning."))

    def test_job_off_does_nothing(self):
        cfg = self.cfg(bot=False)
        fake = FakeTelegram()
        self.assertEqual(runner.job_morning(cfg, fake)[1], {"enabled": False})
        self.assertEqual(fake.calls, [])

    def test_loop_sends_morning_once(self):
        cfg = self.cfg()
        cfg.bot["morning_time"] = "07:00"
        fake = FakeTelegram()
        t = datetime(2026, 3, 10, 7, 5)
        runner.loop(cfg, fake, sleep=lambda s: None, max_passes=3, now=lambda: t)
        self.assertEqual(len(fake.sent()), 1)
        self.assertEqual(len([1 for m, _ in fake.calls if m == "getUpdates"]), 3)


class ConfigHealthTest(BotBase):
    def test_off_by_default(self):
        p = cfgmod.write_default(self.tmp / "kokpit.toml", vault=str(self.tmp / "v"), db=str(self.tmp / "k.db"))
        cfg = cfgmod.load(p)
        self.assertFalse(cfg.modules["bot"])
        self.assertEqual((cfg.bot["token"], cfg.bot["chat_id"], cfg.bot["morning_time"], cfg.bot["poll_timeout"]), ("", "", "07:30", 25))
        self.assertEqual(runner.job_poll(cfg, FakeTelegram())[1], {"enabled": False})

    def test_config_values_parsed(self):
        cfg = self.cfg(extra="\n")
        self.assertEqual(cfg.bot["chat_id"], CHAT)
        self.assertNotIn(TOKEN, repr(cfg))

    def test_missing_token_is_health_warning_not_crash(self):
        cfg = self.cfg(token=False)
        fake = FakeTelegram()
        self.assertTrue(jobs.run(cfg, "bot-poll"))
        self.assertTrue(jobs.run(cfg, "bot-morning"))
        self.assertEqual(fake.calls, [])
        conn = connect(cfg.db)
        ws = health_store.today_warnings(conn)
        conn.close()
        self.assertTrue(any("bot-poll: bot: no token" in w for w in ws), ws)

    def test_missing_chat_id_is_health_warning(self):
        cfg = self.cfg(chat=False)
        rows, info, warns = runner.job_poll(cfg, FakeTelegram())
        self.assertEqual(rows, 1)
        self.assertEqual(warns, ["bot: no chat_id ([bot] chat_id)"])

    def test_env_token_used(self):
        cfg = self.cfg(token=False)
        with mock.patch.dict(os.environ, {"KOKPIT_TELEGRAM_TOKEN": TOKEN}):
            self.assertEqual(runner.job_poll(cfg, FakeTelegram())[2], [])

    def test_send_test_requires_config(self):
        with self.assertRaises(ValueError):
            runner.send_test(self.cfg(token=False), FakeTelegram())
        fake = FakeTelegram()
        runner.send_test(self.cfg(), fake)
        self.assertEqual(fake.sent()[0]["chat_id"], CHAT)

    def test_connections_row(self):
        def row(cfg):
            return next(i for i in conn_api.view(cfg)["connections"] if i["id"] == "telegram-bot")
        self.assertEqual(row(self.cfg(bot=False))["status"], "off")
        self.assertEqual(row(self.cfg(token=False))["status"], "not configured")
        cfg = self.cfg()
        self.assertEqual(row(cfg)["status"], "not run yet")
        conn = self.db(cfg)
        runner.poll_once(cfg, conn, Client(TOKEN, FakeTelegram([msg(1, "/help", OTHER)])))
        conn.close()
        r = row(cfg)
        self.assertEqual(r["status"], "ok")
        self.assertIn("Updates ignored from other chats: 1", r["detail"])
        self.assertNotIn(TOKEN, json.dumps(r))
        self.assertNotIn(CHAT, json.dumps(r))


if __name__ == "__main__":
    unittest.main()
