"""Tests for plugin 0.4.0: /kokpit setup, the demo hint, card_model, the session log and the morning brief.

Synthetic tmp vaults only. Run: python -m unittest tests.test_hermes_v3
"""
import importlib
import json
import os
import sqlite3
import sys
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests.test_hermes_plugin import P, PLUGIN_DIR, Base, FakeCtx  # noqa: E402

C, T, H, CMD = P.contract, P.tools, P.hooks, P.commands
S = importlib.import_module(P.__name__ + ".vault_setup")
CARD = importlib.import_module(P.__name__ + ".card")
B = importlib.import_module(P.__name__ + ".brief")
D = date.today()
HINT = "Demo data. Make it yours: /kokpit setup"


class Settings(dict):
    """A config that /kokpit setup can write into, like ctx.get_config / ctx.set_config."""

    def get_config(self, key, default=None):
        return self.get(key, default)

    def set_config(self, key, value):
        self[key] = value


class StubLlm:
    def __init__(self, parsed=None, boom=False):
        self.parsed, self.boom, self.calls, self.sent = parsed, boom, 0, []

    def complete_structured(self, **kw):
        self.calls += 1
        self.sent.append(kw["input"][0]["text"])
        assert kw["json_schema"]["type"] == "object"
        if self.boom:
            raise RuntimeError("provider down: token=abc")
        return type("Result", (), {"parsed": self.parsed})()


