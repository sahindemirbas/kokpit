"""Briefing digest: fake local servers stand in for the feeds, the model and the TTS provider."""
from __future__ import annotations

import json
import os
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest import mock

from kokpit import config as cfgmod, jobs, scheduler
from kokpit.db import connect
from kokpit.modules.briefing import api, collect, feeds, match, tts
from kokpit.modules.connections import api as conn_api

NOW = datetime(2026, 10, 30, 12, 0, tzinfo=timezone.utc)
KEY = "sk-" + "tts" + "-" + "y" * 6


def rfc(d):
    return (NOW - timedelta(days=d)).strftime("%a, %d %b %Y %H:%M:%S +0000")


RSS = f"""<?xml version="1.0"?><rss version="2.0"><channel><title>t</title>
<item><title>Release 3.0 of the pytools library</title><link>http://x/1</link><description>&lt;p&gt;Big &lt;b&gt;release&lt;/b&gt; notes for pytools. {'long ' * 80}&lt;/p&gt;</description><category>python</category><pubDate>{rfc(1)}</pubDate></item>
<item><title>Cooking tips</title><link>http://x/2</link><description>unrelated</description><pubDate>{rfc(1)}</pubDate></item>
<item><title>Old security release</title><link>http://x/3</link><description>old</description><pubDate>{rfc(30)}</pubDate></item>
<item><title>Security release for the gardening toolkit</title><link>http://x/4</link><description>fix</description><pubDate>{rfc(2)}</pubDate></item>
</channel></rss>"""
ATOM = f"""<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom"><title>a</title>
<entry><title>Atom release of widgets</title><link rel="alternate" href="http://x/5"/><summary>widget summary</summary><category term="widgets"/><updated>{(NOW - timedelta(days=1)).isoformat()}</updated></entry></feed>"""


class FeedHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        body, code = {"/rss": (RSS, 200), "/atom": (ATOM, 200), "/bad": ("<rss", 200)}.get(self.path, ("no", 404))
        self.send_response(code); self.end_headers(); self.wfile.write(body.encode())

    def do_POST(self):
        n = int(self.headers["Content-Length"]); body = json.loads(self.rfile.read(n))
        Srv.hits.append({"path": self.path, "auth": self.headers.get("Authorization"), "body": body})
        if self.path.endswith("/audio/speech"):
            self.send_response(200); self.end_headers(); self.wfile.write(b"ID3fakeaudio"); return
        t = json.loads(body["messages"][1]["content"])["title"]
        out = json.dumps({"choices": [{"message": {"content": json.dumps({"summary": "S:" + t, "why": "W:" + t})}}]}).encode()
        self.send_response(200); self.end_headers(); self.wfile.write(out)

    def log_message(self, *a):
        pass


class Srv:
    hits: list = []


def make_cfg(tmp: Path, base: str, model=False, tts_on=False, brain=None) -> cfgmod.Config:
    (tmp / "vault").mkdir(exist_ok=True)
    if brain:
        (tmp / "vault" / "Threads.md").write_text(brain)
    p = tmp / "kokpit.toml"
    p.write_text(f'''[modules]
briefing = true
brain = {str(bool(brain)).lower()}
[brain]
vault_path = "vault"
[health]
db_path = "t.db"
[model]
enabled = {str(model).lower()}
base_url = "{base}/v1"
model = "fake-1"
[model.uses]
digest = true
[briefing]
max_per_topic = 5
[[briefing.topics]]
name = "open source releases"
feeds = ["{base}/rss", "{base}/atom", "{base}/bad"]
keywords = ["release", "pytools"]
[briefing.tts]
provider = "{'openai_compatible' if tts_on else 'none'}"
base_url = "{base}/v1"
model = "tts-fake"
voice = "v1"
''')
    return cfgmod.load(p)


THREADS = """## Active Threads

### Pytools migration
Status: moving the build to pytools 3.
Open: Sende: test it.
Source: [[notes/Projects/pytools-port]]

## Closed Threads
"""


