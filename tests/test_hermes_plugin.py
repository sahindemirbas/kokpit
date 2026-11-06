"""Tests for the kokpit Hermes plugin (integrations/hermes/kokpit) on synthetic data only.

Run: python -m unittest tests.test_hermes_plugin
"""
import importlib.util
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
PLUGIN_DIR = ROOT / "integrations" / "hermes" / "kokpit"


def load_plugin():
    """Import the plugin folder under its own name (the repo already has a 'kokpit' package)."""
    name = "kokpit_hermes_plugin"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, PLUGIN_DIR / "__init__.py",
                                                  submodule_search_locations=[str(PLUGIN_DIR)])
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


P = load_plugin()
C, T, H, CMD = P.contract, P.tools, P.hooks, P.commands
D = date.today()

THREADS = f"""# Threads

## Active Threads

### Garden timer
Where we left off ({D - timedelta(days=1)}): relay wired, schedule not set.
Open: on you: pick watering hours; on me: write the cron line (due: {D + timedelta(days=2)})
Source: [[notes/garden-timer]]
"""
LAST = f"""# Last session

## {D - timedelta(days=1)} 21:10 · Garden timer: relay test · old-session
Result: relay clicks on GPIO 17.
"""
TODO = f"""# To-Do

## Open
- [ ] Order a spare relay (due: {D - timedelta(days=1)})

## Done
- [x] Solder the header
"""
TODO_TR = f"""# Yapılacaklar

## Açık
- [ ] Prune the tomatoes (due: {D + timedelta(days=1)})
- [ ] Buy seeds

## Bitti
- [ ] Old unticked item that is already done
- [x] Order soil
"""
NOTE = "# Garden timer\n\nThe relay sits on GPIO 17. Watering hours are still open.\n"


class FakeCtx:
    def __init__(self):
        self.tools, self.hooks, self.commands = {}, {}, {}

    def get_config(self, key, default=None):
        return default

    def register_tool(self, name, toolset, schema, handler, **kw):
        self.tools[name] = {"toolset": toolset, "schema": schema, "handler": handler, **kw}

    def register_hook(self, name, fn):
        self.hooks.setdefault(name, []).append(fn)

    def register_command(self, name, handler, **kw):
        self.commands[name] = {"handler": handler, **kw}


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.vault = self.tmp / "vault"
        (self.vault / "notes").mkdir(parents=True)
        (self.vault / "Threads.md").write_text(THREADS, encoding="utf-8")
        (self.vault / "Last-Session.md").write_text(LAST, encoding="utf-8")
        (self.vault / "To-Do.md").write_text(TODO, encoding="utf-8")
        (self.vault / "notes" / "garden-timer.md").write_text(NOTE, encoding="utf-8")
        self.db = self.tmp / "kokpit.db"
        # no real config, no KOKPIT_CONFIG from the machine running the tests
        self.patches = [mock.patch.object(C, "DEFAULT_LOCATIONS", []),
                        mock.patch.dict(os.environ, {"KOKPIT_CONFIG": ""})]
        for p in self.patches:
            p.start()
        C._FOUND.clear()

    def tearDown(self):
        for p in self.patches:
            p.stop()

    def config(self, modules=None, extra=""):
        mods = {"today": True, "brain": True, "health": True, "events": True, "jobs": True, "ai_usage": True}
        mods.update(modules or {})
        lines = ["[modules]"] + [f"{k} = {'true' if v else 'false'}" for k, v in mods.items()]
        lines += ["", "[brain]", f'vault_path = "{self.vault}"', "", "[health]", f'db_path = "{self.db}"',
                  'expected = ["weather", "career", "backup"]', extra]
        p = self.tmp / "kokpit.toml"
        p.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return p

    def env(self, **settings):
        return C.Env(lambda k, default=None: settings.get(k, default), data_dir=self.tmp / "plugin-data")

    def live_env(self, **kw):
        settings = {"kokpit_config": str(self.config(**kw)), "demo": "off"}
        return self.env(**settings)

    def call(self, env, name, args=None):
        out = T.handler(env, name)(args or {})
        self.assertIsInstance(out, str)
        return json.loads(out)

    def make_db(self):
        conn = sqlite3.connect(self.db)
        conn.executescript(json.loads((PLUGIN_DIR / "demo" / "seed.json").read_text(encoding="utf-8"))["schema"])
        return conn


class Registration(unittest.TestCase):
    def test_register_matches_manifest(self):
        ctx = FakeCtx()
        P.register(ctx)
        manifest = (PLUGIN_DIR / "plugin.yaml").read_text(encoding="utf-8")
        tools = [l.strip()[2:] for l in manifest.split("provides_tools:")[1].split("provides_hooks:")[0].splitlines() if l.strip()]
        hooks = [l.strip()[2:] for l in manifest.split("provides_hooks:")[1].split("config_schema:")[0].splitlines() if l.strip()]
        self.assertEqual(sorted(ctx.tools), sorted(tools))
        self.assertEqual(len(ctx.tools), 11)
        self.assertEqual(sorted(ctx.hooks), sorted(hooks))
        self.assertEqual(list(ctx.commands), ["kokpit"])
        self.assertTrue(all(t["toolset"] == "kokpit" for t in ctx.tools.values()))
        self.assertIn('version: "0.3.0"', manifest)
        for name, t in ctx.tools.items():
            self.assertEqual(t["schema"]["name"], name)
            self.assertEqual(t["schema"]["parameters"]["type"], "object")

    def test_only_capture_writes(self):
        writers = [n for n, s in P.schemas.ALL.items() if "append" in s["description"].lower() or "write" in s["description"].lower()]
        # kokpit_night writes only when the user accepts a proposal, through the capture writers
        self.assertEqual(writers, ["kokpit_capture", "kokpit_night"])
        self.assertIn("accept", P.schemas.ALL["kokpit_night"]["description"].lower())

    def test_stdlib_only_no_network(self):
        banned = ("import requests", "urllib.request", "http.client", "socket", "subprocess", "sys.modules",
                  "from kokpit", "import kokpit", "httpx")
        # the two reviewed exceptions: the dashboard API registers its private package name, and
        # the server module runs `systemctl --user is-active <validated name>` with a list argv
        allowed = {"dashboard/plugin_api.py": {"sys.modules"}, "server.py": {"subprocess"}}
        for p in PLUGIN_DIR.rglob("*.py"):
            rel = p.relative_to(PLUGIN_DIR).as_posix()
            text = p.read_text(encoding="utf-8")
            for b in banned:
                if b not in allowed.get(rel, set()):
                    self.assertNotIn(b, text, f"{rel} contains {b}")