class SetupTests(Base):
    def setUp(self):
        super().setUp()
        self.home = self.tmp / "home"
        self.home.mkdir()
        self.patches.append(mock.patch.dict(os.environ, {"HOME": str(self.home)}))
        self.patches[-1].start()

    def env_cfg(self, cfg=None, writer=True):
        cfg = cfg if cfg is not None else Settings()
        return C.Env(cfg.get_config, data_dir=self.tmp / "plugin-data",
                     set_config=cfg.set_config if writer else None), cfg

    def test_new_folder_default_path(self):
        env, cfg = self.env_cfg()
        out = CMD.run(env, "setup")
        dest = self.home / "Kokpit"
        self.assertIn("Kokpit vault ready at ~/Kokpit", out)
        for name in ("Threads.md", "To-Do.md", "Last-Session.md", "Welcome.md"):
            self.assertTrue((dest / name).is_file(), name)
        for name in ("inbox", "daily", "news", "notes", "sessions"):
            self.assertTrue((dest / name).is_dir(), name)
        self.assertNotIn("{{", (dest / "To-Do.md").read_text(encoding="utf-8"))
        welcome = (dest / "Welcome.md").read_text(encoding="utf-8")
        for word in ("Threads.md", "To-Do.md", "Last-Session.md", "inbox/", "daily/", "news/", "notes/"):
            self.assertIn(word, welcome)
        self.assertEqual(cfg, {"vault_path": str(dest), "demo": "off"})
        self.assertIn("Saved as plugins.entries.kokpit.settings.vault_path", out)
        self.assertNotIn(str(self.home), out)  # replies show ~, not the home folder

    def test_env_takes_the_vault_without_restart(self):
        env, _cfg = self.env_cfg(writer=False)
        self.assertEqual(env.state()["mode"], "demo")
        CMD.run(env, f"setup {self.tmp / 'mine'}")
        st = env.state()
        self.assertEqual(st["mode"], "live")
        self.assertEqual(Path(st["vault"]), self.tmp / "mine")
        r = self.call(env, "kokpit_capture", {"text": "first own note"})
        self.assertTrue(r["ok"])
        self.assertTrue(list((self.tmp / "mine" / "inbox").glob("*.md")))

    def test_existing_vault_adopted_untouched(self):
        before = {p: p.read_bytes() for p in self.vault.rglob("*") if p.is_file()}
        env, cfg = self.env_cfg()
        out = CMD.run(env, f"setup {self.vault}")
        self.assertIn("existing vault", out)
        self.assertIn("Nothing was changed", out)
        after = {p: p.read_bytes() for p in self.vault.rglob("*") if p.is_file()}
        self.assertEqual(before, after)
        self.assertFalse((self.vault / "Welcome.md").exists())
        self.assertEqual(cfg["vault_path"], str(self.vault))

    def test_no_overwrite_in_a_folder_with_other_files(self):
        dest = self.tmp / "half"
        (dest / "notes").mkdir(parents=True)
        (dest / "Welcome.md").write_text("my own welcome\n", encoding="utf-8")
        (dest / "notes" / "keep.md").write_text("keep me\n", encoding="utf-8")
        env, _cfg = self.env_cfg()
        r = S.run(env, str(dest))
        self.assertTrue(r["ok"])
        self.assertFalse(r["adopted"])
        self.assertNotIn("Welcome.md", r["created"])
        self.assertNotIn("notes/", r["created"])
        self.assertEqual((dest / "Welcome.md").read_text(encoding="utf-8"), "my own welcome\n")
        self.assertEqual((dest / "notes" / "keep.md").read_text(encoding="utf-8"), "keep me\n")
        self.assertTrue((dest / "Threads.md").is_file())
        # a second run adopts it and writes nothing
        again = S.run(env, str(dest))
        self.assertTrue(again["adopted"])
        self.assertEqual(again["created"], [])

    def test_command_printed_when_setting_cannot_be_saved(self):
        env, cfg = self.env_cfg(writer=False)
        out = CMD.run(env, "setup ~/vaults/k")
        self.assertIn("hermes config set plugins.entries.kokpit.settings.vault_path ~/vaults/k", out)
        self.assertIn("not saved", out)
        self.assertEqual(cfg, {})

        def broken(key, value):
            raise PermissionError("/secret/path/config.yaml")
        env2 = C.Env(lambda k, default=None: default, data_dir=self.tmp / "pd2", set_config=broken)
        out2 = CMD.run(env2, f"setup {self.tmp / 'k2'}")
        self.assertIn("PermissionError", out2)
        self.assertNotIn("/secret/path", out2)
        self.assertIn("hermes config set", out2)

    def test_bad_targets_get_a_fix_line(self):
        env, _cfg = self.env_cfg()
        f = self.tmp / "afile.md"
        f.write_text("x", encoding="utf-8")
        out = CMD.run(env, f"setup {f}")
        self.assertIn("is a file, not a folder", out)
        self.assertIn("Fix:", out)
        out = CMD.run(env, "setup bad\x01name")
        self.assertIn("Fix:", out)
        with mock.patch.object(Path, "mkdir", side_effect=PermissionError("denied")):
            out = CMD.run(env, f"setup {self.tmp / 'nope' / 'deeper'}")
        self.assertIn("could not be created (PermissionError)", out)
        self.assertIn("Fix:", out)

    def test_setup_again_reports_current_vault(self):
        env, _cfg = self.env_cfg()
        CMD.run(env, "setup")
        out = CMD.run(env, "setup")
        self.assertIn("already uses your vault at ~/Kokpit", out)

    def test_relative_path_is_under_home(self):
        self.assertEqual(S.target("notes-vault"), self.home / "notes-vault")
        self.assertEqual(S.target(""), self.home / "Kokpit")

    def test_register_passes_the_setter(self):
        class Ctx(FakeCtx):
            def __init__(self):
                super().__init__()
                self.saved = {}

            def set_config(self, key, value):
                self.saved[key] = value

        ctx = Ctx()
        P.register(ctx)
        out = ctx.commands["kokpit"]["handler"](f"setup {self.tmp / 'reg'}")
        self.assertIn("Kokpit vault ready", out)
        self.assertEqual(ctx.saved["vault_path"], str(self.tmp / "reg"))
        self.assertIn("setup", ctx.commands["kokpit"]["args_hint"])

    def test_help_lists_setup_and_brief(self):
        env, _cfg = self.env_cfg()
        out = CMD.run(env, "help")
        self.assertIn("/kokpit setup [path]", out)
        self.assertIn("/kokpit brief", out)
        self.assertNotIn(HINT, out)


