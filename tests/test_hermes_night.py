"""Tests for the plugin's capture-from-anywhere commands and the night pass, on synthetic tmp vaults only.

Run: python -m unittest tests.test_hermes_night
"""
import json
import sqlite3
import sys
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests.test_hermes_plugin import P, PLUGIN_DIR, Base, FakeCtx  # noqa: E402

C, T, H, CMD = P.contract, P.tools, P.hooks, P.commands
N = sys.modules[P.__name__ + ".night"]
D = date.today()


def iso(days: int) -> str:
    return (D + timedelta(days=days)).isoformat()


THREADS = f"""# Threads

## Active Threads

### Pond pump
Where we left off ({iso(-1)}): the float switch is wired; the pump hums at night.
Open: on me: measure the pump current; on you: choose a quieter pump model.
Source: [[notes/pond-pump]]

### Bread baking
Where we left off ({iso(-15)}): the sourdough starter is alive again.
Open: on me: bake a test loaf with rye flour; on you: buy a proofing basket.
Source: [[notes/bread]]

### Shared chores
Where we left off ({iso(-2)}): rota agreed.
Open: on the landlord: fix the gate; on you: nothing.
"""
TODO = f"""# To-Do

## Open
- [ ] Measure the pump current at night
- [ ] Return the ladder (due: {iso(-3)})
- [ ] Clean the pond filter

## Done
- [x] Wire the float switch
"""
PUMP = """# Pond pump

Float switch on the inlet, pump rated 40 watts.
The impeller rattles; a rubber mount and a quieter housing might help.

- [ ] Clean the pond filter
"""
BREAD = """# Bread

Rye starter fed twice a day.
"""
DECK = """# Workshop bench

The rubber mount from the old impeller housing could dampen the bench grinder too.
"""
NEWS_POND = f"""---
date: {iso(0)}
---
# Quiet pond pump test: five models compared

Measured noise and power for pumps under 50 watts.
"""
NEWS_OLD = f"""---
date: {iso(-20)}
---
# Pond pump recall from last month
"""
JOURNAL = f"""# {iso(0)}

- 07:10 The pond pump and the bread proofing both need a quiet warm corner
"""


class NightBase(Base):
    def setUp(self):
        super().setUp()
        (self.vault / "Threads.md").write_text(THREADS, encoding="utf-8")
        (self.vault / "To-Do.md").write_text(TODO, encoding="utf-8")
        (self.vault / "notes" / "garden-timer.md").unlink()
        (self.vault / "notes" / "pond-pump.md").write_text(PUMP, encoding="utf-8")
        (self.vault / "notes" / "bread.md").write_text(BREAD, encoding="utf-8")
        (self.vault / "notes" / "workshop.md").write_text(DECK, encoding="utf-8")
        (self.vault / "news").mkdir()
        (self.vault / "news" / "quiet-pumps.md").write_text(NEWS_POND, encoding="utf-8")
        (self.vault / "news" / "recall.md").write_text(NEWS_OLD, encoding="utf-8")
        (self.vault / "daily").mkdir()
        (self.vault / "daily" / f"{iso(0)}.md").write_text(JOURNAL, encoding="utf-8")

    def nenv(self, **settings):
        return self.env(vault_path=str(self.vault), demo="off", **settings)

    def cmd(self, env, raw):
        return CMD.handler(env)(raw)

    def snapshot(self):
        return {p.relative_to(self.vault).as_posix(): p.read_bytes() for p in self.vault.rglob("*") if p.is_file()}

    def props(self, env, kind=""):
        r = self.call(env, "kokpit_night", {"action": "list", "limit": 50, **({"kind": kind} if kind else {})})
        self.assertTrue(r["ok"], r)
        return r["proposals"]


