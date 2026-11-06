"""Tests for plugin 0.4.0: /kokpit setup, the demo hint, card + receipt on meaningful results and the morning brief.

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
B = importlib.import_module(P.__name__ + ".brief")
D = date.today()
HINT = "Demo data. Make it yours: /kokpit setup"


class Settings(dict):
    """A config that /kokpit setup can write into, like ctx.get_config / ctx.set_config."""

    def get_config(self, key, default=None):
        return self.get(key, default)

    def set_config(self, key, value):
        self[key] = value


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
        for name in ("inbox", "daily", "news", "notes", "receipts"):
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


class ReceiptTests(Base):
    """Card + receipt only on a meaningful result; chat-only sessions write nothing."""

    def env_v(self, **settings):
        env = self.env(vault_path=str(self.vault), demo="off", **settings)
        env.recorder = H.Hooks(env)
        return env

    def receipts(self, vault=None):
        d = (vault or self.vault) / "receipts"
        return sorted(d.glob("*.md")) if d.is_dir() else []

    def meta(self, p):
        return json.loads(p.read_text(encoding="utf-8").split("---\n")[1])

    def test_chat_only_session_writes_nothing(self):
        env = self.env_v()
        h = env.recorder
        before = (self.vault / "Last-Session.md").read_text(encoding="utf-8")
        h.pre_llm_call(session_id="chat-1", user_message="what is on my list", is_first_turn=True)
        h.post_tool_call(tool_name="kokpit_today", args={}, result='{"ok": true}', session_id="chat-1")
        h.post_tool_call(tool_name="terminal", args={"command": "ls"}, result="{}", session_id="chat-1")
        h.post_tool_call(tool_name="write_file", args={"path": "x.txt"}, result="{}", status="error", session_id="chat-1")
        self.assertIsNone(h.on_session_finalize(session_id="chat-1"))
        self.assertEqual((self.vault / "Last-Session.md").read_text(encoding="utf-8"), before)
        self.assertEqual(self.receipts(), [])
        self.assertIsNone(h.on_session_finalize(session_id="never-seen"))

    def test_capture_session_writes_card_and_receipt(self):
        env = self.env_v()
        h = env.recorder
        h.pre_llm_call(session_id="cap-1", user_message="Note the basil idea", is_first_turn=True)
        out = T.handler(env, "kokpit_capture")({"text": "Basil likes morning sun"}, session_id="cap-1")
        h.post_tool_call(tool_name="kokpit_capture", args={}, result=out, session_id="cap-1")
        written = json.loads(out)["written"]
        r = h.on_session_finalize(session_id="cap-1")
        self.assertIn("session card saved", r["message"])
        card = (self.vault / "Last-Session.md").read_text(encoding="utf-8").split("\n## ")[1]
        self.assertIn("Note the basil idea · cap-1", card)
        self.assertIn(f"captured {written}", card)
        self.assertIn("- Open (from Threads.md):", card)
        [rec] = self.receipts()
        m = self.meta(rec)
        self.assertEqual((m["kind"], m["harness"], m["session"]), ("receipt", "hermes", "cap-1"))
        self.assertEqual(m["refs"], ["Last-Session.md", written])
        body = rec.read_text(encoding="utf-8").split("---\n", 2)[2].strip()
        self.assertLessEqual(len(body), H.RECEIPT_MAX)
        self.assertIn("captured 1", body)

    def test_slash_capture_and_accepted_proposal_count(self):
        env = self.env_v()
        h = env.recorder
        CMD.handler(env)("note Relay arrives friday", session_id="sl-1")
        self.assertTrue(h.meaningful(h._s["sl-1"]))
        CMD.handler(env)("night run", session_id="sl-2")
        self.assertFalse(h.meaningful(h._s.get("sl-2")))
        self.assertIsNone(h.on_session_finalize(session_id="sl-2"))
        self.assertIsNotNone(h.on_session_finalize(session_id="sl-1"))
        self.assertEqual(len(self.receipts()), 1)

    def test_done_writes_receipt_with_refs(self):
        env = self.env_v()
        h = env.recorder
        h.pre_llm_call(session_id="dn-1", user_message="Wire the relay", is_first_turn=True)
        h.post_tool_call(tool_name="write_file", args={"path": str(self.vault / "notes" / "relay.md")},
                         result="{}", session_id="dn-1")
        h.post_tool_call(tool_name="patch", args={"path": "/elsewhere/code.py"}, result="{}", session_id="dn-1")
        out = CMD.handler(env)("done Relay wired on GPIO 17, schedule set to 06:00", session_id="dn-1")
        self.assertTrue(out.startswith("Receipt saved: receipts/"), out)
        self.assertIn("Refs: Last-Session.md, notes/relay.md", out)
        self.assertNotIn("elsewhere", out)  # files outside the vault are not notes
        [rec] = self.receipts()
        m = self.meta(rec)
        self.assertEqual(m["refs"], ["Last-Session.md", "notes/relay.md"])
        self.assertTrue(m["event_id"].startswith("hermes-done-dn-1-"))
        self.assertIn("Relay wired on GPIO 17", rec.read_text(encoding="utf-8"))
        card = (self.vault / "Last-Session.md").read_text(encoding="utf-8").split("\n## ")[1]
        self.assertIn("- Done: Relay wired on GPIO 17, schedule set to 06:00", card)
        # finalize after done, with nothing new: card kept, no second receipt
        r = h.on_session_finalize(session_id="dn-1")
        self.assertIn("unchanged", r["message"])
        self.assertEqual(len(self.receipts()), 1)

    def test_done_usage_secret_and_length(self):
        env = self.env_v()
        self.assertEqual(CMD.handler(env)("done", session_id="u-1"), "Usage: " + CMD.USAGE["done"])
        self.assertEqual(CMD.handler(env)("done    ", session_id="u-1"), "Usage: " + CMD.USAGE["done"])
        out = CMD.handler(env)("done set api_key = abcdef123456", session_id="u-1")
        self.assertIn("Fix:", out)
        self.assertEqual(self.receipts(), [])
        out = CMD.handler(env)("done " + "long " * 200, session_id="u-2")
        [rec] = self.receipts()
        body = rec.read_text(encoding="utf-8").split("---\n", 2)[2].strip()
        self.assertLessEqual(len(body), H.RECEIPT_MAX)
        card = (self.vault / "Last-Session.md").read_text(encoding="utf-8").split("\n## ")[1]
        self.assertLessEqual(len(card), C.CARD_MAX + 3)

    def test_done_is_idempotent_per_session(self):
        env = self.env_v()
        cmd = CMD.handler(env)
        cmd("done First result", session_id="id-1")
        again = cmd("done First result", session_id="id-1")
        self.assertIn("already saved", again)
        cmd("done Second result", session_id="id-1")
        self.assertEqual(len(self.receipts()), 2)
        text = (self.vault / "Last-Session.md").read_text(encoding="utf-8")
        self.assertEqual(text.count("· id-1\n"), 1)  # one card per session id
        card = text.split("\n## ")[1]
        self.assertIn("- Done: First result", card)
        self.assertIn("- Done: Second result", card)

    def test_capture_tool_receipt_kind(self):
        env = self.env_v()
        r = json.loads(T.handler(env, "kokpit_capture")({"kind": "receipt", "text": "Decided: water at 06:00"},
                                                        session_id="tk-1"))
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["kind"], "receipt")
        self.assertEqual(r["refs"], ["Last-Session.md"])
        r = json.loads(T.handler(env, "kokpit_capture")({"kind": "receipt", "text": ""}, session_id="tk-1"))
        self.assertFalse(r["ok"])
        self.assertIn("usage", r["fix"])
        bare = self.env(vault_path=str(self.vault), demo="off")  # no hooks registered
        r = json.loads(T.handler(bare, "kokpit_capture")({"kind": "receipt", "text": "x"}))
        self.assertFalse(r["ok"])
        self.assertIn("/kokpit done", r["fix"])

    def test_receipt_needs_a_vault(self):
        env = self.env(vault_path=str(self.tmp / "missing"), demo="off")
        env.recorder = H.Hooks(env)
        out = CMD.handler(env)("done Something", session_id="mv-1")
        self.assertIn("Fix:", out)
        self.assertNotIn("Traceback", out)
        self.assertFalse((self.tmp / "missing").exists())

    def test_demo_writes_only_into_demo_copy(self):
        env = self.env(demo="on")
        env.recorder = H.Hooks(env)
        before = sorted(p.relative_to(C.DEMO_SRC).as_posix() for p in C.DEMO_SRC.rglob("*"))
        out = CMD.handler(env)("done Tried the demo", session_id="demo-1")
        self.assertIn("demo", out)
        demo_vault = env.state()["vault"]
        self.assertTrue(str(demo_vault).startswith(str(self.tmp / "plugin-data")))
        self.assertEqual(len(self.receipts(Path(demo_vault))), 1)
        self.assertEqual(sorted(p.relative_to(C.DEMO_SRC).as_posix() for p in C.DEMO_SRC.rglob("*")), before)
        self.assertEqual(self.receipts(), [])
        self.assertNotIn("demo-1", (self.vault / "Last-Session.md").read_text(encoding="utf-8"))

    def test_context_shows_open_items_and_latest_receipt(self):
        last = (f"# Last session\n\n## {D} 10:00 · Relay work · s-new\n- Done: wired it.\n"
                f"- Open: order a spare relay\n- Next: test the 06:00 schedule\n\n"
                f"## {D - timedelta(days=1)} 09:00 · Older · s-old\n- Open: never shown\n")
        (self.vault / "Last-Session.md").write_text(last, encoding="utf-8")
        env = self.env_v()
        CMD.handler(env)("done Relay ordered from the shop", session_id="rc-1")
        (self.vault / "Last-Session.md").write_text(last, encoding="utf-8")  # keep the fixture card on top
        h = H.Hooks(env)
        block = h.pre_llm_call(session_id="ctx", user_message="hello", is_first_turn=True)["context"]
        self.assertIn("## Last session card (historical claim, not verified)", block)
        self.assertIn("order a spare relay", block)
        self.assertIn("test the 06:00 schedule", block)
        self.assertNotIn("never shown", block)
        self.assertIn("## Latest receipt (historical agent claim, not verified)", block)
        self.assertIn("Relay ordered from the shop", block)
        r = C.latest_receipt(self.vault / "receipts")
        self.assertEqual(r["at"][:13], datetime.now().strftime("%Y-%m-%d %H")[:13])  # local time, like card headings
        self.assertLess(block.index("## Latest receipt"), block.index("## Active threads"))  # cut last by the budget
        self.assertNotIn("Harness", block)
        self.assertLessEqual(len(block), 1200)
        later = h.pre_llm_call(session_id="ctx", user_message="relay", is_first_turn=False)
        self.assertTrue(later is None or "Latest receipt" not in later["context"])
        for budget in (120, 200, 400):
            small = H.Hooks(self.env_v(context_max_chars=budget)).pre_llm_call(
                session_id=f"b{budget}", user_message="hi", is_first_turn=True)
            self.assertTrue(small is None or len(small["context"]) <= budget, budget)

    def test_no_message_buffer_kept(self):
        h = H.Hooks(self.env_v())
        for i in range(30):
            h.pre_llm_call(session_id="mem", user_message=f"message {i} " + "x" * 1900, is_first_turn=i == 0)
        s = h._s["mem"]
        self.assertNotIn("msgs", s)
        self.assertLessEqual(len(s["first"]), 200)

    def test_removed_settings_and_files(self):
        y = (PLUGIN_DIR / "plugin.yaml").read_text(encoding="utf-8")
        self.assertNotIn("card_model", y)
        self.assertNotIn("session_log", y)
        self.assertFalse((PLUGIN_DIR / "card.py").exists())
        self.assertFalse(hasattr(C, "append_session_log"))


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

    def test_missing_vault_brief_and_today_have_fix(self):
        env = self.env(vault_path=str(self.tmp / "nope"), demo="off")
        out = CMD.run(env, "brief")
        self.assertIn("Vault: not found. Fix: run /kokpit setup", out)
        self.assertNotIn("Act:", out)
        self.assertNotIn("no pass yet", out)
        self.assertIn("Tasks: unknown (vault not found). Fix: run /kokpit setup", CMD.run(env, "today"))
        self.assertFalse((self.tmp / "nope").exists())

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