class DemoHintTests(Base):
    def test_hint_only_in_demo(self):
        demo = self.env(demo="on")
        live = self.env(vault_path=str(self.vault), demo="off")
        for sub in ("today", "brain", "night", "health", "brief", "brain garden"):
            self.assertTrue(CMD.run(demo, sub).rstrip().endswith(HINT), sub)
            self.assertNotIn(HINT, CMD.run(live, sub), sub)
        self.assertEqual(CMD.run(demo, "brief").count(HINT), 1)

    def test_first_turn_context_hint(self):
        h = H.Hooks(self.env(demo="on"))
        first = h.pre_llm_call(session_id="d1", user_message="hello", is_first_turn=True)
        self.assertTrue(first["context"].endswith(HINT))
        self.assertLessEqual(len(first["context"]), 1200)
        later = h.pre_llm_call(session_id="d1", user_message="irrigation timer", is_first_turn=False)
        self.assertTrue(later is None or HINT not in later["context"])
        live = H.Hooks(self.env(vault_path=str(self.vault), demo="off"))
        r = live.pre_llm_call(session_id="l1", user_message="hello", is_first_turn=True)
        self.assertNotIn(HINT, r["context"])

    def test_hint_respects_small_budget(self):
        h = H.Hooks(self.env(demo="on", context_max_chars=300))
        r = h.pre_llm_call(session_id="d2", user_message="hello", is_first_turn=True)
        self.assertLessEqual(len(r["context"]), 300)
        self.assertTrue(r["context"].endswith(HINT))


class CardModelTests(Base):
    MSGS = ["We decided to water at 06:00 and 19:00 from now on.",
            "Next I will order the spare relay tomorrow morning.",
            "ok thanks"]

    def env_llm(self, llm, **settings):
        s = {"vault_path": str(self.vault), "demo": "off", **settings}
        return C.Env(lambda k, default=None: s.get(k, default), data_dir=self.tmp / "plugin-data", llm=lambda: llm)

    def session(self, h, sid="cm-1", msgs=None):
        for i, m in enumerate(msgs or self.MSGS):
            h.pre_llm_call(session_id=sid, user_message=m, is_first_turn=i == 0)

    def first_card(self):
        text = (self.vault / "Last-Session.md").read_text(encoding="utf-8")
        return text.split("\n## ")[1]

    def test_off_by_default_no_model_call(self):
        llm = StubLlm({"decisions": [], "next": []})
        h = H.Hooks(self.env_llm(llm))
        self.session(h)
        h.on_session_finalize(session_id="cm-1")
        self.assertEqual(llm.calls, 0)
        self.assertIn("without a model", self.first_card())

    def test_kept_and_rejected_quotes(self):
        parsed = {"decisions": [{"text": "Water twice a day", "quote": "water at 06:00 and 19:00"},
                                {"text": "Invented", "quote": "we will buy a new pump"},      # not in messages
                                {"text": "Too short", "quote": "ok"}],                         # under 8 chars
                  "next": [{"text": "Order the spare relay", "quote": "order the spare relay tomorrow"},
                           {"text": "Bad item"}]}                                              # no quote
        llm = StubLlm(parsed)
        h = H.Hooks(self.env_llm(llm, card_model=True))
        self.session(h)
        out = h.on_session_finalize(session_id="cm-1")
        self.assertEqual(llm.calls, 1)
        card = self.first_card()
        self.assertIn('- Decision: Water twice a day ("water at 06:00 and 19:00")', card)
        self.assertIn('- Next: Order the spare relay ("order the spare relay tomorrow")', card)
        self.assertNotIn("Invented", card)
        self.assertNotIn("Too short", card)
        self.assertIn("3 unquoted lines rejected", card)
        self.assertIn("Harness: hermes", card)
        self.assertNotIn("card model", out["message"])

    def test_failure_falls_back_to_rule_card(self):
        llm = StubLlm(boom=True)
        h = H.Hooks(self.env_llm(llm, card_model=True))
        self.session(h)
        out = h.on_session_finalize(session_id="cm-1")
        self.assertIn("card model failed (RuntimeError), rule card used", out["message"])
        self.assertNotIn("token=abc", out["message"])
        card = self.first_card()
        self.assertIn("without a model", card)
        self.assertNotIn("Decision:", card)

    def test_missing_llm_falls_back(self):
        h = H.Hooks(self.env_llm(None, card_model=True))
        self.session(h)
        out = h.on_session_finalize(session_id="cm-1")
        self.assertIn("card model unavailable", out["message"])
        self.assertIn("without a model", self.first_card())

    def test_budget_and_memory_bounds(self):
        big = [f"message {i} " + "x" * 1900 for i in range(40)]
        llm = StubLlm({"decisions": [], "next": []})
        h = H.Hooks(self.env_llm(llm, card_model=True))
        self.session(h, "cm-big", big)
        s = h._s["cm-big"]
        self.assertLessEqual(len(s["msgs"]), CARD.MSG_KEEP)
        self.assertTrue(all(len(m) <= CARD.MSG_MAX for m in s["msgs"]))
        h.on_session_finalize(session_id="cm-big")
        self.assertEqual(llm.calls, 1)
        self.assertLessEqual(len(llm.sent[0]), CARD.SEND_MAX)
        self.assertIn("message 39", llm.sent[0])  # the newest messages are the ones sent

    def test_secret_like_messages_are_not_sent(self):
        llm = StubLlm({"decisions": [], "next": []})
        h = H.Hooks(self.env_llm(llm, card_model=True))
        self.session(h, "cm-s", ["set the api_key = abcdef123456 please", "We decided to keep the relay on GPIO 17."])
        h.on_session_finalize(session_id="cm-s")
        self.assertNotIn("abcdef123456", llm.sent[0])
        self.assertIn("GPIO 17", llm.sent[0])

    def test_check_unit(self):
        kept, rejected = CARD.check({"decisions": [{"text": "a", "quote": "y" * 201}], "next": "nope"}, "y" * 300)
        self.assertEqual(kept, {"decisions": [], "next": []})
        self.assertEqual(rejected, 1)
        self.assertEqual(CARD.check(None, "abc")[1], 1)