class Journal(Base):
    def test_capture_journal_creates_and_appends(self):
        now = datetime(2031, 3, 4, 7, 5)
        p, line = C.capture_journal(self.vault, "first  line", now)
        self.assertEqual(p, self.vault / "daily" / "2031-03-04.md")
        self.assertEqual(line, "- 07:05 first line")
        self.assertEqual(p.read_text(encoding="utf-8"), "# 2031-03-04\n\n- 07:05 first line\n")
        p.write_text(p.read_text(encoding="utf-8") + "hand-written, no newline", encoding="utf-8")
        before = p.read_bytes()
        C.capture_journal(self.vault, "second", datetime(2031, 3, 4, 9, 30))
        after = p.read_bytes()
        self.assertTrue(after.startswith(before))  # append-only
        self.assertTrue(after.endswith(b"no newline\n- 09:30 second\n"))

    def test_journal_dir_setting_and_unsafe_values(self):
        now = datetime(2031, 3, 4, 7, 5)
        p, _ = C.capture_journal(self.vault, "x", now, folder="Journal/days")
        self.assertEqual(p.parent, self.vault / "Journal" / "days")
        for bad in ("../out", "/abs", ".hidden", ""):
            p, _ = C.capture_journal(self.vault, "x", now, folder=bad)
            self.assertEqual(p.parent, self.vault / "daily", bad)
        with self.assertRaises(ValueError):
            C.capture_journal(self.vault, "   ", now)

    def test_tool_kind_journal(self):
        env = self.env(vault_path=str(self.vault), demo="off", journal_dir="log")
        r = self.call(env, "kokpit_capture", {"kind": "journal", "text": "pump hums"})
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["written"], f"log/{D.isoformat()}.md")
        self.assertRegex(r["line"], r"^- \d\d:\d\d pump hums$")
        r = self.call(env, "kokpit_capture", {"kind": "diary", "text": "x"})
        self.assertFalse(r["ok"])
        self.assertIn("journal", r["error"])


class CaptureCommands(Base):
    def setUp(self):
        super().setUp()
        self.e = self.env(vault_path=str(self.vault), demo="off")
        self.h = CMD.handler(self.e)

    def test_note_todo_journal(self):
        out = self.h("note Ask about the spare relay")
        self.assertTrue(out.startswith("Saved note: inbox/"), out)
        self.assertEqual(len(list((self.vault / "inbox").glob("*.md"))), 1)
        out = self.h(f"todo Call the plumber due:{iso(5)}")
        self.assertEqual(out, f"Added to To-Do.md: - [ ] Call the plumber (due: {iso(5)})")
        self.assertIn(f"- [ ] Call the plumber (due: {iso(5)})", (self.vault / "To-Do.md").read_text(encoding="utf-8"))
        out = self.h("j relay clicks")
        self.assertRegex(out, rf"^Added to daily/{D.isoformat()}\.md: - \d\d:\d\d relay clicks$")
        out = self.h("journal second line")
        self.assertIn("second line", out)

    def test_usage_on_empty_text(self):
        for sub, usage in (("note", "/kokpit note <text>"), ("todo", "/kokpit todo <text>"), ("j", "/kokpit j <text>"),
                           ("journal", "/kokpit journal <text>"), ("add", "/kokpit add <id>"), ("skip", "/kokpit skip <id>")):
            self.assertTrue(self.h(sub).startswith("Usage: " + usage), sub)
        self.assertTrue(self.h(f"todo due:{iso(1)}").startswith("Usage:"))

    def test_missing_vault_is_a_clear_message(self):
        h = CMD.handler(self.env(vault_path=str(self.tmp / "nope"), demo="off"))
        for raw in ("note hi", "todo hi", "j hi", "night run", "add abcdef"):
            out = h(raw)
            self.assertTrue(out.startswith("Kokpit: "), (raw, out))
            self.assertNotIn("failed (", out)

    def test_help_lists_new_commands(self):
        out = self.h("help")
        for word in ("/kokpit note", "/kokpit todo", "/kokpit j ", "/kokpit night", "/kokpit add", "/kokpit skip"):
            self.assertIn(word, out)


