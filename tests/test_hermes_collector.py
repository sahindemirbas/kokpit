"""Tests for the plugin's opt-in collector core and personal news briefing (collector.py).

Synthetic feeds and fakes only: fetch is replaced in every test, nothing touches the network or needs a key.
"""
from __future__ import annotations

import gzip
import importlib
import io
import json
import os
import sqlite3
import sys
import unittest
from datetime import date, datetime, timedelta, timezone
from email.utils import format_datetime
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests.test_hermes_plugin import CMD, PLUGIN_DIR, C, H, P, T, Base, FakeCtx  # noqa: E402

K = importlib.import_module(P.__name__ + ".collector")
B = importlib.import_module(P.__name__ + ".brief")
N = importlib.import_module(P.__name__ + ".night")

NOW = datetime.now(timezone.utc)
RSS_URL = "https://feeds.example.org/ai.rss"
ATOM_URL = "https://news.example.net/data.atom"
BAD_URL = "https://broken.example.com/feed"


def rss(items) -> str:
    body = "".join(
        f"<item><title>{t}</title><link>{u}</link><description><![CDATA[<p>{d}</p>]]></description>"
        f"<category>ai</category><pubDate>{format_datetime(p)}</pubDate></item>" for t, u, d, p in items)
    return f'<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel><title>AI</title>{body}</channel></rss>'


def atom(items) -> str:
    body = "".join(
        f'<entry><title>{t}</title><link rel="alternate" href="{u}"/><summary type="html">&lt;b&gt;{d}&lt;/b&gt;</summary>'
        f"<updated>{p.isoformat()}</updated></entry>" for t, u, d, p in items)
    return f'<?xml version="1.0" encoding="utf-8"?><feed xmlns="http://www.w3.org/2005/Atom"><title>Data</title>{body}</feed>'


RSS = rss([
    ("Local models get faster on small boards", "https://a.example/1",
     "A local model now answers in two seconds on a small board. Second sentence here. Third.", NOW - timedelta(hours=2)),
    ("Agents and evaluation: a new benchmark", "https://a.example/2",
     "The benchmark covers 200 tasks. It reports early stops.", NOW - timedelta(hours=5)),
    ("Shared story", "https://shared.example/x", "Same URL in both feeds.", NOW - timedelta(hours=3)),
    ("Very old item", "https://a.example/old", "From last month.", NOW - timedelta(days=30)),
])
ATOM = atom([
    ("Data analyst demand steady", "https://b.example/1", "Hiring report for data roles.", NOW - timedelta(hours=1)),
    ("Shared story", "https://shared.example/x", "Same URL in both feeds.", NOW - timedelta(hours=3)),
    ("Pipelines with plain SQL", "https://b.example/2", "SQL first pipelines.", NOW - timedelta(hours=8)),
])
TOPICS = [{"name": "AI", "feeds": [RSS_URL]}, {"name": "Data", "feeds": [ATOM_URL]}]


def fake_fetch(pages: dict):
    calls = []

    def f(url, timeout=K.TIMEOUT, max_bytes=K.MAX_BYTES):
        calls.append(url)
        v = pages[url]
        if isinstance(v, Exception):
            raise v
        return v
    f.calls = calls
    return f


def no_network(*a, **k):
    raise AssertionError(f"fetch called: {a}")


class FakeLLM:
    def __init__(self, parsed=None, boom=False):
        self.parsed, self.boom, self.calls, self.kw = parsed, boom, 0, None

    def complete_structured(self, **kw):
        self.calls += 1
        self.kw = kw
        if self.boom:
            raise RuntimeError("provider down")
        return type("Result", (), {"parsed": self.parsed})()