class SessionLogTests(Base):
    def env_v(self, **settings):
        return self.env(vault_path=str(self.vault), demo="off", **settings)

    def run_session(self, env, sid, msg):
        h = H.Hooks(env)
        h.pre_llm_call(session_id=sid, user_message=msg, is_first_turn=True)
        h.post_tool_call(tool_name="terminal", args={}, result="{}", session_id=sid)
        return h.on_session_finalize(session_id=sid)

    def test_log_appends_one_line_per_session(self):
        env = self.env_v()
        out = self.run_session(env, "log-1", "Fix the garden timer")
        self.run_session(env, "log-2", "Plan the relay order")
        p = self.vault / "sessions" / f"{datetime.now():%Y-%m}.md"
        self.assertIn(f"session log sessions/{p.name}", out["message"])
        text = p.read_text(encoding="utf-8")
        lines = [x for x in text.splitlines() if x.startswith("- ")]
        self.assertEqual(len(lines), 2)
        self.assertIn("Fix the garden timer · log-1 · 1 turns, 1 tool calls", lines[0])
        self.assertIn("log-2", lines[1])
        self.assertTrue(text.startswith("# Sessions "))

    def test_log_never_rewrites_earlier_lines(self):
        p = self.vault / "sessions" / f"{datetime.now():%Y-%m}.md"
        p.parent.mkdir()
        p.write_text("# My own header\n\n- an earlier line written by hand\n", encoding="utf-8")
        self.run_session(self.env_v(), "log-3", "hello")
        text = p.read_text(encoding="utf-8")
        self.assertTrue(text.startswith("# My own header\n\n- an earlier line written by hand\n- "))

    def test_log_off(self):
        out = self.run_session(self.env_v(session_log=False), "log-4", "hello")
        self.assertFalse((self.vault / "sessions").exists())
        self.assertNotIn("session log", out["message"])

    def test_log_is_searchable(self):
        self.run_session(self.env_v(), "log-5", "Calibrate the moisture sensor")
        r = self.call(self.env_v(), "kokpit_brain_search", {"query": "moisture sensor"})
        self.assertTrue(any(h["path"].startswith("sessions/") for h in r["results"]))

    def test_context_mentions_recent_next_steps(self):
        last = (f"# Last session\n\n## {D} 10:00 · Relay work · s-new\n- Done: wired it.\n"
                f"- Next: order a spare relay\n\n## {D - timedelta(days=1)} 09:00 · Timer · s-old\n"
                f"- Next step: test the 06:00 schedule\n\n## {D - timedelta(days=2)} 09:00 · Older · s-x\n"
                f"- Next: never shown\n")
        (self.vault / "Last-Session.md").write_text(last, encoding="utf-8")
        h = H.Hooks(self.env_v())
        r = h.pre_llm_call(session_id="ctx", user_message="hello", is_first_turn=True)
        block = r["context"]
        self.assertIn("## Next steps from recent sessions", block)
        self.assertIn("order a spare relay", block)
        self.assertIn("test the 06:00 schedule", block)
        self.assertNotIn("never shown", block)
        small = H.Hooks(self.env_v(context_max_chars=200)).pre_llm_call(session_id="c2", user_message="hi",
                                                                         is_first_turn=True)
        self.assertLessEqual(len(small["context"]), 200)