class Demo(Base):
    def test_auto_demo_without_config(self):
        env = self.env()
        for name in T.TOOLS:
            args = {"query": "irrigation"} if name == "kokpit_brain_search" else {"text": "demo note"} if name == "kokpit_capture" else {}
            r = self.call(env, name, args)
            self.assertTrue(r["ok"], (name, r))
            self.assertEqual(r["mode"], "demo")
        r = self.call(env, "kokpit_ai_usage")
        self.assertEqual(r["total_cost_usd"], "unknown")
        self.assertIn("demo-unpriced", r["unpriced_models"])
        unknown = [x for x in r["rows"] if x["model"] == "demo-unpriced"][0]
        self.assertEqual(unknown["cost_usd"], "unknown")
        priced = [x for x in r["rows"] if x["model"] == "demo-small"][0]
        self.assertAlmostEqual(priced["cost_usd"], 9000 * 0.5 / 1e6 + 1500 * 2.0 / 1e6, places=6)

    def test_demo_dates_are_relative(self):
        r = self.call(self.env(demo="on"), "kokpit_today")
        self.assertEqual(r["date"], D.isoformat())
        self.assertTrue(any(e["date"] == D.isoformat() for e in r["calendar"]["events"]))
        text = (self.tmp / "plugin-data" / "demo" / "vault" / "To-Do.md").read_text(encoding="utf-8")
        self.assertNotIn("{{", text)
        self.assertIn((D + timedelta(days=1)).isoformat(), text)

    def test_demo_health_shows_problems(self):
        r = self.call(self.env(demo="on"), "kokpit_health")
        joined = " ".join(r["problems"])
        self.assertIn("backup: never run", joined)
        self.assertIn("career: failed 2 times in a row", joined)
        self.assertIn("events-collect: last run produced 0 records", joined)

    def test_demo_off_without_config_explains(self):
        r = self.call(self.env(demo="off"), "kokpit_today")
        self.assertFalse(r["ok"])
        self.assertEqual(r["error"], "kokpit not configured")
        self.assertTrue(r["fix"])


class Live(Base):
    def test_missing_db_says_never_run(self):
        env = self.live_env()
        r = self.call(env, "kokpit_events")
        self.assertTrue(r["ok"])
        self.assertEqual(r["status"], "never run")
        self.assertEqual(r["events"], [])
        r = self.call(env, "kokpit_jobs")
        self.assertEqual(r["status"], "never run")
        r = self.call(env, "kokpit_health")
        self.assertEqual(r["db"], "missing")
        self.assertIn("weather: never run", r["problems"])
        r = self.call(env, "kokpit_today")
        self.assertEqual(r["calendar"]["status"], "never run")
        self.assertEqual(r["weather"]["status"], "never run")
        self.assertEqual(r["tasks"]["open"], 1)
        self.assertEqual(r["tasks"]["overdue"], 1)

    def test_module_disabled(self):
        env = self.live_env(modules={"events": False, "jobs": False, "ai_usage": False})
        for name, mod in (("kokpit_events", "events"), ("kokpit_jobs", "jobs"), ("kokpit_ai_usage", "ai_usage")):
            r = self.call(env, name)
            self.assertFalse(r["ok"])
            self.assertEqual(r["error"], "module disabled")
            self.assertIn(f"enable with [modules] {mod} = true", r["fix"])

    def test_broken_config(self):
        p = self.tmp / "broken.toml"
        p.write_text("[modules\nevents = ", encoding="utf-8")
        r = self.call(self.env(kokpit_config=str(p)), "kokpit_today")
        self.assertFalse(r["ok"])
        self.assertIn("could not be read", r["error"])
        r = self.call(self.env(kokpit_config=str(self.tmp / "nope.toml")), "kokpit_today")
        self.assertFalse(r["ok"])
        self.assertIn("not found", r["error"])

    def test_missing_vault(self):
        env = self.env(kokpit_config=str(self.config()), vault_path=str(self.tmp / "no-vault"))
        r = self.call(env, "kokpit_threads")
        self.assertFalse(r["ok"])
        self.assertIn("vault not found", r["error"])

    def test_jobs_unknown_score_and_reasons(self):
        conn = self.make_db()
        now = D.isoformat()
        conn.execute("INSERT INTO job_listings (source, external_id, title, status, first_seen, last_seen, score, reasons) "
                     "VALUES ('t','1','Analyst','open',?,?,75,?)",
                     (now, now, json.dumps({"reasons": [{"kind": "skills", "effect": "+", "text": "SQL"}], "unknowns": ["salary"]})))
        conn.execute("INSERT INTO job_listings (source, external_id, title, status, first_seen, last_seen, score, reasons) "
                     "VALUES ('t','2','Engineer','open',?,?,NULL,'[]')", (now, now))
        conn.commit()
        conn.close()
        r = self.call(self.live_env(), "kokpit_jobs")
        self.assertTrue(r["ok"])
        self.assertEqual(r["listings"][0]["score"], 75)
        self.assertEqual(r["listings"][0]["reasons"], ["+ skills: SQL"])
        self.assertEqual(r["listings"][0]["unknowns"], ["salary"])
        self.assertEqual(r["listings"][1]["score"], "unknown")
        self.assertEqual(r["freshness"]["last_status"], "never run")

    def test_ai_usage_without_prices_is_unknown(self):
        conn = self.make_db()
        conn.execute("INSERT INTO ai_usage_hours (tool, model_raw, model, session_hash, bucket, input_tokens, output_tokens) "
                     "VALUES ('hermes','m','m','abc',?,100,10)", (D.isoformat() + "T08:00:00",))
        conn.commit()
        conn.close()
        r = self.call(self.live_env(), "kokpit_ai_usage")
        self.assertEqual(r["rows"][0]["cost_usd"], "unknown")
        self.assertEqual(r["total_cost_usd"], "unknown")
        self.assertEqual(r["known_cost_usd"], 0)

    def test_health_zero_records(self):
        conn = self.make_db()
        conn.execute("INSERT INTO runs (day, job, finished, ok, rows, info, warnings) VALUES (?, 'weather', ?, 1, 0, '{}', '[]')",
                     (D.isoformat(), D.isoformat() + "T06:00:00"))
        conn.commit()
        conn.close()
        r = self.call(self.live_env(), "kokpit_health")
        self.assertIn("weather: last run produced 0 records", r["problems"])
        self.assertIn("career: never run", r["problems"])
        self.assertEqual(r["never_run"], ["career", "backup"])

    def test_db_is_opened_read_only(self):
        self.make_db().close()
        before = self.db.read_bytes()
        env = self.live_env()
        for name in ("kokpit_today", "kokpit_events", "kokpit_jobs", "kokpit_ai_usage", "kokpit_health"):
            self.assertTrue(self.call(env, name)["ok"], name)
        self.assertEqual(self.db.read_bytes(), before)

    def test_search_returns_source_paths(self):
        r = self.call(self.live_env(), "kokpit_brain_search", {"query": "relay GPIO"})
        self.assertTrue(r["ok"])
        self.assertIn("notes/garden-timer.md", [h["path"] for h in r["results"]])
        r = self.call(self.live_env(), "kokpit_brain_search", {"query": ""})
        self.assertFalse(r["ok"])

    def test_threads(self):
        r = self.call(self.live_env(), "kokpit_threads")
        self.assertEqual(r["threads"][0]["title"], "Garden timer")
        self.assertEqual(r["on_you"][0]["text"], "pick watering hours")
        self.assertEqual(r["last_session"]["title"], "Garden timer: relay test")

    def test_threads_empty_values_and_owner_notes(self):
        due = (D + timedelta(days=4)).isoformat()
        (self.vault / "Threads.md").write_text(
            "## Active Threads\n\n### Project X\nNerede kaldık: vendor access blocked. Açık: Sende: yok; "
            "Bende (Fri): send the pilot plan; Başkası: Vendor; on Vendor team (" + due + "): sign the NDA.\n"
            "Kaynak: [[notes/project-x]].\n\n### Quiet one\nWhere we left off: waiting... Open: on you: None.; "
            "on me: -; Başkası: n/a\n", encoding="utf-8")
        r = self.call(self.live_env(), "kokpit_threads")
        px, quiet = r["threads"]
        self.assertEqual(px["status"], "vendor access blocked")
        self.assertEqual(quiet["status"], "waiting...")
        self.assertEqual(quiet["open"], [])
        self.assertEqual(r["on_you"], [])
        self.assertEqual([(o["owner"], o["note"], o["text"]) for o in px["open"]],
                         [("me", "Fri", "send the pilot plan"), ("someone", "", "Vendor"),
                          ("vendor", due, "sign the NDA")])
        self.assertEqual([o["due"] for o in px["open"]], ["", "", due])
        self.assertFalse(any(o["on_you"] for o in px["open"]))
        self.assertIn({"date": due, "text": "sign the NDA", "from": "Project X"},
                      [{k: u[k] for k in ("date", "text", "from")} for u in self.call(self.live_env(), "kokpit_today")["upcoming"]])