class NightKinds(NightBase):
    def test_each_kind_from_fixture_vault(self):
        env = self.nenv()
        before = self.snapshot()
        out = self.cmd(env, "night run")
        self.assertIn("Notes were not changed", out)
        self.assertEqual(self.snapshot(), before)  # the pass never touches the vault
        ps = self.props(env)
        kinds = {p["kind"] for p in ps}
        self.assertEqual(kinds, {"todo", "improve", "link", "idea", "news"})
        for p in ps:  # every path source is quoted word for word
            for s in p["sources"]:
                if "path" in s:
                    self.assertIn(s["quote"], (self.vault / s["path"]).read_text(encoding="utf-8"), p)
            self.assertNotIn("—", p["text"])

    def test_todo_rules(self):
        env = self.nenv()
        self.cmd(env, "night run")
        todos = self.props(env, "todo")
        reasons = {p["reason"] for p in todos}
        self.assertEqual(reasons, {"thread_item_not_in_todo", "overdue", "duplicate", "news_match"})
        texts = " | ".join(p["text"] for p in todos)
        self.assertNotIn("measure the pump current", texts)  # already in To-Do.md
        self.assertNotIn("landlord", texts)  # someone else's item
        self.assertNotIn("nothing", texts)
        self.assertIn("choose a quieter pump model", texts)
        dup = next(p for p in todos if p["reason"] == "duplicate")
        self.assertEqual(sorted(s["path"] for s in dup["sources"]), ["To-Do.md", "notes/pond-pump.md"])
        over = next(p for p in todos if p["reason"] == "overdue")
        self.assertIn("Return the ladder", over["text"])
        news = next(p for p in todos if p["reason"] == "news_match")
        self.assertEqual(news["text"], "Check Quiet pond pump test: five models compared for Pond pump")

    def test_improve_is_the_stale_thread_first_item(self):
        env = self.nenv()
        self.cmd(env, "night run")
        imp = self.props(env, "improve")
        self.assertEqual(len(imp), 1)
        self.assertIn("Bread baking has not moved for 15 days", imp[0]["text"])
        self.assertEqual(imp[0]["sources"][0]["quote"], "on me: bake a test loaf with rye flour")
        env = self.nenv(stale_days=30)
        self.call(env, "kokpit_night", {"action": "run"})
        self.assertEqual(self.props(env, "improve"), [])

    def test_link_quotes_shared_words_and_skips_linked_notes(self):
        env = self.nenv()
        self.cmd(env, "night run")
        links = self.props(env, "link")
        self.assertEqual(len(links), 1)
        self.assertEqual({s["path"] for s in links[0]["sources"]}, {"notes/pond-pump.md", "notes/workshop.md"})
        self.assertTrue({"rubber", "impeller", "housing"} <= set(links[0]["words"]))
        (self.vault / "notes" / "workshop.md").write_text(DECK + "\nSee [[notes/pond-pump]].\n", encoding="utf-8")
        self.cmd(env, "night run")
        self.assertEqual(self.props(env, "link"), [])  # cause gone: open proposal dropped

    def test_idea_from_journal_line_matching_two_projects(self):
        env = self.nenv()
        self.cmd(env, "night run")
        ideas = self.props(env, "idea")
        self.assertEqual(len(ideas), 1)
        self.assertIn("connects", ideas[0]["text"])
        self.assertIn("Pond pump", ideas[0]["text"])
        self.assertIn("Bread baking", ideas[0]["text"])
        self.assertEqual(ideas[0]["sources"][0]["path"], f"daily/{iso(0)}.md")

    def test_news_from_vault_folder_respects_news_days(self):
        env = self.nenv()
        r = self.call(env, "kokpit_night", {"action": "run"})
        self.assertEqual(r["news"]["items"], 1)  # the 20 day old item is out of range
        news = self.props(env, "news")
        self.assertEqual([p["project"] for p in news], ["Pond pump"])
        r = self.call(self.nenv(news_days=30), "kokpit_night", {"action": "run"})
        self.assertEqual(r["news"]["items"], 2)

    def test_no_news_source(self):
        import shutil
        shutil.rmtree(self.vault / "news")
        r = self.call(self.nenv(), "kokpit_night", {"action": "run"})
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["news"]["source"], "no news source")
        self.assertEqual(r["generated"]["news"], 0)
        self.assertIn("News source: no news source", self.cmd(self.nenv(), "night run"))