class Briefing(Base):
    def benv(self, **extra):
        settings = {"kokpit_config": str(self.config()), "demo": "off", "briefing": True,
                    "briefing_topics": TOPICS, **extra}
        return self.env(**settings)

    def store(self):
        return self.tmp / "plugin-data" / "collector.db"

    def q(self, sql, args=()):
        conn = sqlite3.connect(self.store())
        try:
            return conn.execute(sql, args).fetchall()
        finally:
            conn.close()

    # 1 -------------------------------------------------------------- default settings: zero network
    def test_default_settings_never_fetch(self):
        env = self.live_env()
        with mock.patch.object(K, "fetch", no_network), \
                mock.patch.object(K.urllib.request, "urlopen", no_network), mock.patch.object(K, "_open", no_network):
            for name in P.schemas.ALL:
                if name == "kokpit_capture":
                    continue  # needs text; it writes the vault, not the network
                self.call(env, name, {})
            self.call(env, "kokpit_briefing", {"action": "collect"})  # refused: module off
            self.assertIn("Kokpit brief", CMD.run(env, "brief"))
            CMD.run(env, "news")
            CMD.run(env, "news run")
            h = H.Hooks(env)
            h.on_session_start(session_id="s0")
            h.pre_llm_call(session_id="s0", user_message="garden timer", is_first_turn=True)
            h.post_tool_call(tool_name="terminal", args={"command": "ls"}, result="{}", session_id="s0")
            h.post_compaction(session_id="s0")
            h.on_session_finalize(session_id="s0", platform="cli")
            self.assertIsNone(h.last_collect, "collect_auto is off by default")
            T.overview(env)
            # briefing on with feeds, collect_auto left at its default: session start still makes no call
            h2 = H.Hooks(self.benv())
            h2.on_session_start(session_id="s1")
            self.assertIsNone(h2.last_collect)
        self.assertFalse(self.store().exists(), "no collector store before the user collects")

    # 2 -------------------------------------------------------------- module off
    def test_module_off_is_the_standard_disabled_error(self):
        r = self.call(self.live_env(), "kokpit_briefing", {})
        self.assertFalse(r["ok"])
        self.assertEqual(r["error"], "module disabled")
        self.assertIn("plugins.entries.kokpit.settings.briefing true", r["fix"])
        r = self.call(self.benv(briefing=False), "kokpit_briefing", {"action": "collect"})
        self.assertEqual(r["error"], "module disabled")

    # 3 -------------------------------------------------------------- collect and list
    def test_collect_then_list_from_plugin_store(self):
        env = self.benv()
        f = fake_fetch({RSS_URL: RSS, ATOM_URL: ATOM})
        before = self.call(env, "kokpit_briefing", {})
        self.assertEqual(before["status"], "never run")
        with mock.patch.object(K, "fetch", f):
            r = self.call(env, "kokpit_briefing", {"action": "collect"})
            self.assertTrue(r["ok"], r)
            self.assertEqual(r["status"], "ok")
            self.assertEqual(r["stored"], 5)  # old item dropped, shared URL once
            self.assertEqual(sorted(f.calls), sorted([RSS_URL, ATOM_URL]))
            again = self.call(env, "kokpit_briefing", {"action": "collect"})
            self.assertTrue(again["ok"], again)
        lst = self.call(env, "kokpit_briefing", {"action": "list"})
        self.assertEqual(lst["source"], "plugin store")
        self.assertEqual(lst["status"], "ok")
        self.assertEqual(lst["freshness"]["last_status"], "ok")
        self.assertLess(lst["freshness"]["age_hours"], 1)
        self.assertFalse(lst["freshness"]["stale"])
        urls = [i["url"] for i in lst["items"]]
        self.assertEqual(len(urls), len(set(urls)))
        self.assertEqual(len(urls), 5)
        heads = [i["topic"] for i in lst["items"] if i["headline"]]
        self.assertEqual(sorted(heads), ["AI", "Data"])
        conn = sqlite3.connect(self.store())
        n, distinct = conn.execute("SELECT COUNT(*), COUNT(DISTINCT url) FROM briefing_items WHERE day=?",
                                   (date.today().isoformat(),)).fetchone()
        self.assertEqual((n, distinct), (5, 5))
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM runs WHERE job='briefing' AND ok=1").fetchone()[0], 2)
        by_model = {r[0] for r in conn.execute("SELECT by_model FROM briefing_items")}
        self.assertEqual(by_model, {0})
        summary = conn.execute("SELECT summary FROM briefing_items WHERE url='https://a.example/1'").fetchone()[0]
        self.assertEqual(summary, "A local model now answers in two seconds on a small board. Second sentence here.")
        self.assertNotIn("<", "".join(r[0] for r in conn.execute("SELECT summary FROM briefing_items")))
        conn.close()
        only = self.call(env, "kokpit_briefing", {"topic": "data"})
        self.assertEqual({i["topic"] for i in only["items"]}, {"Data"})
        self.assertIn("plugin store", CMD.run(env, "news"))

    def test_keywords_filter_and_score(self):
        env = self.benv(briefing_topics=[{"name": "AI", "feeds": [RSS_URL], "keywords": ["benchmark"]}])
        with mock.patch.object(K, "fetch", fake_fetch({RSS_URL: RSS})):
            r = self.call(env, "kokpit_briefing", {"action": "collect"})
        self.assertEqual(r["stored"], 1)
        self.assertEqual(r["headlines"][0]["title"], "Agents and evaluation: a new benchmark")

    # 4 -------------------------------------------------------------- one feed fails
    def test_failed_feed_is_a_warning_not_a_failed_run(self):
        env = self.benv(briefing_topics=[{"name": "AI", "feeds": [BAD_URL, RSS_URL]}])
        with mock.patch.object(K, "fetch", fake_fetch({BAD_URL: TimeoutError("timed out"), RSS_URL: RSS})):
            r = self.call(env, "kokpit_briefing", {"action": "collect"})
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["status"], "ok")
        self.assertEqual(r["stored"], 3)
        self.assertEqual([x["url"] for x in r["failed_feeds"]], [BAD_URL])
        self.assertIn("TimeoutError", r["failed_feeds"][0]["error"])
        row = self.q("SELECT ok, warnings, info FROM runs ORDER BY id DESC")[0]
        self.assertEqual(row[0], 1)
        self.assertIn("broken.example.com", row[1])
        self.assertIn(BAD_URL, row[2])
        with mock.patch.object(K, "fetch", fake_fetch({BAD_URL: TimeoutError("timed out"), RSS_URL: RSS})):
            self.assertIn(f"Failed feed: {BAD_URL}", CMD.run(env, "news run"))

    def test_every_feed_failing_is_a_failed_run(self):
        env = self.benv(briefing_topics=[{"name": "AI", "feeds": [BAD_URL]}])
        with mock.patch.object(K, "fetch", fake_fetch({BAD_URL: OSError("down")})):
            r = self.call(env, "kokpit_briefing", {"action": "collect"})
        self.assertFalse(r["ok"])
        self.assertIn(BAD_URL, r["fix"])
        self.assertEqual(self.q("SELECT ok FROM runs")[0][0], 0)

    # 5 -------------------------------------------------------------- URL schemes and body cap
    def test_non_http_urls_are_rejected(self):
        for bad in ("file:///etc/passwd", "ftp://example.org/feed", "javascript:alert(1)", "data:text/xml,<rss/>",
                    "", "https://", "https://exa mple.org/x"):
            with self.assertRaises(ValueError, msg=bad):
                K.check_url(bad)
            with self.assertRaises(ValueError, msg=bad):
                K.fetch(bad)
        env = self.benv(briefing_topics=[])
        out = CMD.run(env, "news add AI file:///etc/passwd")
        self.assertIn("feed not added", out)
        out = CMD.run(env, "news add AI ftp://example.org/feed")
        self.assertIn("only http and https", out)
        topics, problems = K.parse_topics([{"name": "X", "feeds": ["file:///etc/passwd", RSS_URL]}])
        self.assertEqual(topics[0]["feeds"], [RSS_URL])
        self.assertTrue(any("feed dropped" in p for p in problems))

    def test_redirect_to_other_scheme_is_refused(self):
        h = K._Redirects()
        with self.assertRaises(ValueError):
            h.redirect_request(None, None, 302, "Found", {}, "file:///etc/passwd")
        op = K._opener()
        names = {type(x).__name__ for x in op.handlers}
        self.assertFalse(names & {"FileHandler", "FTPHandler", "DataHandler", "UnknownHandler"})

    def test_body_cap_and_gzip(self):
        class Resp(io.BytesIO):
            def __init__(self, data, headers):
                super().__init__(data)
                self.headers = headers

            def geturl(self):
                return RSS_URL

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        from email.message import Message
        plain = Message()
        plain["Content-Type"] = "application/rss+xml; charset=utf-8"
        big = b"x" * (K.MAX_BYTES + 5000)
        seen = {}

        def opener(req, timeout):
            seen["ua"] = req.get_header("User-agent")
            seen["timeout"] = timeout
            return Resp(big, plain)
        with mock.patch.object(K, "_open", opener):
            text = K.fetch(RSS_URL)
        self.assertEqual(len(text), K.MAX_BYTES)
        self.assertIn("Kokpit", seen["ua"])
        self.assertEqual(seen["timeout"], K.TIMEOUT)
        gz = Message()
        gz["Content-Encoding"] = "gzip"
        with mock.patch.object(K, "_open", lambda req, timeout: Resp(gzip.compress(big), gz)):
            self.assertEqual(len(K.fetch(RSS_URL, max_bytes=1000)), 1000)
        with mock.patch.object(K, "_open", lambda req, timeout: Resp(gzip.compress(RSS.encode()), gz)):
            self.assertEqual(len(K.parse(K.fetch(RSS_URL))), 4)

    def test_parse_rss_atom_and_refuse_dtd(self):
        self.assertEqual([i["url"] for i in K.parse(ATOM)], ["https://b.example/1", "https://shared.example/x",
                                                             "https://b.example/2"])
        self.assertEqual(K.parse(ATOM)[0]["summary"], "Hiring report for data roles.")
        with self.assertRaises(ValueError):
            K.parse('<?xml version="1.0"?><!DOCTYPE r [<!ENTITY a "aaaa">]><rss><channel></channel></rss>')
        evil = rss([("x", "javascript:alert(1)", "d", NOW)])
        self.assertEqual(K.parse(evil), [])

    def test_feed_and_item_caps(self):
        many = [{"name": f"T{i}", "feeds": [f"https://f{i}-{j}.example/rss" for j in range(5)]} for i in range(8)]
        topics, problems = K.parse_topics(many + [{"name": "extra", "feeds": [RSS_URL]}])
        self.assertEqual(len(topics), K.MAX_TOPICS)
        pages = {u: rss([(f"t{k}", f"https://i.example/{u[8:20]}/{k}", "d", NOW) for k in range(60)])
                 for t in topics for u in t["feeds"]}
        f = fake_fetch(pages)
        with mock.patch.object(K, "fetch", f):
            items, per, failed, warnings, fetched = K.gather(topics, 30, NOW)
        self.assertEqual(fetched, K.MAX_FEEDS_PER_RUN)
        self.assertEqual(len(f.calls), K.MAX_FEEDS_PER_RUN)
        self.assertTrue(any("feed cap" in w for w in warnings))
        self.assertLessEqual(len(items), 30)
        self.assertEqual(len(K.parse(pages[topics[0]["feeds"][0]])), K.MAX_ITEMS_PER_FEED)

    # 6 -------------------------------------------------------------- app database wins, never written
    def test_app_database_wins_and_is_never_written(self):
        conn = self.make_db()
        conn.execute("INSERT INTO briefing_items(day, topic, url, title, score, headline, summary, why) "
                     "VALUES (?,?,?,?,?,?,?,?)", (date.today().isoformat(), "Garden", "https://app.example/1",
                                                  "From the Kokpit app", 3, 1, "App summary.", "app"))
        conn.execute("INSERT INTO runs(day, job, started, finished, ok, rows, info, warnings) VALUES (?,?,?,?,?,?,?,?)",
                     (date.today().isoformat(), "briefing", "x", datetime.now().isoformat(timespec="seconds"), 1, 1,
                      "{}", "[]"))
        conn.commit()
        conn.close()
        before = (self.db.read_bytes(), os.stat(self.db).st_mtime_ns)
        env = self.benv()
        with mock.patch.object(K, "fetch", fake_fetch({RSS_URL: RSS, ATOM_URL: ATOM})):
            self.assertTrue(self.call(env, "kokpit_briefing", {"action": "collect"})["ok"])
        r = self.call(env, "kokpit_briefing", {})
        self.assertEqual(r["source"], "kokpit app database")
        self.assertEqual([i["title"] for i in r["items"]], ["From the Kokpit app"])
        self.assertEqual(r["freshness"]["last_status"], "ok")
        self.call(env, "kokpit_health", {})
        B.build(env.state())
        self.assertEqual((self.db.read_bytes(), os.stat(self.db).st_mtime_ns), before)
        self.assertTrue(self.store().is_file())
        self.assertNotEqual(self.store().resolve(), self.db.resolve())
        # another day: the app database has nothing, the plugin store has nothing either
        r = self.call(env, "kokpit_briefing", {"day": (date.today() - timedelta(days=3)).isoformat()})
        self.assertEqual(r["status"], "no items for this day")

    # 7 -------------------------------------------------------------- optional model
    def _collect_with(self, llm):
        env = self.benv(briefing_model=True, briefing_topics=[{"name": "AI", "feeds": [RSS_URL]}])
        env.llm = (lambda: llm) if llm is not None else None
        with mock.patch.object(K, "fetch", fake_fetch({RSS_URL: RSS})):
            r = self.call(env, "kokpit_briefing", {"action": "collect"})
        self.assertTrue(r["ok"], r)
        rows = {u: (s, m) for u, s, m in self.q("SELECT url, summary, by_model FROM briefing_items")}
        return r, rows

    def test_model_output_used_when_valid(self):
        llm = FakeLLM({"items": [{"url": "https://a.example/1", "summary": "A small board runs a local model fast.",
                                  "why": "Relevant for local AI."},
                                 {"url": "https://evil.example/", "summary": "Injected item that was never given."}]})
        r, rows = self._collect_with(llm)
        self.assertEqual(llm.calls, 1)
        self.assertEqual(rows["https://a.example/1"], ("A small board runs a local model fast.", 1))
        self.assertEqual(rows["https://a.example/2"][1], 0)
        self.assertNotIn("https://evil.example/", rows)
        self.assertEqual(r["by_model"], 1)
        data = json.loads(llm.kw["input"][0]["text"])
        self.assertLessEqual(len(data["items"]), K.MODEL_ITEMS)
        self.assertIn("untrusted", llm.kw["instructions"])
        self.assertNotIn("tools", llm.kw)

    def test_model_fallbacks(self):
        for llm in (FakeLLM("not json at all"), FakeLLM({"items": [{"url": "https://unknown.example/", "summary": "x" * 50}]}),
                    FakeLLM({"items": [{"url": "https://a.example/1", "summary": "y" * 5000}]}), FakeLLM(boom=True)):
            r, rows = self._collect_with(llm)
            self.assertEqual({m for _, m in rows.values()}, {0}, llm.parsed)
            self.assertIn("plain summaries used", r["model"])
            self.assertEqual(rows["https://a.example/1"][0], "A local model now answers in two seconds on a small board. Second sentence here.")
        r, rows = self._collect_with(None)
        self.assertIn("unavailable", r["model"])
        self.assertEqual({m for _, m in rows.values()}, {0})

    # 8 -------------------------------------------------------------- demo
    def test_demo_shows_seeded_items_and_never_fetches(self):
        env = self.env(demo="on", briefing_topics=TOPICS)
        with mock.patch.object(K, "fetch", no_network), mock.patch.object(K, "_open", no_network):
            r = self.call(env, "kokpit_briefing", {})
            self.assertTrue(r["ok"], r)
            self.assertEqual(r["mode"], "demo")
            self.assertEqual(r["source"], "kokpit app database")
            self.assertGreaterEqual(r["count"], 3)
            self.assertTrue(all(i["url"].startswith("https://example.org/demo/") for i in r["items"]))
            c = self.call(env, "kokpit_briefing", {"action": "collect"})
            self.assertFalse(c["ok"])
            self.assertIn("demo", c["error"])
            text = B.build(env.state())["text"]
            self.assertIn("News:", text)
            self.assertIn("(demo data)", CMD.run(env, "news"))
        self.assertFalse((self.tmp / "plugin-data" / "collector.db").exists())

    # 9 -------------------------------------------------------------- health
    def test_health_lists_plugin_job_only_after_a_run(self):
        env = self.benv()
        h = self.call(env, "kokpit_health", {})
        self.assertEqual(h["plugin_jobs"], [])
        self.assertNotIn("briefing", json.dumps(h["jobs"]))
        with mock.patch.object(K, "fetch", fake_fetch({RSS_URL: RSS, ATOM_URL: ATOM})):
            self.call(env, "kokpit_briefing", {"action": "collect"})
        h = self.call(env, "kokpit_health", {})
        self.assertEqual([(j["job"], j["source"], j["status"], j["records"]) for j in h["plugin_jobs"]],
                         [("briefing", "plugin", "ok", 5)])
        self.assertIn("Plugin job briefing (collected by this plugin): ok", CMD.run(env, "health"))

    # brief, night and commands ---------------------------------------
    def test_brief_news_block_and_budget(self):
        env = self.benv()
        self.assertNotIn("News:", B.build(env.state())["text"])
        with mock.patch.object(K, "fetch", fake_fetch({RSS_URL: RSS, ATOM_URL: ATOM})):
            self.call(env, "kokpit_briefing", {"action": "collect"})
        out = B.build(env.state())
        self.assertIn("News:", out["text"])
        self.assertIn("- AI: ", out["text"])
        self.assertLessEqual(out["chars"], B.LIMIT)
        self.assertNotIn("News:", B.build(self.benv(briefing=False).state())["text"])

    def test_night_sees_plugin_store_matches(self):
        env = self.benv(briefing_topics=[{"name": "Home", "feeds": [RSS_URL]}])
        feed = rss([("Garden timer relay tips", "https://g.example/1", "Relay wiring for a timer.", NOW)])
        with mock.patch.object(K, "fetch", fake_fetch({RSS_URL: feed})):
            r = self.call(env, "kokpit_briefing", {"action": "collect"})
        self.assertEqual(r["matched_projects"], 1)
        st = env.state()
        out = N.run(st)
        self.assertIn("plugin store", json.dumps(out))
        props = N.load(st["night_store"])["proposals"]
        self.assertTrue(any(p["kind"] == "news" and "Garden timer" in p["text"] for p in props))

    def test_news_commands_write_settings_or_print_the_line(self):
        env = self.benv(briefing_topics=[])
        out = CMD.run(env, "news add AI https://feeds.example.org/ai.rss local llm")
        self.assertIn("hermes config set plugins.entries.kokpit.settings.briefing_topics", out)
        self.assertEqual(env.setting("briefing_topics", [])[0]["keywords"], ["local", "llm"])
        saved = {}
        env.set_config = lambda k, v: saved.__setitem__(k, v)
        out = CMD.run(env, "news add AI https://other.example/rss")
        self.assertIn("Saved", out)
        self.assertEqual(saved["briefing_topics"][0]["feeds"], [RSS_URL, "https://other.example/rss"])
        out = CMD.run(env, "news off")
        self.assertEqual(saved["briefing"], False)
        self.assertFalse(self.call(env, "kokpit_briefing", {})["ok"])
        CMD.run(env, "news on")
        self.assertEqual(saved["briefing"], True)
        self.assertIn("removed", CMD.run(env, "news remove AI"))
        self.assertEqual(saved["briefing_topics"], [])
        self.assertIn("no topic named", CMD.run(env, "news remove Nope"))
        r = self.call(env, "kokpit_briefing", {"action": "collect"})
        self.assertEqual(r["error"], "no briefing topic configured")

    def test_collect_cli_registered_and_overview_lists_briefing(self):
        class Ctx(FakeCtx):
            def __init__(self):
                super().__init__()
                self.cli = {}

            def register_cli_command(self, name, help_, setup, handler):
                self.cli[name] = handler
        ctx = Ctx()
        P.register(ctx)
        self.assertIn("kokpit-collect", ctx.cli)
        self.assertIn("news", ctx.commands["kokpit"]["args_hint"])
        self.assertIn("kokpit_briefing", ctx.tools)
        mods = {m["module"]: m for m in T.modules(self.live_env().state())}
        self.assertFalse(mods["briefing"]["enabled"])

    def test_store_tables_match_the_app_schema(self):
        def cols(script, table):
            conn = sqlite3.connect(":memory:")
            try:
                conn.executescript(script)
                return [r[1] for r in conn.execute(f'PRAGMA table_info("{table}")')]
            finally:
                conn.close()
        app = {"briefing_items": (ROOT / "kokpit" / "modules" / "briefing" / "schema.sql").read_text(encoding="utf-8"),
               "runs": (ROOT / "kokpit" / "modules" / "health" / "schema.sql").read_text(encoding="utf-8")}
        for table, script in app.items():
            self.assertEqual(cols(K.SCHEMA, table), cols(script, table), table)

    def test_temp_store_is_reported(self):
        env = C.Env(lambda k, default=None: {"kokpit_config": str(self.config()), "demo": "off", "briefing": True,
                                             "briefing_topics": TOPICS}.get(k, default))
        with mock.patch.object(C.tempfile, "gettempdir", return_value=str(self.tmp / "tmpdir")), \
                mock.patch.dict(sys.modules, {"plugins": None, "plugins.plugin_storage": None}):
            with mock.patch.object(K, "fetch", fake_fetch({RSS_URL: RSS, ATOM_URL: ATOM})):
                r = self.call(env, "kokpit_briefing", {"action": "collect"})
        self.assertTrue(r["ok"], r)
        self.assertFalse(r["store"]["durable"])
        self.assertIn("temporary", r["store"]["note"])