class Capture(Base):
    def test_capture_task_is_append_only(self):
        env = self.live_env()
        before = (self.vault / "To-Do.md").read_text(encoding="utf-8")
        r = self.call(env, "kokpit_capture", {"kind": "task", "text": "Buy seeds", "due": (D + timedelta(days=3)).isoformat()})
        self.assertTrue(r["ok"])
        after = (self.vault / "To-Do.md").read_text(encoding="utf-8")
        for line in before.splitlines():
            self.assertIn(line, after.splitlines())
        self.assertIn("- [ ] Buy seeds (due:", after.split("## Done")[0])
        self.assertEqual(len(after.splitlines()), len(before.splitlines()) + 1)

    def test_capture_note_new_file_each_time(self):
        env = self.live_env()
        a = self.call(env, "kokpit_capture", {"text": "Same title"})
        b = self.call(env, "kokpit_capture", {"text": "Same title"})
        self.assertNotEqual(a["written"], b["written"])
        self.assertTrue(a["written"].startswith("inbox/"))
        self.assertTrue((self.vault / a["written"]).is_file())

    def test_capture_rejects_bad_input(self):
        env = self.live_env()
        for args in ({"text": ""}, {"kind": "task", "text": "x", "due": "tomorrow"}, {"kind": "other", "text": "x"},
                     {"text": "x" * 2001}):
            r = self.call(env, "kokpit_capture", args)
            self.assertFalse(r["ok"], args)

    def test_capture_off(self):
        env = self.live_env(extra="")
        cfg = (self.tmp / "kokpit.toml").read_text(encoding="utf-8").replace("[brain]\n", "[brain]\nquick_capture = false\n")
        (self.tmp / "kokpit.toml").write_text(cfg, encoding="utf-8")
        r = self.call(env, "kokpit_capture", {"text": "x"})
        self.assertFalse(r["ok"])
        self.assertIn("quick_capture", r["fix"])