class NightStore(NightBase):
    def test_idempotent_rerun(self):
        env = self.nenv()
        r1 = self.call(env, "kokpit_night", {"action": "run"})
        ids1 = sorted(p["id"] for p in self.props(env))
        r2 = self.call(env, "kokpit_night", {"action": "run"})
        ids2 = sorted(p["id"] for p in self.props(env))
        self.assertGreater(r1["new"], 0)
        self.assertEqual(r2["new"], 0)
        self.assertEqual(ids1, ids2)

    def test_dismissed_and_done_never_reopen(self):
        env = self.nenv()
        self.cmd(env, "night run")
        link = self.props(env, "link")[0]
        news = self.props(env, "news")[0]
        self.assertIn("Dismissed", self.cmd(env, "skip " + link["short"]))
        self.assertIn("Accepted", self.cmd(env, "add " + news["short"]))
        for _ in range(2):
            self.cmd(env, "night run")
        open_ids = {p["id"] for p in self.props(env)}
        self.assertNotIn(link["id"], open_ids)
        self.assertNotIn(news["id"], open_ids)
        self.assertIn("already dismissed", self.cmd(env, "skip " + link["short"]))
        self.assertIn("already done", self.cmd(env, "add " + news["short"]))

    def test_store_lives_outside_the_notes(self):
        env = self.nenv()
        self.cmd(env, "night run")
        store = env.state()["night_store"]
        self.assertTrue(store.is_file())
        self.assertNotIn(self.vault.resolve(), store.resolve().parents)
        self.assertEqual(store.parent, self.tmp / "plugin-data" / "night")
        data = json.loads(store.read_text(encoding="utf-8"))
        p = data["proposals"][0]
        for key in ("id", "kind", "text", "reason", "sources", "created", "status"):
            self.assertIn(key, p)

    def test_fallback_store_without_plugin_data_dir(self):
        env = C.Env(lambda k, default=None: {"vault_path": str(self.vault), "demo": "off"}.get(k, default))
        env._data_dir, env._durable = self.tmp / "tmpdir", False  # what Env does when Hermes has no data dir
        self.assertEqual(env.night_store(self.vault), self.vault / ".kokpit" / "night.json")

    def test_ambiguous_prefix_and_bad_id(self):
        env = self.nenv()
        self.cmd(env, "night run")
        self.assertIn("at least 4 characters", self.cmd(env, "add ab"))
        self.assertIn("no proposal", self.cmd(env, "add ffffffffffff"))

    def test_runtime_small_on_a_2k_note_vault(self):
        notes = self.vault / "notes" / "bulk"
        notes.mkdir()
        words = ["alpha", "bravo", "charlie", "delta", "echo", "foxtrot", "golf", "hotel", "india", "juliet"]
        for i in range(2000):
            notes.joinpath(f"n{i}.md").write_text(
                f"# Note {i}\n\n{words[i % 10]} {words[(i * 3) % 10]} topic{i % 50} item{i}\n- [ ] task {i}\n",
                encoding="utf-8")
        r = self.call(self.nenv(), "kokpit_night", {"action": "run"})
        self.assertTrue(r["ok"], r)
        self.assertLess(r["seconds"], 20)


class NightAccept(NightBase):
    def test_accept_todo_appends_to_todo_once(self):
        env = self.nenv()
        self.cmd(env, "night run")
        p = next(x for x in self.props(env, "todo") if "quieter pump" in x["text"])
        before = (self.vault / "To-Do.md").read_bytes()
        out = self.cmd(env, "add " + p["short"])
        self.assertEqual(out, f"Accepted [{p['short']}]. Wrote To-Do.md: - [ ] choose a quieter pump model")
        after = (self.vault / "To-Do.md").read_bytes()
        self.assertEqual(after.count(b"choose a quieter pump model"), 1)
        self.assertTrue(after.replace(b"- [ ] choose a quieter pump model\n", b"", 1) == before)
        self.cmd(env, "night run")
        self.assertFalse(any("quieter pump" in x["text"] for x in self.props(env, "todo")))

    def test_accept_idea_creates_new_file_never_overwrites(self):
        env = self.nenv()
        self.cmd(env, "night run")
        idea = self.props(env, "idea")[0]
        title = idea["idea_title"]
        clash = self.vault / "ideas" / f"{D.isoformat()}-{C._slug(title)}.md"
        clash.parent.mkdir()
        clash.write_text("mine, keep me\n", encoding="utf-8")
        out = self.cmd(env, "add " + idea["short"])
        self.assertIn("Wrote ideas/", out)
        self.assertEqual(clash.read_text(encoding="utf-8"), "mine, keep me\n")
        made = [p for p in (self.vault / "ideas").glob("*.md") if p != clash]
        self.assertEqual(len(made), 1)
        text = made[0].read_text(encoding="utf-8")
        self.assertIn("type: idea", text)
        self.assertIn(idea["text"], text)
        self.assertIn(f'daily/{iso(0)}.md: "', text)

    def test_accept_rechecks_source(self):
        env = self.nenv()
        self.cmd(env, "night run")
        p = next(x for x in self.props(env, "todo") if "quieter pump" in x["text"])
        (self.vault / "Threads.md").write_text(THREADS.replace("choose a quieter pump model", "keep the pump"),
                                               encoding="utf-8")
        out = self.cmd(env, "add " + p["short"])
        self.assertIn("source changed", out)
        self.assertNotIn("quieter", (self.vault / "To-Do.md").read_text(encoding="utf-8"))

    def test_accept_needs_quick_capture(self):
        p = self.config()
        p.write_text(p.read_text(encoding="utf-8").replace("[brain]\n", "[brain]\nquick_capture = false\n"), encoding="utf-8")
        env = self.env(kokpit_config=str(p), demo="off")
        self.cmd(env, "night run")
        p = next(x for x in self.props(env, "todo") if "quieter pump" in x["text"])
        self.assertIn("quick capture is off", self.cmd(env, "add " + p["short"]))


