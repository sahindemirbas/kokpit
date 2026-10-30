"""Instagram approval through the bot. Synthetic data, stubbed Telegram and Instagram HTTP, no network."""
from __future__ import annotations

import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from kokpit import config as cfgmod, jobs
from kokpit.db import connect
from kokpit.modules.bot import callbacks, instagram as bot_ig, runner, store
from kokpit.modules.bot.client import Client
from kokpit.modules.instagram import plan, store as ig_store
from test_bot import CHAT, OTHER, TOKEN, FakeTelegram, cb, msg
from test_instagram_publish import FakeApi

IGTOKEN = "IGTEST-token-for-tests-123"
NOW = datetime(2026, 3, 10, 12, 0, tzinfo=timezone.utc)


class BotIgBase(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)
        self.addCleanup(self._td.cleanup)
        env = mock.patch.dict(os.environ, {}, clear=False)
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop("KOKPIT_TELEGRAM_TOKEN", None)
        os.environ["KOKPIT_INSTAGRAM_TOKEN"] = IGTOKEN
        self.api = FakeApi()
        for patch in (mock.patch.object(bot_ig, "OPENER", self.api), mock.patch.object(bot_ig, "SLEEP", lambda s: None)):
            patch.start()
            self.addCleanup(patch.stop)

    def cfg(self, bot=True, ig=True, publish=True):
        p = cfgmod.write_default(self.tmp / "kokpit.toml", vault=str(self.tmp / "vault"), db=str(self.tmp / "k.db"))
        text = p.read_text(encoding="utf-8")
        if bot:
            text = text.replace("bot = false", "bot = true", 1)
        if ig:
            text = text.replace("instagram = false", "instagram = true", 1)
        if publish:
            text = text.replace("publish = false", "publish = true", 1)
        text = text.replace('token_file = "data/instagram-token"', f'token_file = "{self.tmp / "tok"}"', 1)
        text = text.replace('chat_id = ""', f'token = "{TOKEN}"\nchat_id = "{CHAT}"', 1)
        p.write_text(text, encoding="utf-8")
        return cfgmod.load(p)

    def conn(self, cfg):
        c = connect(cfg.db)
        store.ensure_schema(c)
        ig_store.ensure_schema(c)
        self.addCleanup(c.close)
        return c

    def slot(self, conn, status="ready", due="2026-03-11 09:00", **kw):
        return plan.create(conn, {"due": due, "kind": "post", "owner": "me", "caption": "hello world", "media_urls": ["https://example.invalid/a.jpg"],
                                  "status": status, **kw}, NOW)

    def poll(self, cfg, conn, updates):
        tg = FakeTelegram(updates)
        runner.poll_once(cfg, conn, Client(TOKEN, tg))
        return tg