class Hooks(Base):
    def test_pre_llm_call_budget_and_turns(self):
        h = H.Hooks(self.env(kokpit_config=str(self.config()), context_max_chars=300))
        r = h.pre_llm_call(session_id="s1", user_message="garden timer relay", is_first_turn=True)
        self.assertLessEqual(len(r["context"]), 300)
        self.assertIn("Garden timer", r["context"])
        later = h.pre_llm_call(session_id="s1", user_message="garden timer relay", is_first_turn=False)
        self.assertNotIn("## Active threads", later["context"])
        self.assertIn("notes/garden-timer.md", later["context"])
        self.assertIsNone(h.pre_llm_call(session_id="s1", user_message="weather in space", is_first_turn=False))
        h.post_compaction(session_id="s1")
        again = h.pre_llm_call(session_id="s1", user_message="hello", is_first_turn=False)
        self.assertIn("Active threads", again["context"])

    def test_pre_llm_call_off_and_never_raises(self):
        h = H.Hooks(self.env(kokpit_config=str(self.config()), context_max_chars=0))
        self.assertIsNone(h.pre_llm_call(session_id="s", user_message="garden", is_first_turn=True))
        h = H.Hooks(self.env(kokpit_config=str(self.tmp / "missing.toml")))
        self.assertIsNone(h.pre_llm_call(session_id="s", user_message="garden", is_first_turn=True))

    def test_session_card_and_receipt(self):
        h = H.Hooks(self.live_env())
        h.pre_llm_call(session_id="sess-42", user_message="Fix the garden timer schedule", is_first_turn=True)
        h.post_tool_call(tool_name="write_file", args={"path": "/tmp/garden/cron.txt"}, result="{}", session_id="sess-42")
        h.post_tool_call(tool_name="terminal", args={"command": "ls"}, result="{}", session_id="sess-42")
        h.post_tool_call(tool_name="terminal", args={"command": "ls"}, result="{}", session_id="sess-42")
        out = h.on_session_finalize(session_id="sess-42", platform="cli")
        self.assertIn("session card saved", out["message"])
        text = (self.vault / "Last-Session.md").read_text(encoding="utf-8")
        first = text.split("\n## ")[1]
        self.assertTrue(first.endswith("\n") or first)
        self.assertIn("Fix the garden timer schedule · sess-42", first)
        self.assertIn("Harness: hermes", first)
        self.assertIn("terminal x2", first)
        self.assertIn("/tmp/garden/cron.txt", first)
        self.assertIn("pick watering hours", first)
        self.assertIn("Garden timer: relay test · old-session", text)  # older card kept
        receipts = list((self.vault / "receipts").glob("*.md"))
        self.assertEqual(len(receipts), 1)
        meta = json.loads(receipts[0].read_text(encoding="utf-8").split("---\n")[1])
        self.assertEqual(meta["kind"], "receipt")
        self.assertEqual(meta["harness"], "hermes")
        self.assertEqual(meta["session"], "sess-42")
        self.assertIn("Last-Session.md", meta["refs"])
        # a second finalize without new turns writes nothing
        self.assertIsNone(h.on_session_finalize(session_id="sess-42"))

    def test_card_hides_secret_looking_first_message(self):
        h = H.Hooks(self.live_env())
        h.pre_llm_call(session_id="s9", user_message="my password: abc123 please store", is_first_turn=True)
        h.on_session_finalize(session_id="s9")
        text = (self.vault / "Last-Session.md").read_text(encoding="utf-8")
        self.assertNotIn("abc123", text)
        self.assertIn("Hermes session · s9", text)

    def test_session_card_off(self):
        h = H.Hooks(self.env(kokpit_config=str(self.config()), session_card=False))
        h.pre_llm_call(session_id="s2", user_message="hi", is_first_turn=True)
        self.assertIsNone(h.on_session_finalize(session_id="s2"))
        self.assertEqual((self.vault / "Last-Session.md").read_text(encoding="utf-8"), LAST)
        self.assertFalse((self.vault / "receipts").exists())


class VaultLayout(Base):
    """Real vaults keep the files in folders, sometimes with emoji or non-ASCII names."""

    def nest(self):
        for n in ("Threads.md", "To-Do.md", "Last-Session.md"):
            (self.vault / n).unlink()
        comp = self.vault / "🧭 Companion"
        tasks = self.vault / "notes" / "Görevler"
        comp.mkdir()
        tasks.mkdir(parents=True)
        (comp / "Threads.md").write_text(THREADS, encoding="utf-8")
        (comp / "Last-Session.md").write_text(LAST, encoding="utf-8")
        (tasks / "To-Do.md").write_text(TODO_TR, encoding="utf-8")
        return comp, tasks

    def nested_env(self, **settings):
        return self.env(kokpit_config=str(self.config()), demo="off", **settings)

    def test_discovers_single_match_in_unicode_folders(self):
        comp, tasks = self.nest()
        env = self.nested_env()
        r = self.call(env, "kokpit_threads")
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["threads"][0]["title"], "Garden timer")
        self.assertEqual(r["source"], "🧭 Companion/Threads.md")
        r = self.call(env, "kokpit_today")
        self.assertEqual(r["tasks"]["open"], 2)  # Bitti section is skipped
        r = self.call(env, "kokpit_health")
        vf = r["vault_files"]
        self.assertEqual(vf["threads"], {"file": "🧭 Companion/Threads.md", "found": True, "how": "discovered"})
        self.assertEqual(vf["todo"]["file"], "notes/Görevler/To-Do.md")
        self.assertEqual(vf["last_session"]["how"], "discovered")
        self.assertIn("Vault files: 🧭 Companion/Threads.md (found by search)", CMD.run(env, "health"))
        r = self.call(env, "kokpit_capture", {"kind": "task", "text": "Water the basil"})
        self.assertEqual(r["written"], "notes/Görevler/To-Do.md")
        text = (tasks / "To-Do.md").read_text(encoding="utf-8")
        self.assertLess(text.index("Water the basil"), text.index("## Bitti"))
        self.assertFalse((self.vault / "To-Do.md").exists())

    def test_ambiguous_match_asks_for_the_setting(self):
        comp, _ = self.nest()
        (self.vault / "notes" / "old").mkdir()
        (self.vault / "notes" / "old" / "Threads.md").write_text(THREADS, encoding="utf-8")
        env = self.nested_env()
        r = self.call(env, "kokpit_threads")
        self.assertFalse(r["ok"])
        self.assertIn("ambiguous", r["error"])
        self.assertIn("threads_file", r["fix"])
        r = self.call(env, "kokpit_health")
        self.assertEqual(r["vault_files"]["threads"]["how"], "ambiguous")
        self.assertEqual(len(r["vault_files"]["threads"]["candidates"]), 2)
        self.assertTrue(any(p.startswith("vault: several Threads.md") for p in r["problems"]))
        r = self.call(self.nested_env(threads_file="🧭 Companion/Threads.md"), "kokpit_threads")
        self.assertTrue(r["ok"])
        self.assertEqual(r["source"], "🧭 Companion/Threads.md")

    def test_settings_relative_and_absolute(self):
        comp, tasks = self.nest()
        abs_todo = self.tmp / "elsewhere" / "Tasks.md"
        abs_todo.parent.mkdir()
        abs_todo.write_text("# Tasks\n\n## To do\n- [ ] Elsewhere task\n", encoding="utf-8")
        env = self.nested_env(threads_file="🧭 Companion/Threads.md", todo_file=str(abs_todo),
                              last_session_file="🧭 Companion/Last-Session.md", inbox_dir="📥 Gelen",
                              receipts_dir="🧭 Companion/receipts")
        r = self.call(env, "kokpit_today")
        self.assertEqual([t["text"] for t in r["tasks"]["next"]], ["Elsewhere task"])
        r = self.call(env, "kokpit_capture", {"text": "Basil likes morning sun"})
        self.assertTrue(r["written"].startswith("📥 Gelen/"), r)
        r = self.call(env, "kokpit_brain_search", {"query": "basil morning"})
        self.assertTrue(r["results"] and r["results"][0]["path"].startswith("📥 Gelen/"))
        r = self.call(env, "kokpit_health")
        self.assertEqual(r["vault_files"]["todo"], {"file": str(abs_todo), "found": True, "how": "setting"})
        h = H.Hooks(env)
        h.pre_llm_call(session_id="s-1", user_message="plan the garden", is_first_turn=True)
        msg = h.on_session_finalize(session_id="s-1")
        self.assertIn("🧭 Companion/Last-Session.md", msg["message"])
        self.assertIn("· s-1", (comp / "Last-Session.md").read_text(encoding="utf-8"))
        self.assertEqual(len(list((comp / "receipts").glob("*.md"))), 1)
        self.assertFalse((self.vault / "Last-Session.md").exists())
        self.assertFalse((self.vault / "receipts").exists())

    def test_discovery_skips_symlinks_hidden_and_deep_folders(self):
        for n in ("Threads.md",):
            (self.vault / n).unlink()
        outside = self.tmp / "outside"
        outside.mkdir()
        (outside / "Threads.md").write_text(THREADS, encoding="utf-8")
        (self.vault / "link").symlink_to(outside, target_is_directory=True)
        (self.vault / ".obsidian").mkdir()
        (self.vault / ".obsidian" / "Threads.md").write_text(THREADS, encoding="utf-8")
        deep = self.vault.joinpath("a", "b", "c", "d", "e")
        deep.mkdir(parents=True)
        (deep / "Threads.md").write_text(THREADS, encoding="utf-8")
        self.assertEqual(C.discover(self.vault, "Threads.md"), [])
        self.assertEqual(C.discover(self.vault, "Threads.md", depth=5), [deep / "Threads.md"])
        r = self.call(self.nested_env(), "kokpit_health")
        self.assertEqual(r["vault_files"]["threads"]["how"], "default")
        self.assertFalse(r["vault_files"]["threads"]["found"])
        r = self.call(self.nested_env(), "kokpit_threads")
        self.assertIn("not found", r["note"])

    def test_local_todo_headings(self):
        self.assertEqual([t["text"] for t in C.parse_todo(TODO_TR, D)], ["Prune the tomatoes", "Buy seeds"])
        no_open = "# To-Do\n\n- [ ] Loose task\n\n## ✅ Done\n- [ ] Forgotten tick\n\n## Ideas\n- [ ] Compost\n"
        self.assertEqual(sorted(t["text"] for t in C.parse_todo(no_open, D)), ["Compost", "Loose task"])
        body = "# Liste\n\n## Aperti\n- [ ] uno\n\n## Fatti\n- [x] due\n"
        p = self.tmp / "it.md"
        p.write_text(body, encoding="utf-8")
        C.capture_task(self.vault, "tre", path=p)
        text = p.read_text(encoding="utf-8")
        self.assertLess(text.index("- [ ] tre"), text.index("## Fatti"))
        self.assertNotIn("## Open", text)