class NightDb(NightBase):
    def briefing(self, conn, rows):
        conn.execute("CREATE TABLE briefing_items (id INTEGER PRIMARY KEY, day TEXT NOT NULL, topic TEXT NOT NULL, "
                     "url TEXT NOT NULL, title TEXT NOT NULL, project TEXT, reason TEXT)")
        conn.executemany("INSERT INTO briefing_items (day, topic, url, title, project, reason) VALUES (?,?,?,?,?,?)", rows)

    def test_briefing_table_wins_over_news_folder(self):
        conn = sqlite3.connect(self.db)
        self.briefing(conn, [(iso(0), "tech", "https://example.org/a", "Sourdough rye ratios explained", "Bread baking",
                              "matches project Bread baking: keyword rye in thread Bread baking"),
                             (iso(0), "tech", "https://example.org/b", "Unmatched item", None, None)])
        conn.commit()
        conn.close()
        env = self.nenv(db_path=str(self.db))
        r = self.call(env, "kokpit_night", {"action": "run"})
        self.assertEqual(r["news"]["source"], "kokpit database (briefing_items)")
        news = self.props(env, "news")
        self.assertEqual(len(news), 1)
        self.assertEqual(news[0]["project"], "Bread baking")
        self.assertIn("keyword rye", news[0]["reason"])
        self.assertTrue(news[0]["sources"][0]["item"].startswith("briefing_items#"))

    def test_missing_db_and_foreign_schema(self):
        r = self.call(self.nenv(db_path=str(self.tmp / "missing.db")), "kokpit_night", {"action": "run"})
        self.assertTrue(r["ok"], r)
        self.assertTrue(r["news"]["source"].startswith("vault"))
        conn = sqlite3.connect(self.db)
        conn.execute("CREATE TABLE briefing_items (id INTEGER PRIMARY KEY, headline TEXT)")
        conn.execute("CREATE TABLE organiser_proposals (x TEXT)")
        conn.commit()
        conn.close()
        env = self.nenv(db_path=str(self.db))
        r = self.call(env, "kokpit_night", {"action": "run"})
        self.assertTrue(r["ok"], r)
        self.assertTrue(r["news"]["source"].startswith("vault"))
        r = self.call(env, "kokpit_night", {})
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["kokpit_app"], [])
        (self.tmp / "junk.db").write_bytes(b"not a database at all" * 50)
        r = self.call(self.nenv(db_path=str(self.tmp / "junk.db")), "kokpit_night", {"action": "run"})
        self.assertTrue(r["ok"], r)

    def test_app_organiser_proposals_shown_read_only(self):
        schema = (ROOT / "kokpit" / "modules" / "organiser" / "schema.sql").read_text(encoding="utf-8")
        conn = sqlite3.connect(self.db)
        conn.executescript(schema)
        conn.execute("INSERT INTO organiser_proposals (kind, path, text, status, first_seen, last_seen) "
                     "VALUES ('link', 'notes/bread.md', 'Link bread to the pond note', 'open', ?, ?)", (iso(0), iso(0)))
        conn.commit()
        conn.close()
        env = self.nenv(db_path=str(self.db))
        self.cmd(env, "night run")
        out = self.cmd(env, "night")
        self.assertIn("From the Kokpit app organiser", out)
        self.assertIn("Link bread to the pond note", out)