class BriefingBase(unittest.TestCase):
    def setUp(self):
        self.srv = HTTPServer(("127.0.0.1", 0), FeedHandler)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.srv.server_port}"
        Srv.hits = []
        self.tmp = Path(tempfile.mkdtemp())
        self.env = mock.patch.dict(os.environ, {"KOKPIT_MODEL_API_KEY": KEY, "KOKPIT_TTS_API_KEY": KEY})
        self.env.start()

    def tearDown(self):
        self.env.stop(); self.srv.shutdown(); self.srv.server_close()


class ParseTests(BriefingBase):
    def test_rss_and_atom(self):
        r = feeds.parse(RSS)
        self.assertEqual(len(r), 4)
        self.assertEqual(r[0]["tags"], ["python"])
        self.assertNotIn("<", r[0]["summary"])
        a = feeds.parse(ATOM)[0]
        self.assertEqual((a["url"], a["tags"], a["title"]), ("http://x/5", ["widgets"], "Atom release of widgets"))
        self.assertIsNotNone(a["published"])

    def test_dtd_refused(self):
        with self.assertRaises(ValueError):
            feeds.parse('<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "b">]><rss/>')


class CollectTests(BriefingBase):
    def test_plain_digest_without_model(self):
        cfg = make_cfg(self.tmp, self.base)
        items, info, warns = collect.build(cfg, now=NOW, threads=[])
        titles = [i["title"] for i in items]
        self.assertIn("Atom release of widgets", titles)
        self.assertNotIn("Cooking tips", titles)           # no keyword
        self.assertNotIn("Old security release", titles)   # too old
        self.assertTrue(all(not i["by_model"] and i["project"] is None for i in items))
        self.assertLessEqual(len(items[0]["summary"]), 203)
        self.assertTrue(any("/bad" in w or "127.0.0.1" in w for w in warns))
        self.assertEqual(Srv.hits, [])  # model never called
        self.assertEqual(sum(i["headline"] for i in items), 1)  # one section, one headline

    def test_brain_matching_tags_and_orders_first(self):
        cfg = make_cfg(self.tmp, self.base, brain=THREADS)
        items, info, _ = collect.build(cfg, now=NOW)
        self.assertEqual(info["brain_threads"], 1)
        first = items[0]
        self.assertEqual(first["project"], "pytools-port")
        self.assertIn("matches project pytools-port: keyword pytools in thread Pytools migration", first["reason"])
        self.assertTrue(all(i["project"] is None for i in items[1:]))
        self.assertGreater(len(items), 1)  # untagged items are kept, not hidden
        self.assertEqual(sum(i["headline"] for i in items), 2)  # projects section + topic section

    def test_model_digest_declared_fields_only(self):
        cfg = make_cfg(self.tmp, self.base, model=True)
        items, info, _ = collect.build(cfg, now=NOW, threads=[])
        self.assertTrue(all(i["by_model"] and i["summary"].startswith("S:") and i["why"].startswith("W:") for i in items))
        sent = json.loads(Srv.hits[0]["body"]["messages"][1]["content"])
        self.assertEqual(set(sent), {"title", "tags", "summary", "facts"})
        self.assertEqual(Srv.hits[0]["auth"], "Bearer " + KEY)

    def test_model_failure_falls_back(self):
        cfg = make_cfg(self.tmp, self.base, model=True)
        with mock.patch.object(collect.client, "complete", side_effect=collect.client.ModelError("x")):
            items, _, warns = collect.build(cfg, now=NOW, threads=[])
        self.assertTrue(items and all(not i["by_model"] and i["summary"] for i in items))
        self.assertTrue(any("three failures" in w for w in warns))

    def test_job_stores_and_api(self):
        cfg = make_cfg(self.tmp, self.base, brain=THREADS)
        with mock.patch.object(collect.feeds.fetch, "get", side_effect=lambda u, **k: __import__("urllib.request").request.urlopen(u).read().decode()):
            ok = jobs.run(cfg, "briefing")
        self.assertTrue(ok)
        v = api.view(cfg)
        self.assertTrue(v["sections"][0]["tagged"])
        self.assertEqual(v["sections"][0]["items"][0]["project"], "pytools-port")
        self.assertIn("For project pytools-port", v["speech"])
        self.assertIsNone(v["audio"])
        conn = connect(cfg.db, readonly=True)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM runs WHERE job='briefing'").fetchone()[0], 1)

    def test_no_topics(self):
        cfg = make_cfg(self.tmp, self.base)
        cfg.briefing["topics"] = []
        conn = connect(cfg.db)
        self.assertEqual(collect.collect(cfg, conn)[0], 0)
        self.assertIn("topic", api.view(cfg)["reason"])

    def test_off_by_default_and_schedule(self):
        self.assertFalse(cfgmod.Config(path=self.tmp / "k.toml").modules.get("briefing", False))
        cfg = make_cfg(self.tmp, self.base)
        self.assertIn("briefing", {e.job for e in scheduler.entries_for(cfg)})
        cfg.modules["briefing"] = False
        self.assertNotIn("briefing", {e.job for e in scheduler.entries_for(cfg)})
        self.assertFalse(api.view(cfg)["enabled"])