class BriefTests(Base):
    def test_brief_vault_only(self):
        env = self.env(vault_path=str(self.vault), demo="off")
        out = CMD.run(env, "brief")
        self.assertTrue(out.startswith(f"Kokpit brief {D}"))
        self.assertIn("On you (1):", out)
        self.assertIn("pick watering hours", out)
        self.assertIn("Night: no pass yet", out)
        self.assertIn("Last session:", out)
        self.assertNotIn("Calendar", out)   # no database: the section is dropped, not an error
        self.assertNotIn(HINT, out)
        CMD.run(env, "night run")
        out = CMD.run(env, "brief")
        self.assertRegex(out, r"Night \(\d{4}-\d{2}-\d{2}\): ")

    def test_brief_tool_param_and_limit(self):
        env = self.env(demo="on")
        r = self.call(env, "kokpit_today", {"brief": True})
        self.assertTrue(r["ok"])
        self.assertTrue(r["brief"])
        self.assertLess(r["chars"], 3500)
        self.assertIn("Calendar:", r["text"])
        self.assertTrue(r["text"].endswith(HINT))
        plain = self.call(env, "kokpit_today", {})
        self.assertIn("calendar", plain)
        self.assertNotIn("brief", plain)
        self.assertIn("brief", P.schemas.ALL["kokpit_today"]["parameters"]["properties"])

    def test_brief_is_capped(self):
        many = "# Threads\n\n## Active Threads\n\n" + "".join(
            f"### Thread {i}\nWhere we left off ({D}): " + "w" * 300 + f"\nOpen: on you: step {i} " + "z" * 300 + "\n\n"
            for i in range(60))
        (self.vault / "Threads.md").write_text(many, encoding="utf-8")
        st = self.env(vault_path=str(self.vault), demo="off").state()
        with mock.patch.object(B, "LIMIT", 400):
            r = B.build(st)
        self.assertLessEqual(r["chars"], 400)

    def test_brief_with_foreign_db(self):
        conn = sqlite3.connect(self.db)
        conn.execute("CREATE TABLE something_else (x)")
        conn.commit()
        conn.close()
        env = self.env(vault_path=str(self.vault), db_path=str(self.db), demo="off")
        out = CMD.run(env, "brief")
        self.assertIn("Kokpit brief", out)
        self.assertNotIn("Traceback", out)

    def test_brief_cli_registered(self):
        class Ctx(FakeCtx):
            def __init__(self):
                super().__init__()
                self.cli = {}

            def register_cli_command(self, name, help_, setup, handler):
                self.cli[name] = handler

        ctx = Ctx()
        P.register(ctx)
        self.assertEqual(set(ctx.cli), {"kokpit-night", "kokpit-setup", "kokpit-brief"})


if __name__ == "__main__":
    unittest.main()