class NightBrainAndAuto(NightBase):
    def test_brain_block_line_within_budget(self):
        env = self.nenv(context_max_chars=400)
        self.cmd(env, "night run")
        h = H.Hooks(env)
        r = h.pre_llm_call(session_id="s1", user_message="hello", is_first_turn=True)
        self.assertIsNotNone(r)
        self.assertLessEqual(len(r["context"]), 400)
        self.assertRegex(r["context"], r"Night: \d+ todos, 1 next step, 1 link, 1 idea, 1 news match\. /kokpit night")
        r2 = h.pre_llm_call(session_id="s1", user_message="hello", is_first_turn=False)
        self.assertTrue(r2 is None or "Night:" not in r2["context"])  # only on full turns

    def test_brain_line_empty_without_proposals(self):
        self.assertEqual(N.brain_line(None), "")
        self.assertEqual(N.brain_line(self.tmp / "nothing.json"), "")

    def test_auto_run_off_by_default_then_once_per_20h(self):
        env = self.nenv()
        h = H.Hooks(env)
        h.on_session_start(session_id="a")
        self.assertIsNone(h.last_auto)
        env = self.nenv(night_auto=True)
        h = H.Hooks(env)
        h.on_session_start(session_id="a")
        self.assertIsNotNone(h.last_auto)
        h.last_auto.join(30)
        store = env.state()["night_store"]
        first = N.load(store)["last_run"]
        self.assertTrue(first)
        h.last_auto = None
        h.on_session_start(session_id="b")
        self.assertIsNone(h.last_auto)  # ran less than 20 hours ago
        self.assertFalse(N.due_for_auto(store, datetime.now() + timedelta(hours=19)))
        self.assertTrue(N.due_for_auto(store, datetime.now() + timedelta(hours=21)))

    def test_registers_hook_and_cli(self):
        class Ctx(FakeCtx):
            def __init__(self):
                super().__init__()
                self.cli = {}

            def register_cli_command(self, name, help, setup_fn, handler_fn=None, description=""):
                self.cli[name] = (setup_fn, handler_fn)

        ctx = Ctx()
        P.register(ctx)
        self.assertIn("on_session_start", ctx.hooks)
        self.assertIn("kokpit_night", ctx.tools)
        self.assertIn("kokpit-night", ctx.cli)
        P.register(FakeCtx())  # an older Hermes without register_cli_command still loads


class StubLlm:
    def __init__(self, parsed=None, boom=False):
        self.parsed, self.boom, self.calls = parsed, boom, 0

    def complete_structured(self, **kw):
        self.calls += 1
        assert kw["json_schema"]["type"] == "object"
        if self.boom:
            raise RuntimeError("provider down: token=abc")

        return type("Result", (), {"parsed": self.parsed})()