class CollectAuto(Briefing):
    """collect_auto: background collection at session start (Part A). Injected clock, joined threads."""

    def start(self, h, sid="s"):
        h.last_collect = None
        h.on_session_start(session_id=sid)
        if h.last_collect is not None:
            h.last_collect.join(30)
            self.assertFalse(h.last_collect.is_alive())
        return h.last_collect

    def test_runs_once_then_waits_for_the_threshold(self):
        env = self.benv(collect_auto=True)
        h = H.Hooks(env)
        f = fake_fetch({RSS_URL: RSS, ATOM_URL: ATOM})
        with mock.patch.object(K, "fetch", f):
            self.assertIsNotNone(self.start(h, "a"))
            self.assertEqual(sorted(f.calls), sorted([RSS_URL, ATOM_URL]))
            self.assertEqual(self.q("SELECT COUNT(*) FROM runs WHERE job='briefing' AND ok=1")[0][0], 1)
            n = len(f.calls)
            h.now = lambda: datetime.now() + timedelta(hours=19)
            self.assertIsNone(self.start(h, "b"), "within 20 hours: no second collection")
            self.assertEqual(len(f.calls), n)
            h.now = lambda: datetime.now() + timedelta(hours=21)
            self.assertIsNotNone(self.start(h, "c"))
            self.assertEqual(len(f.calls), 2 * n)
        self.assertEqual(self.q("SELECT COUNT(*) FROM runs WHERE job='briefing' AND ok=1")[0][0], 2)

    def test_demo_or_module_off_never_fetches(self):
        with mock.patch.object(K, "fetch", no_network), mock.patch.object(K, "_open", no_network):
            for env in (self.env(demo="on", collect_auto=True, briefing=True, briefing_topics=TOPICS),
                        self.benv(collect_auto=True, briefing=False),
                        self.benv(collect_auto=True, briefing_topics=[])):
                h = H.Hooks(env)
                self.assertIsNone(self.start(h))
        self.assertFalse(self.store().exists())

    def test_failure_in_thread_is_a_failed_run_not_an_exception(self):
        env = self.benv(collect_auto=True)
        h = H.Hooks(env)
        with mock.patch.object(K, "collect", side_effect=RuntimeError("boom")):
            self.assertIsNone(h.on_session_start(session_id="x"))
            h.last_collect.join(30)
        rows = self.q("SELECT ok, info FROM runs WHERE job='briefing'")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][0], 0)
        self.assertIn("boom", rows[0][1])
        # a failed run is retried only after AUTO_RETRY_HOURS, so a broken feed is not hit every session
        self.assertFalse(K.due(self.store(), "briefing", datetime.now() + timedelta(hours=1)))
        self.assertTrue(K.due(self.store(), "briefing", datetime.now() + timedelta(hours=K.AUTO_RETRY_HOURS + 1)))

    def test_one_thread_at_a_time(self):
        env = self.benv(collect_auto=True)
        h = H.Hooks(env)
        h._collect_running = True
        with mock.patch.object(K, "fetch", no_network):
            h.on_session_start(session_id="y")
        self.assertIsNone(h.last_collect)


if __name__ == "__main__":
    unittest.main()
