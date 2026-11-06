"""Kokpit bot capture: /note, /todo, /journal (/j) and the optional plain_text mode. Synthetic vaults only."""
from __future__ import annotations

import unittest
from datetime import datetime

from kokpit import config as cfgmod
from kokpit.modules.bot import commands
from kokpit.modules.brain import api as brain_api
from kokpit.modules.brain import capture as cap
from tests.test_bot import TOKEN, BotBase, FakeTelegram, msg
from kokpit.modules.bot import runner
from kokpit.modules.bot.client import Client


class CaptureWriters(unittest.TestCase):
    def setUp(self):
        import tempfile
        from pathlib import Path
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.vault = Path(self._td.name) / "vault"
        self.vault.mkdir()

    def test_split_due(self):
        self.assertEqual(cap.split_due("call the printer due:2031-05-04"), ("call the printer", "2031-05-04"))
        self.assertEqual(cap.split_due("call (due: 2031-05-04) now"), ("call now", "2031-05-04"))
        self.assertEqual(cap.split_due("  no  date "), ("no date", ""))

    def test_journal_creates_then_appends_only(self):
        p, line = cap.capture_journal(self.vault, "first", datetime(2031, 1, 2, 8, 5))
        self.assertEqual(p.read_text(encoding="utf-8"), "# 2031-01-02\n\n- 08:05 first\n")
        self.assertEqual(line, "- 08:05 first")
        p.write_text(p.read_text(encoding="utf-8") + "typed by hand", encoding="utf-8")
        before = p.read_bytes()
        cap.capture_journal(self.vault, "second", datetime(2031, 1, 2, 9, 0))
        self.assertTrue(p.read_bytes().startswith(before))
        self.assertTrue(p.read_text(encoding="utf-8").endswith("typed by hand\n- 09:00 second\n"))

    def test_journal_rejects_empty_and_escape(self):
        with self.assertRaises(ValueError):
            cap.capture_journal(self.vault, "  ")
        with self.assertRaises(ValueError):
            cap.capture_journal(self.vault, "x", folder="../outside")

    def test_rel_dir_setting(self):
        for bad in ("/abs", "../x", ".hidden", "", "~/x"):
            self.assertEqual(cfgmod._rel_dir(bad, "daily"), "daily", bad)
        self.assertEqual(cfgmod._rel_dir("Journal/days/", "daily"), "Journal/days")


class BotCapture(BotBase):
    def setUp(self):
        super().setUp()
        self.c = self.cfg()
        self.c.vault.mkdir(parents=True, exist_ok=True)

    def test_note_todo_journal(self):
        out = commands.reply(self.c, "/note Spare relay idea")
        self.assertTrue(out.startswith("Saved: inbox/"), out)
        out = commands.reply(self.c, "/todo Call the plumber due:2031-05-04")
        self.assertEqual(out, "Added: - [ ] Call the plumber (due: 2031-05-04)")
        self.assertIn("Call the plumber", (self.c.vault / "To-Do.md").read_text(encoding="utf-8"))
        out = commands.reply(self.c, "/j pump hums at night")
        self.assertRegex(out, r"^Journal daily/\d{4}-\d\d-\d\d\.md: - \d\d:\d\d pump hums at night$")
        out = commands.reply(self.c, "/journal@some_bot second line")
        self.assertIn("second line", out)

    def test_usage_and_missing_vault(self):
        self.assertEqual(commands.reply(self.c, "/note"), commands.USAGE["/note"])
        self.assertEqual(commands.reply(self.c, "/todo due:2031-05-04"), commands.USAGE["/todo"])
        self.assertEqual(commands.reply(self.c, "/j"), commands.USAGE["/journal"])
        self.c.vault.rmdir()
        self.assertTrue(commands.reply(self.c, "/note hi").startswith("Vault not found"))

    def test_help_lists_capture(self):
        for w in ("/note", "/todo", "/journal"):
            self.assertIn(w, commands.HELP)

    def test_journal_dir_from_config(self):
        p = self.tmp / "kokpit.toml"
        p.write_text(p.read_text(encoding="utf-8").replace("[brain]\n", '[brain]\njournal_dir = "log"\n', 1), encoding="utf-8")
        c = cfgmod.load(p)
        self.assertEqual(c.journal_dir, "log")
        self.assertIn("Journal log/", commands.reply(c, "/j hello"))

    def test_brain_api_journal_kind(self):
        r = brain_api.quick_capture(self.c, "journal", "from the dashboard")
        self.assertEqual(r["kind"], "journal")
        self.assertTrue(r["path"].startswith("daily/"))

    def poll(self, cfg, fake):
        conn = self.db(cfg)
        try:
            return runner.poll_once(cfg, conn, Client(TOKEN, fake), 0)
        finally:
            conn.close()

    def plain_cfg(self, mode):
        p = self.tmp / "kokpit.toml"
        text = p.read_text(encoding="utf-8").replace("remind_hours = 24", f'remind_hours = 24\nplain_text = "{mode}"', 1)
        p.write_text(text, encoding="utf-8")
        c = cfgmod.load(p)
        c.vault.mkdir(parents=True, exist_ok=True)
        return c

    def test_plain_text_off_by_default(self):
        self.assertEqual(self.c.bot["plain_text"], "off")
        fake = FakeTelegram([msg(1, "just a thought")])
        self.poll(self.c, fake)
        self.assertEqual(fake.sent()[0]["text"], commands.UNKNOWN)
        self.assertFalse((self.c.vault / "daily").exists())

    def test_plain_text_journal_mode(self):
        c = self.plain_cfg("journal")
        fake = FakeTelegram([msg(1, "just a thought"), msg(2, "/help")])
        self.poll(c, fake)
        out = [p["text"] for p in fake.sent()]
        self.assertTrue(out[0].startswith("Journal daily/"), out)
        self.assertTrue(out[1].startswith("Commands:"))

    def test_plain_text_bad_value_is_off(self):
        self.assertEqual(self.plain_cfg("everything").bot["plain_text"], "off")


if __name__ == "__main__":
    unittest.main()