class NightModel(NightBase):
    def env_with(self, llm, **settings):
        s = {"vault_path": str(self.vault), "demo": "off", **settings}
        return C.Env(lambda k, default=None: s.get(k, default), data_dir=self.tmp / "plugin-data", llm=lambda: llm)

    def test_off_by_default(self):
        llm = StubLlm({"proposals": []})
        r = self.call(self.env_with(llm), "kokpit_night", {"action": "run"})
        self.assertEqual(r["model"], "off")
        self.assertEqual(llm.calls, 0)

    def test_checked_proposals_kept_rejected_counted(self):
        good = {"kind": "todo", "text": "Test a rubber mount under the pump", "path": "notes/pond-pump.md",
                "quote": "a rubber mount and a quieter housing might help"}
        bad = [
            {"kind": "todo", "text": "Invented", "path": "notes/pond-pump.md", "quote": "this sentence is not in the note"},
            {"kind": "delete", "text": "Remove the bread note", "path": "notes/bread.md", "quote": "Rye starter fed twice a day."},
            {"kind": "idea", "text": "x" * 300, "path": "notes/bread.md", "quote": "Rye starter fed twice a day."},
            {"kind": "todo", "text": "Outside", "path": "../secret.md", "quote": "anything at all here"},
            {"kind": "todo", "text": "No file", "path": "notes/missing.md", "quote": "anything at all here"},
            {"text": "no kind"},
        ]
        llm = StubLlm({"proposals": [good] + bad})
        env = self.env_with(llm, night_model=True)
        r = self.call(env, "kokpit_night", {"action": "run"})
        self.assertEqual(llm.calls, 1)
        self.assertEqual(r["model"], "on")
        self.assertEqual(r["model_kept"], 1)
        self.assertEqual(r["model_rejected"], len(bad))
        stored = json.loads(env.state()["night_store"].read_text(encoding="utf-8"))["proposals"]
        model = [p for p in stored if p["reason"] == "model"]
        self.assertEqual([p["text"] for p in model], [good["text"]])
        self.assertIn("1 kept, 6 rejected", self.cmd(env, "night run"))

    def test_model_failure_and_missing_llm(self):
        env = self.env_with(StubLlm(boom=True), night_model=True)
        r = self.call(env, "kokpit_night", {"action": "run"})
        self.assertTrue(r["ok"])
        self.assertEqual(r["model"], "failed (RuntimeError)")
        self.assertNotIn("token", json.dumps(r))
        self.assertGreater(sum(r["generated"].values()), 0)  # the rule path still ran
        r = self.call(self.env_with(None, night_model=True), "kokpit_night", {"action": "run"})
        self.assertTrue(r["model"].startswith("unavailable"))