class DbPathAndSchema(Base):
    """db_path without kokpit.toml, and databases whose schema is older, newer or partial."""

    def test_db_path_without_kokpit_toml(self):
        conn = self.make_db()
        conn.execute("INSERT INTO runs(day, job, finished, ok, rows) VALUES ('2026-01-01', 'weather', '2026-01-01T07:00', 1, 3)")
        conn.commit()
        conn.close()
        env = self.env(db_path=str(self.db), vault_path=str(self.vault), demo="off")
        r = self.call(env, "kokpit_health")
        self.assertEqual(r["mode"], "live")
        self.assertEqual(r["db"], "ok")
        self.assertEqual(r["jobs"][0]["job"], "weather")
        r = self.call(self.env(db_path=str(self.db), demo="off"), "kokpit_health")  # no vault at all
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["vault_files"]["status"], "vault not found")
        r = self.call(self.env(db_path=str(self.db), demo="off"), "kokpit_threads")
        self.assertFalse(r["ok"])
        self.assertIn("no vault configured", r["fix"])

    def test_db_path_setting_beats_kokpit_toml(self):
        other = self.tmp / "other.db"
        conn = sqlite3.connect(other)
        conn.executescript(json.loads((PLUGIN_DIR / "demo" / "seed.json").read_text(encoding="utf-8"))["schema"])
        conn.execute("INSERT INTO runs(day, job, finished, ok, rows) VALUES ('2026-01-01', 'backup', '2026-01-01T03:30', 1, 1)")
        conn.commit()
        conn.close()
        env = self.env(kokpit_config=str(self.config()), db_path=str(other), demo="off")
        self.assertEqual([j["job"] for j in self.call(env, "kokpit_health")["jobs"]], ["backup"])

    def test_missing_tables_say_unknown(self):
        sqlite3.connect(self.db).close()  # a database with no tables at all
        env = self.live_env()
        r = self.call(env, "kokpit_events")
        self.assertTrue(r["ok"])
        self.assertEqual(r["status"], "unknown / table events missing")
        self.assertEqual(self.call(env, "kokpit_jobs")["status"], "unknown / table job_listings missing")
        self.assertEqual(self.call(env, "kokpit_ai_usage")["status"], "unknown / table ai_usage_hours missing")
        r = self.call(env, "kokpit_health")
        self.assertEqual(r["reason"], "unknown / table runs missing")
        r = self.call(env, "kokpit_today")
        self.assertEqual(r["calendar"]["status"], "unknown / table calendar missing")
        self.assertEqual(r["weather"]["status"], "unknown / table weather missing")
        self.assertEqual(r["counts"]["open_job_listings"], "unknown")

    def test_missing_columns_say_unknown(self):
        conn = sqlite3.connect(self.db)
        conn.executescript("CREATE TABLE runs (id INTEGER PRIMARY KEY, job TEXT, ok INTEGER);"
                           "CREATE TABLE events (id INTEGER PRIMARY KEY, title TEXT, start TEXT);"
                           "CREATE TABLE job_listings (id INTEGER PRIMARY KEY, title TEXT);"
                           "CREATE TABLE ai_usage_hours (id INTEGER PRIMARY KEY, tool TEXT);"
                           "CREATE TABLE calendar (id INTEGER PRIMARY KEY, fetched TEXT);"
                           "INSERT INTO runs(job, ok) VALUES ('weather', 1);")
        conn.close()
        env = self.live_env()
        for tool, key in (("kokpit_events", "status"), ("kokpit_jobs", "status"), ("kokpit_ai_usage", "status"),
                          ("kokpit_health", "reason")):
            r = self.call(env, tool)
            self.assertTrue(r["ok"], (tool, r))
            self.assertRegex(r[key], r"^unknown / table \w+ missing columns: ", tool)
        r = self.call(env, "kokpit_events")
        self.assertIn("venue", r["status"])
        self.assertTrue(r["freshness"]["last_status"].startswith("unknown / table runs missing columns"))
        r = self.call(env, "kokpit_today")
        self.assertTrue(r["ok"], r)
        self.assertIn("missing columns: ok, payload", r["calendar"]["status"])
        self.assertEqual(self.call(env, "kokpit_health")["problems"][0][:38], "job runs: unknown / table runs missing")
        self.assertIn("no runs recorded yet. unknown / table runs missing columns", CMD.run(env, "health"))

    def old_runs_db(self):
        """An older Kokpit: runs without the rows column, no weather/calendar, an events table of another shape."""
        conn = sqlite3.connect(self.db)
        conn.executescript(
            "CREATE TABLE runs (id INTEGER PRIMARY KEY, day TEXT, job TEXT, started TEXT, finished TEXT, ok INTEGER,"
            " info TEXT, warnings TEXT);"
            "CREATE TABLE events (id INTEGER PRIMARY KEY, name TEXT, starts_at TEXT, place TEXT);")
        now = datetime.now().isoformat(timespec="seconds")
        conn.executemany("INSERT INTO runs(day, job, started, finished, ok, info, warnings) VALUES (?,?,?,?,?,?,?)",
                         [(D.isoformat(), "vendor-sync", now, now, 1, "{}", "[]"),
                          (D.isoformat(), "nightly-copy", now, now, 0, '{"error": "disk full"}', "[]")])
        conn.commit()
        conn.close()

    def test_optional_runs_rows_column(self):
        self.old_runs_db()
        env = self.env(kokpit_config=str(self.config()), demo="off", expected="vendor-sync, report")
        r = self.call(env, "kokpit_health")
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["db"], "ok")
        self.assertEqual({j["job"]: j["records"] for j in r["jobs"]}, {"nightly-copy": None, "vendor-sync": None})
        self.assertEqual(r["expected"], ["vendor-sync", "report"])
        self.assertEqual(r["never_run"], ["report"])
        self.assertFalse(any("0 records" in p for p in r["problems"]), r["problems"])
        self.assertIn("nightly-copy: failed (disk full)", r["problems"])
        self.assertEqual(T.freshness(C.open_db(self.db), "vendor-sync")["records"], None)
        with self.assertRaises(ValueError):
            C.optional_col(C.open_db(self.db), "runs", "job")

    def test_foreign_schema_never_raises(self):
        self.old_runs_db()
        env = self.live_env()
        for name in T.TOOLS:
            if name == "kokpit_capture":
                continue
            r = self.call(env, name, {"query": "garden"} if name == "kokpit_brain_search" else {})
            self.assertNotIn("database schema differs", str(r.get("error", "")), name)
            self.assertNotIn("could not be read", str(r.get("error", "")), name)
        t = self.call(env, "kokpit_today")
        self.assertEqual(t["weather"]["status"], "unknown / table weather missing")
        self.assertEqual(t["calendar"]["status"], "unknown / table calendar missing")
        self.assertEqual(t["counts"]["events_next_7_days"], "unknown")
        self.assertRegex(self.call(env, "kokpit_events")["status"], r"^unknown / table events missing columns: ")
        text = CMD.run(env, "today")
        self.assertIn("Weather: unknown / table weather missing", text)
        self.assertIn("Calendar: unknown / table calendar missing", text)
        api = load_dashboard_api()
        api.ENV = env
        o = api.overview()
        self.assertTrue(o["ok"])
        for key in ("today", "threads", "health", "events"):
            self.assertTrue(o[key]["ok"], (key, o[key]))
        for name in api.READ_TOOLS:
            self.assertIsInstance(api.tool(name, query="garden"), dict)

    def test_unexpected_schema_error_is_reported_not_raised(self):
        env = self.live_env()
        self.make_db().close()
        boom = mock.Mock(side_effect=sqlite3.OperationalError("no such column: r.day"))
        with mock.patch.dict(T.TOOLS, {"kokpit_health": boom}):
            r = self.call(env, "kokpit_health")
        self.assertFalse(r["ok"])
        self.assertIn("unknown / database schema differs", r["error"])