class TtsTests(BriefingBase):
    def test_speak_writes_file_and_records(self):
        cfg = make_cfg(self.tmp, self.base, tts_on=True)
        name = tts.speak(cfg, "2026-10-30", "hello digest")
        self.assertEqual((tts.audio_path(cfg, "2026-10-30")).read_bytes(), b"ID3fakeaudio")
        self.assertEqual(Srv.hits[0]["body"]["input"], "hello digest")
        self.assertEqual(Srv.hits[0]["auth"], "Bearer " + KEY)
        self.assertEqual(api.audio_file(cfg).name, name)
        row = connect(cfg.db, readonly=True).execute("SELECT info FROM runs WHERE job='tts:briefing'").fetchone()[0]
        self.assertNotIn(KEY, row)

    def test_provider_none_and_missing_key(self):
        cfg = make_cfg(self.tmp, self.base)
        with self.assertRaises(RuntimeError):
            tts.speak(cfg, "d", "x")
        cfg = make_cfg(self.tmp, self.base, tts_on=True)
        with mock.patch.dict(os.environ, {"KOKPIT_TTS_API_KEY": ""}):
            with self.assertRaises(RuntimeError):
                tts.speak(cfg, "d", "x")
        self.assertEqual(Srv.hits, [])

    def test_collect_with_tts_and_connection_row(self):
        cfg = make_cfg(self.tmp, self.base, tts_on=True)
        conn = connect(cfg.db)
        n, info, warns = collect.collect(cfg, conn, fetcher=lambda u: feeds.parse(RSS if u.endswith("rss") else ATOM) if "bad" not in u else [], now=NOW, threads=[])
        self.assertGreater(n, 0)
        self.assertTrue(info["audio"].endswith(".mp3"))
        self.assertEqual(api.view(cfg)["audio"], "/api/briefing/audio")
        item = [c for c in conn_api.view(cfg)["connections"] if c["id"] == "tts-briefing"][0]
        self.assertEqual(item["status"], "ok")
        self.assertIn("summary text", item["writes"])
        self.assertNotIn(KEY, json.dumps(item))

    def test_connection_row_off(self):
        cfg = make_cfg(self.tmp, self.base)
        item = [c for c in conn_api.view(cfg)["connections"] if c["id"] == "tts-briefing"][0]
        self.assertEqual((item["status"], item["enabled"]), ("off", False))


class MatchTests(unittest.TestCase):
    def test_no_match_no_tag(self):
        it = {"title": "Weather in Rome", "tags": []}
        th = match.brain_threads({"threads": [{"title": "Pytools migration", "status": "build work", "source": "a/b"}]})
        self.assertIsNone(match.project_match(it, th))

    def test_freshness_orders(self):
        self.assertGreater(match.freshness(NOW, NOW, 7), match.freshness(NOW - timedelta(days=5), NOW, 7))


if __name__ == "__main__":
    unittest.main()