class NightTurkish(NightBase):
    THREADS_TR = """## Active Threads

### Su pompası
Nerede kaldık (29 Ekim): pompa sesi azaldı. Açık: Sende: yeni conta sipariş et; Bende (30 Ekim): basınç ölçümünü tekrarla; Başkası: servis teklif verecek. Kaynak: [[notes/pond-pump]].

### Boş thread
Nerede kaldık: bekliyor. Açık: Sende: yok; Bende: yok. Kaynak: [[notes/bread]].
"""

    def test_inline_acik_thread_gives_todo_items(self):
        ts = N.raw_threads(self.THREADS_TR, date(2026, 11, 6))
        self.assertEqual([t["title"] for t in ts], ["Su pompası", "Boş thread"])
        items = ts[0]["items"]
        self.assertEqual([(i["owner"], i["text"]) for i in items],
                         [("you", "yeni conta sipariş et"), ("me", "basınç ölçümünü tekrarla"),
                          ("someone", "servis teklif verecek")])
        self.assertEqual(items[1]["quote"], "Bende (30 Ekim): basınç ölçümünü tekrarla")
        self.assertIn(items[1]["quote"], self.THREADS_TR)  # verbatim
        self.assertEqual(ts[0]["source"], "notes/pond-pump")
        self.assertEqual(ts[0]["date"], "2026-10-30")
        self.assertEqual(ts[1]["items"], [])
        (self.vault / "Threads.md").write_text(self.THREADS_TR, encoding="utf-8")
        env = self.nenv()
        self.cmd(env, "night run")
        texts = [p["text"] for p in self.props(env, "todo")]
        self.assertIn("Add to To-Do: yeni conta sipariş et (Su pompası)", texts)
        self.assertIn("Add to To-Do: basınç ölçümünü tekrarla (Su pompası)", texts)
        self.assertFalse(any("servis" in t for t in texts))  # someone else's item

    def test_turkish_dates(self):
        today = date(2026, 11, 6)
        self.assertEqual(N.parse_date("Nerede kaldık (29 Ekim): x", today), date(2026, 10, 29))
        self.assertEqual(N.parse_date("29 Ekim 2025", today), date(2025, 10, 29))
        self.assertEqual(N.parse_date("(31 Eki)", today), date(2026, 10, 31))
        for s in ("5 Şubat", "5 subat", "5 ŞUBAT"):
            self.assertEqual(N.parse_date(s, today), date(2026, 2, 5), s)
        self.assertEqual(N.parse_date("3 Ağustos", today), N.parse_date("3 Agustos", today))
        self.assertEqual(N.parse_date("12 Kasım", date(2026, 11, 20)), date(2026, 11, 12))
        self.assertEqual(N.parse_date("1 Aralik", today), date(2025, 12, 1))  # future: previous year
        self.assertEqual(N.parse_date("7 novembre", today), date(2025, 11, 7))
        self.assertEqual(N.parse_date("6 November", today), date(2026, 11, 6))
        self.assertEqual(N.parse_date("2026-10-29", today), date(2026, 10, 29))
        self.assertIsNone(N.parse_date("31 Şubat", today))
        self.assertIsNone(N.parse_date("12 adet", today))

    def test_stale_thread_from_turkish_date(self):
        old = D - timedelta(days=20)
        months = ["Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran", "Temmuz", "Ağustos", "Eylül", "Ekim",
                  "Kasım", "Aralık"]
        text = (f"## Active Threads\n\n### Ekmek\nNerede kaldık ({old.day} {months[old.month - 1]}): maya canlandı. "
                f"Açık: Bende: çavdar unuyla deneme ekmeği pişir. Kaynak: [[notes/bread]].\n")
        (self.vault / "Threads.md").write_text(text, encoding="utf-8")
        env = self.nenv()
        self.cmd(env, "night run")
        imp = self.props(env, "improve")
        self.assertEqual(len(imp), 1)
        self.assertIn("Ekmek has not moved for 20 days", imp[0]["text"])

    def test_tr_it_stop_words_never_link_reason(self):
        for w in ("bizim", "için", "olan", "sonra", "adet", "cosa", "della", "sono"):
            self.assertNotIn(w, N.words(w.capitalize()), w)
        notes = self.vault / "notes"
        notes.joinpath("tr-a.md").write_text("# A\n\nBizim için olan sonra adet şekilde gerekli.\n", encoding="utf-8")
        notes.joinpath("tr-b.md").write_text("# B\n\nBizim için olan sonra adet şekilde gerekli.\n", encoding="utf-8")
        notes.joinpath("it-a.md").write_text("# C\n\nCosa della sono anche quando sempre.\n", encoding="utf-8")
        notes.joinpath("it-b.md").write_text("# D\n\nCosa della sono anche quando sempre.\n", encoding="utf-8")
        env = self.nenv()
        self.cmd(env, "night run")
        for p in self.props(env, "link"):
            paths = {s["path"] for s in p["sources"]}
            self.assertFalse(paths & {"notes/tr-a.md", "notes/tr-b.md", "notes/it-a.md", "notes/it-b.md"}, p)

    def test_generic_word_cut_by_document_frequency(self):
        notes = self.vault / "notes"
        for i in range(30):
            notes.joinpath(f"n{i}.md").write_text(f"# Note {i}\n\nfrobnik quuxal zibber topic{i}\n",
                                                  encoding="utf-8")
        env = self.nenv()
        self.cmd(env, "night run")
        for p in self.props(env, "link"):
            self.assertFalse({"frobnik", "quuxal", "zibber"} & set(p["words"]), p)


class DemoNight(Base):
    def test_demo_vault_produces_every_kind(self):
        env = self.env(demo="on")
        out = CMD.handler(env)("night run")
        self.assertIn("(demo data)", out)
        r = self.call(env, "kokpit_night", {"action": "list", "limit": 50})
        self.assertEqual({p["kind"] for p in r["proposals"]}, {"todo", "improve", "link", "idea", "news"})
        listing = CMD.handler(env)("night")
        self.assertLessEqual(max(len(line) for line in listing.splitlines()), 160)
        self.assertNotIn("—", listing)

    def test_demo_copy_refreshes_on_version_change(self):
        env = self.env(demo="on")
        root = env.state()["vault"].parent
        (root / ".demo-version").write_text("old\n", encoding="utf-8")
        import shutil
        shutil.rmtree(root / "vault" / "news")
        self.assertTrue((env.state()["vault"] / "news").is_dir())
        self.assertTrue((env.state()["vault"] / "daily" / f"{D.isoformat()}.md").is_file())

    def test_overview_has_night_card(self):
        env = self.env(demo="on")
        CMD.handler(env)("night run")
        o = T.overview(env)
        self.assertTrue(o["night"]["ok"], o["night"])
        self.assertLessEqual(len(o["night"]["proposals"]), 3)
        for f in ("desktop/plugin.js", "dashboard/dist/index.js"):
            self.assertIn("data.night", (PLUGIN_DIR / f).read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