class ApproveTest(BotIgBase):
    def test_approve_publishes_once_and_edits_message(self):
        cfg = self.cfg()
        conn = self.conn(cfg)
        s = self.slot(conn)
        tg = self.poll(cfg, conn, [cb(1, f"ig:approve:{s['id']}")])
        self.assertEqual(plan.get(conn, s["id"])["status"], "published")
        self.assertEqual([c for c in self.api.posts() if c[1] == "me/media_publish"].__len__(), 1)
        self.assertEqual(tg.sent("answerCallbackQuery")[0]["text"], "Published")
        edit = tg.sent("editMessageText")[0]
        self.assertIn("Published", edit["text"])
        self.assertEqual(edit["reply_markup"], {"inline_keyboard": []})

    def test_double_press_publishes_once(self):
        cfg = self.cfg()
        conn = self.conn(cfg)
        s = self.slot(conn)
        tg = self.poll(cfg, conn, [cb(1, f"ig:approve:{s['id']}"), cb(2, f"ig:approve:{s['id']}")])
        self.assertEqual(len([c for c in self.api.posts() if c[1] == "me/media_publish"]), 1)
        answers = [a["text"] for a in tg.sent("answerCallbackQuery")]
        self.assertEqual(answers[0], "Published")
        self.assertIn("Already published", answers[1])

    def test_publish_switch_off(self):
        cfg = self.cfg(publish=False)
        conn = self.conn(cfg)
        s = self.slot(conn)
        tg = self.poll(cfg, conn, [cb(1, f"ig:approve:{s['id']}")])
        self.assertEqual(self.api.calls, [])
        self.assertEqual(plan.get(conn, s["id"])["status"], "ready")
        self.assertIn("Publishing is off", tg.sent("answerCallbackQuery")[0]["text"])

    def test_instagram_module_off(self):
        cfg = self.cfg(ig=False)
        conn = self.conn(cfg)
        s = self.slot(conn)
        tg = self.poll(cfg, conn, [cb(1, f"ig:approve:{s['id']}")])
        self.assertEqual(self.api.calls, [])
        self.assertIn("module is off", tg.sent("answerCallbackQuery")[0]["text"])

    def test_missing_instagram_token(self):
        cfg = self.cfg()
        conn = self.conn(cfg)
        s = self.slot(conn)
        os.environ.pop("KOKPIT_INSTAGRAM_TOKEN")
        tg = self.poll(cfg, conn, [cb(1, f"ig:approve:{s['id']}")])
        self.assertEqual(self.api.calls, [])
        self.assertIn("No Instagram token", tg.sent("answerCallbackQuery")[0]["text"])

    def test_planned_slot_is_not_published(self):
        cfg = self.cfg()
        conn = self.conn(cfg)
        s = self.slot(conn, status="planned")
        tg = self.poll(cfg, conn, [cb(1, f"ig:approve:{s['id']}")])
        self.assertEqual(self.api.calls, [])
        self.assertIn("Already planned", tg.sent("answerCallbackQuery")[0]["text"])

    def test_unknown_slot_and_bad_data(self):
        cfg = self.cfg()
        conn = self.conn(cfg)
        tg = self.poll(cfg, conn, [cb(1, "ig:approve:999"), cb(2, "ig:approve:x"), cb(3, "ig:other:1")])
        answers = [a["text"] for a in tg.sent("answerCallbackQuery")]
        self.assertEqual(answers, ["No such slot", "unknown action", "unknown action"])

    def test_platform_failure_shows_stored_error(self):
        self.api.statuses = ["ERROR"]
        cfg = self.cfg()
        conn = self.conn(cfg)
        s = self.slot(conn)
        tg = self.poll(cfg, conn, [cb(1, f"ig:approve:{s['id']}")])
        self.assertEqual(plan.get(conn, s["id"])["status"], "failed")
        self.assertEqual(tg.sent("answerCallbackQuery")[0]["text"], "Publishing failed")
        self.assertIn("media could not be fetched", tg.sent("editMessageText")[0]["text"])
        self.assertNotIn(IGTOKEN, str(tg.calls))

    def test_foreign_chat_cannot_approve(self):
        cfg = self.cfg()
        conn = self.conn(cfg)
        s = self.slot(conn)
        tg = self.poll(cfg, conn, [cb(1, f"ig:approve:{s['id']}", chat=OTHER)])
        self.assertEqual(self.api.calls, [])
        self.assertEqual(plan.get(conn, s["id"])["status"], "ready")
        self.assertEqual(tg.calls[1:], [])  # only the getUpdates call; nothing answered
        self.assertEqual(store.get(conn, "foreign"), "1")

    def test_not_now_removes_buttons_and_keeps_slot(self):
        cfg = self.cfg()
        conn = self.conn(cfg)
        s = self.slot(conn)
        tg = self.poll(cfg, conn, [cb(1, f"ig:later:{s['id']}")])
        self.assertEqual(self.api.calls, [])
        self.assertEqual(plan.get(conn, s["id"])["status"], "ready")
        self.assertEqual(tg.sent("editMessageReplyMarkup")[0]["reply_markup"], {"inline_keyboard": []})
        self.assertEqual(tg.sent("answerCallbackQuery")[0]["text"], "Left as it is")

    def test_registered_in_the_registry(self):
        self.assertIn("ig", callbacks.REGISTRY)