class Dashboard(Base):
    """The web tab and the desktop panel render tools.overview: the same JSON the tools return."""

    def test_manifest_and_files(self):
        d = PLUGIN_DIR / "dashboard"
        m = json.loads((d / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(m["name"], "kokpit")
        self.assertEqual(m["tab"]["path"], "/kokpit")
        for key in ("entry", "css", "api"):
            self.assertTrue((d / m[key]).is_file(), m[key])
        js = (d / m["entry"]).read_text(encoding="utf-8")
        self.assertIn('__HERMES_PLUGINS__.register("kokpit"', js)
        self.assertIn("Demo data", js)
        self.assertNotIn("fetch(", js.replace("fetchJSON(", ""))  # only the SDK's authenticated fetch
        self.assertNotRegex(js, r"https?://")                      # no CDN, works offline

    def test_desktop_plugin_follows_the_sdk(self):
        js = (PLUGIN_DIR / "desktop" / "plugin.js").read_text(encoding="utf-8")
        specs = set(re.findall(r"""^\s*import\b[^'"]*['"]([^'"]+)['"]""", js, re.M))
        self.assertLessEqual(specs, {"@hermes/plugin-sdk", "react", "react/jsx-runtime"})
        self.assertIn("id: 'kokpit'", js)
        self.assertIn("area: 'panes'", js)
        self.assertIn("ctx.rest('/overview')", js)
        self.assertIn("ctx.rest('/capture'", js)
        self.assertIn("Demo data", js)
        self.assertNotRegex(js, r"#[0-9a-fA-F]{3,8}\b|rgba?\(|hsla?\(")  # theme variables only
        self.assertNotRegex(js, r"https?://|\bfetch\(|window\.setInterval")
        self.assertNotRegex(js, r"<[A-Za-z][^>]*/?>")                    # no JSX, there is no build step
        if shutil.which("node"):
            r = subprocess.run(["node", "--check", str(PLUGIN_DIR / "desktop" / "plugin.js")],
                               capture_output=True, text=True, timeout=30)
            self.assertEqual(r.returncode, 0, r.stderr)

    @unittest.skipUnless(shutil.which("node"), "node not installed")
    def test_panels_label_owners_and_statuses(self):
        """ownerLabel() and plain() are the same in the web tab and the desktop pane, and say what they should."""
        cases = {"o": [{"owner": "you", "on_you": True, "note": ""}, {"owner": "me", "note": "Fri"},
                       {"owner": "someone", "note": ""}, {"owner": "vendor team", "note": "2026-01-09"},
                       {"owner": "", "on_you": False, "note": ""}],
                 "s": ["unknown / table weather missing", "unknown / table runs missing (no database)",
                       "unknown / table events missing columns: venue", "unknown / table runs unreadable (DatabaseError)",
                       "never run", "stale"]}
        want = [["You", "Me · Fri", "Someone", "Vendor team · 2026-01-09", "Open"],
                ["not collected here", "not collected here", "unknown format", "unknown format", "never run", "stale"]]
        for f in (PLUGIN_DIR / "dashboard" / "dist" / "index.js", PLUGIN_DIR / "desktop" / "plugin.js"):
            js = f.read_text(encoding="utf-8")
            fns = [re.search(r"^ *function %s\(.*?^ *\}$" % n, js, re.M | re.S).group(0) for n in ("ownerLabel", "plain")]
            prog = "\n".join(fns) + f"\nconst c = {json.dumps(cases)};\n" \
                "console.log(JSON.stringify([c.o.map(ownerLabel), c.s.map(plain)]))"
            r = subprocess.run(["node", "-e", prog], capture_output=True, text=True, timeout=30)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(json.loads(r.stdout), want, f.name)

    def test_overview_is_the_tool_output(self):
        env = self.env(demo="on")
        o = T.overview(env)
        self.assertTrue(o["ok"] and o["demo"])
        for key, tool, args in T.OVERVIEW:
            got, want = o[key], json.loads(T.handler(env, tool)(dict(args)))
            self.assertEqual(got.keys(), want.keys(), key)
            if key != "health":  # health ages move with the clock
                self.assertEqual(got, want, key)
        self.assertEqual({m["module"] for m in o["modules"]}, set(T.MODULE_TOOLS))
        self.assertEqual({j["freshness"] for j in o["health"]["jobs"]} - {"fresh", "stale", "failed", "unknown"}, set())
        self.assertIn("failed", {j["freshness"] for j in o["health"]["jobs"]})

    def test_overview_shows_disabled_modules_and_setup_errors(self):
        o = T.overview(self.live_env(modules={"events": False, "ai_usage": False}))
        self.assertTrue(o["ok"])
        off = {m["module"]: m for m in o["modules"] if not m["enabled"]}
        self.assertEqual(set(off), {"events", "ai_usage", "training", "server"})
        self.assertIn("[modules] events = true", off["events"]["enable"])
        self.assertIn("module disabled", o["events"]["error"])
        o = T.overview(self.env(demo="off"))
        self.assertFalse(o["ok"])
        self.assertIn("kokpit_config", o["fix"])

    def test_api_routes_use_the_tools(self):
        api = load_dashboard_api()
        api.ENV = api.contract.Env(lambda k, default=None: {"demo": "on"}.get(k, default), data_dir=self.tmp / "dash")
        o = api.overview()
        self.assertTrue(o["demo"])
        r = api.capture({"text": "From the dashboard", "kind": "task", "due": ""})
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["written"], "To-Do.md")
        self.assertEqual(api.tool("kokpit_brain_search", query="irrigation")["results"][0]["path"], "notes/irrigation.md")
        with self.assertRaises(Exception) as cm:
            api.tool("kokpit_capture")
        self.assertEqual(getattr(cm.exception, "status_code", None), 404)
        self.assertEqual(api.plugin_settings.__name__, "plugin_settings")


def load_dashboard_api():
    """Import dashboard/plugin_api.py the way Hermes does (spec_from_file_location). Uses FastAPI
    when installed; otherwise a stand-in with just APIRouter, Body and HTTPException."""
    import importlib.util
    try:
        import fastapi  # noqa: F401
    except ImportError:
        import types

        class Router:
            def __init__(self):
                self.routes = []

            def _add(self, method, path):
                def deco(fn):
                    self.routes.append((method, path, fn))
                    return fn
                return deco

            def get(self, path):
                return self._add("GET", path)

            def post(self, path):
                return self._add("POST", path)

        class HTTPException(Exception):
            def __init__(self, status_code, detail=""):
                super().__init__(detail)
                self.status_code, self.detail = status_code, detail

        stub = types.ModuleType("fastapi")
        stub.APIRouter, stub.HTTPException, stub.Body = Router, HTTPException, lambda *a, **k: None
        patcher = mock.patch.dict("sys.modules", {"fastapi": stub})
        patcher.start()
        unittest.addModuleCleanup(patcher.stop)
    spec = importlib.util.spec_from_file_location("hermes_dashboard_plugin_kokpit_test", PLUGIN_DIR / "dashboard" / "plugin_api.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class OptionalModules(Base):
    def live(self, **settings):
        base = {"kokpit_config": str(self.config()), "demo": "off"}
        base.update(settings)
        return self.env(**base)

    def write_jsonl(self, name, rows):
        p = self.tmp / "runs" / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
        return p

    def test_off_by_default_with_enable_hint(self):
        env = self.live()
        for tool, mod in (("kokpit_training", "training"), ("kokpit_server", "server")):
            out = self.call(env, tool)
            self.assertFalse(out["ok"])
            self.assertEqual(out["error"], "module disabled")
            self.assertIn(f"plugins.entries.kokpit.settings.{mod} true", out["fix"])
        mods = C.load_config(self.config())["modules"]
        self.assertFalse(mods["training"] or mods["server"])

    def test_training_running_stalled_finished(self):
        now = time.time()
        self.write_jsonl("a.jsonl", [{"step": s, "time": now - (100 - s) * 2, "loss": 1.0 / (s + 1),
                                      "total_steps": 200, "acc": 0.5} for s in range(0, 101, 10)])
        self.write_jsonl("b.jsonl", [{"step": s, "time": now - 7200 + s, "loss": 0.9} for s in range(0, 51, 10)])
        csv = self.tmp / "runs" / "c.csv"
        csv.write_text("step,max_steps,loss\n10,20,0.5\n20,20,0.25\n", encoding="utf-8")
        env = self.live(training=True, training_logs=json.dumps([str(self.tmp / "runs" / "*.jsonl"), str(csv)]),
                        training_stall_minutes=30)
        out = self.call(env, "kokpit_training")
        self.assertTrue(out["ok"], out)
        runs = {r["run"]: r for r in out["runs"]}
        self.assertEqual(runs["a"]["status"], "running")
        self.assertEqual(runs["a"]["last_step"], 100)
        self.assertAlmostEqual(runs["a"]["steps_per_sec"], 0.5, places=2)
        self.assertAlmostEqual(runs["a"]["eta_minutes"], 100 / 0.5 / 60, places=0)
        self.assertEqual(runs["a"]["metrics"]["acc"], 0.5)
        self.assertEqual(runs["b"]["status"], "stalled?")
        self.assertIn("may have stopped", runs["b"]["warning"])
        self.assertEqual(runs["c"]["status"], "finished")
        self.assertEqual(len(out["warnings"]), 1)
        # a per-call threshold overrides the setting
        self.assertEqual(self.call(env, "kokpit_training", {"stall_minutes": 1000})["warnings"], [])

    def test_training_without_logs_or_bad_rows(self):
        out = self.call(self.live(training="true"), "kokpit_training")
        self.assertFalse(out["ok"])
        self.assertIn("training_logs", out["fix"])
        bad = self.tmp / "runs" / "bad.jsonl"
        bad.parent.mkdir(parents=True)
        bad.write_text("not json\n{\"note\": 1}\n", encoding="utf-8")
        out = self.call(self.live(training=True, training_logs=[str(bad)]), "kokpit_training")
        self.assertTrue(out["ok"])
        self.assertEqual(out["runs"][0]["status"], "unknown")
        out = self.call(self.live(training=True, training_logs=[str(self.tmp / "none" / "*.jsonl")]), "kokpit_training")
        self.assertEqual(out["files"], 0)
        self.assertIn("note", out)

    def test_server_reads_machine_and_validates_units(self):
        env = self.live(server=True, server_paths=[str(self.tmp)], server_units=["ok-name.service", "bad; rm -rf /"])
        with mock.patch.object(P.server.subprocess, "run") as run:
            run.return_value = mock.Mock(stdout="active\n")
            with mock.patch.object(P.server.shutil, "which", return_value="/usr/bin/systemctl"):
                out = self.call(env, "kokpit_server")
        self.assertTrue(out["ok"], out)
        self.assertEqual(out["disks"][0]["path"], str(self.tmp))
        self.assertIsInstance(out["disks"][0]["used_pct"], float)
        svc = {s["name"]: s["state"] for s in out["services"]}
        self.assertEqual(svc["ok-name.service"], "active")
        self.assertEqual(svc["bad; rm -rf /"], "invalid name")
        argv = run.call_args.args[0]
        self.assertEqual(argv[1:], ["--user", "is-active", "--", "ok-name.service"])
        self.assertIsInstance(argv, list)
        self.assertEqual(run.call_count, 1)
        self.assertIn("service bad; rm -rf /: invalid name", out["warnings"])

    def test_server_unknowns(self):
        self.assertEqual(P.server.cpu_temp(self.tmp / "no-thermal"), "unknown")
        self.assertEqual(P.server.memory(self.tmp / "no-proc"), "unknown")
        self.assertEqual(P.server.uptime(self.tmp / "no-proc"), "unknown")
        with mock.patch.object(P.server.shutil, "which", return_value=None):
            self.assertEqual(P.server.unit_state("x.service"), "unknown")
        zone = self.tmp / "th" / "thermal_zone0"
        zone.mkdir(parents=True)
        (zone / "temp").write_text("61500\n")
        self.assertEqual(P.server.cpu_temp(self.tmp / "th"), 61.5)
        self.assertEqual(P.server.disks([str(self.tmp / "missing")])[0]["status"][:7], "unknown")

    def test_demo_has_synthetic_training_and_server(self):
        env = self.env(demo="on")
        tr = self.call(env, "kokpit_training")
        self.assertTrue(tr["ok"], tr)
        self.assertEqual(sorted(r["status"] for r in tr["runs"]), ["finished", "running", "stalled?"])
        sv = self.call(env, "kokpit_server")
        self.assertTrue(sv["ok"])
        self.assertTrue(any("disk" in w for w in sv["warnings"]))
        ov = T.overview(env)
        self.assertTrue(ov["training"]["ok"] and ov["server"]["ok"])
        self.assertTrue(all(m["enabled"] for m in ov["modules"]))


class Command(Base):
    def test_subcommands(self):
        env = self.live_env()
        self.assertIn("Tasks: 1 open, 1 overdue", CMD.run(env, "today"))
        self.assertIn("Garden timer", CMD.run(env, "brain"))
        self.assertIn("notes/garden-timer.md", CMD.run(env, "brain relay"))
        self.assertIn("no runs recorded yet", CMD.run(env, "health"))
        self.assertIn("/kokpit today", CMD.run(env, "help"))
        self.assertIn("/kokpit today", CMD.run(env, ""))
        self.assertIn("Unknown subcommand", CMD.run(env, "dance"))

    def test_plain_text_and_errors(self):
        env = self.live_env(modules={"health": False})
        out = CMD.handler(env)("health")
        self.assertIn("module disabled", out)
        for sub in ("today", "brain", "help"):
            text = CMD.handler(self.env(demo="on"))(sub)
            self.assertNotIn("**", text)
            self.assertNotIn("\u2014", text)


class Privacy(unittest.TestCase):
    def test_plugin_files_are_synthetic(self):
        # built from pieces so this file itself stays clean for the commit-time grep
        words = ("sa" + "hin", "demir" + "bas", "cre" + "mona", "@" + "gmail", "/ho" + "me/", "s" + "k-", "gh" + "p_",
                 "tok" + "en=", "api" + "_key")
        for p in PLUGIN_DIR.rglob("*"):
            if p.is_file() and p.suffix in (".py", ".md", ".json", ".yaml", ".js", ".css"):
                text = p.read_text(encoding="utf-8").lower()
                for w in words:
                    self.assertNotIn(w, text, f"{p.relative_to(ROOT)} contains {w}")


if __name__ == "__main__":
    unittest.main()