class RemindTest(BotIgBase):
    def test_ready_slot_reminded_once_with_buttons(self):
        cfg = self.cfg()
        conn = self.conn(cfg)
        s = self.slot(conn, caption="x" * 300)
        tg = FakeTelegram()
        client = Client(TOKEN, tg)
        self.assertEqual(bot_ig.remind(cfg, conn, client, NOW), {"buttons": 1, "plain": 0})
        self.assertEqual(bot_ig.remind(cfg, conn, client, NOW), {"buttons": 0, "plain": 0})
        sent = tg.sent()
        self.assertEqual(len(sent), 1)
        self.assertIn("post, due 2026-03-11 09:00 UTC", sent[0]["text"])
        self.assertIn("Owner: me", sent[0]["text"])
        self.assertIn("Media: 1", sent[0]["text"])
        self.assertIn("x" * 200 + "...", sent[0]["text"])
        self.assertNotIn("x" * 201, sent[0]["text"])
        row = sent[0]["reply_markup"]["inline_keyboard"][0]
        self.assertEqual([b["callback_data"] for b in row], [f"ig:approve:{s['id']}", f"ig:later:{s['id']}"])
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM bot_reminder").fetchone()[0], 1)

    def test_planned_slot_gets_plain_reminder_without_buttons(self):
        cfg = self.cfg()
        conn = self.conn(cfg)
        self.slot(conn, status="planned")
        tg = FakeTelegram()
        client = Client(TOKEN, tg)
        self.assertEqual(bot_ig.remind(cfg, conn, client, NOW), {"buttons": 0, "plain": 1})
        self.assertEqual(bot_ig.remind(cfg, conn, client, NOW), {"buttons": 0, "plain": 0})
        self.assertNotIn("reply_markup", tg.sent()[0])

    def test_slot_outside_window_and_published_are_skipped(self):
        cfg = self.cfg()
        conn = self.conn(cfg)
        self.slot(conn, due="2026-03-20 09:00")
        s = self.slot(conn)
        plan.mark_published(conn, s["id"], "m1", None, NOW)
        tg = FakeTelegram()
        self.assertEqual(bot_ig.remind(cfg, conn, Client(TOKEN, tg), NOW), {"buttons": 0, "plain": 0})
        self.assertEqual(tg.calls, [])

    def test_job_runs_and_respects_switches(self):
        cfg = self.cfg()
        conn = self.conn(cfg)
        self.slot(conn)
        conn.close()
        tg = FakeTelegram()
        self.assertIn("bot-instagram-remind", jobs.REGISTRY)
        ok, info, warns = bot_ig.job_remind(cfg, tg, NOW)
        self.assertEqual(info, {"buttons": 1, "plain": 0})
        self.assertEqual(warns, [])
        off = self.cfg(ig=False)
        self.assertEqual(bot_ig.job_remind(off, tg, NOW)[1], {"enabled": False})

    def test_remind_hours_default(self):
        self.assertEqual(self.cfg().bot["remind_hours"], 24)


class PlanCommandTest(BotIgBase):
    def test_plan_sends_cards_with_buttons_for_ready_slots(self):
        cfg = self.cfg()
        conn = self.conn(cfg)
        a = self.slot(conn)
        self.slot(conn, status="planned", due="2026-03-12 09:00")
        tg = self.poll(cfg, conn, [msg(1, "/plan")])
        sent = tg.sent()
        self.assertEqual(len(sent), 2)  # the list and one card
        self.assertNotIn("reply_markup", sent[0])
        self.assertEqual(sent[1]["reply_markup"]["inline_keyboard"][0][0]["callback_data"], f"ig:approve:{a['id']}")


if __name__ == "__main__":
    unittest.main()
