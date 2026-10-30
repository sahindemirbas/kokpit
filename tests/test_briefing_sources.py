"""Briefing sources and visuals: fake local GitHub / Hugging Face / model servers, strict visual validation, API shape."""
from __future__ import annotations

import json
import os
import tempfile
import threading
import unittest
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest import mock
from urllib.parse import parse_qs, urlparse

from kokpit import config as cfgmod
from kokpit.db import connect
from kokpit.modules.briefing import api, collect, match, sources, visual

NOW = datetime(2026, 10, 30, 12, 0, tzinfo=timezone.utc)
KEY = "sk-" + "vis" + "-" + "z" * 6

GH = {"total_count": 2, "items": [
    {"full_name": "acme/fastgrid", "html_url": "https://github.com/acme/fastgrid", "description": "A fast grid layout engine for terminals. Written in Rust.",
     "stargazers_count": 1840, "forks_count": 52, "language": "Rust", "topics": ["terminal", "layout"],
     "created_at": "2026-10-20T08:00:00Z", "pushed_at": "2026-10-29T08:00:00Z"},
    {"full_name": "acme/tinyqueue", "html_url": "https://github.com/acme/tinyqueue", "description": None,
     "stargazers_count": 310, "forks_count": 4, "language": None, "topics": [],
     "created_at": "2026-10-28T08:00:00Z", "pushed_at": "2026-10-29T10:00:00Z"},
    {"html_url": "broken"}]}
HF_MODELS = [{"id": "lab/voice-small", "likes": 420, "downloads": 12000, "pipeline_tag": "text-to-speech",
              "createdAt": "2026-10-01T00:00:00.000Z", "lastModified": "2026-10-28T00:00:00.000Z",
              "tags": ["tts", "license:mit"], "safetensors": {"total": 820000000}},
             {"id": "lab/no-params", "likes": 3, "pipeline_tag": "text-to-speech"}]
HF_PAPERS = [{"paper": {"id": "2610.00001", "title": "Sparse Attention at Scale", "summary": "We study sparse attention. It is 3 times faster.",
                        "upvotes": 77, "publishedAt": "2026-10-29T00:00:00.000Z"}}]


class Api(BaseHTTPRequestHandler):
    seen: list = []
    model_reply: dict = {}

    def do_GET(self):
        Api.seen.append({"path": self.path, "auth": self.headers.get("Authorization")})
        u = urlparse(self.path)
        body = {"/search/repositories": GH, "/api/models": HF_MODELS, "/api/daily_papers": HF_PAPERS}.get(u.path)
        if u.path == "/rss":
            body = None
            txt = ('<?xml version="1.0"?><rss version="2.0"><channel><title>t</title><item><title>Grid layout news</title>'
                   '<link>http://x/g</link><description>First sentence here. Second one is not kept.</description>'
                   '<pubDate>Thu, 29 Oct 2026 10:00:00 +0000</pubDate></item></channel></rss>')
            self.send_response(200); self.end_headers(); self.wfile.write(txt.encode()); return
        if body is None:
            self.send_response(404); self.end_headers(); return
        self.send_response(200); self.end_headers(); self.wfile.write(json.dumps(body).encode())

    def do_POST(self):
        n = int(self.headers["Content-Length"]); req = json.loads(self.rfile.read(n))
        t = json.loads(req["messages"][1]["content"])["title"]
        reply = Api.model_reply.get(t, {"summary": "S:" + t, "why": "W:" + t, "visual": None})
        out = json.dumps({"choices": [{"message": {"content": json.dumps(reply)}}]}).encode()
        self.send_response(200); self.end_headers(); self.wfile.write(out)

    def log_message(self, *a):
        pass


def make_cfg(tmp: Path, base: str, model=False, topics=False, brain=None) -> cfgmod.Config:
    (tmp / "vault").mkdir(exist_ok=True)
    if brain:
        (tmp / "vault" / "Threads.md").write_text(brain)
    topic = f'[[briefing.topics]]\nname = "layouts"\nfeeds = ["{base}/rss"]\nkeywords = ["grid"]\n' if topics else ""
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
max_age_days = 7
{topic}[briefing.github]
queries = ["layout engine"]
language = "rust"
base_url = "{base}"
[briefing.huggingface]
models = ["speech"]
papers = true
base_url = "{base}"
''')
    return cfgmod.load(p)


THREADS = """## Active Threads

### Terminal layout rewrite
Status: replacing the old renderer.
Source: [[notes/Projects/layout-rewrite]]

## Closed Threads
"""


class Base(unittest.TestCase):
    def setUp(self):
        self.srv = HTTPServer(("127.0.0.1", 0), Api)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.srv.server_port}"
        Api.seen, Api.model_reply = [], {}
        self.tmp = Path(tempfile.mkdtemp())
        self.env = mock.patch.dict(os.environ, {"KOKPIT_MODEL_API_KEY": KEY}, clear=False)
        self.env.start()
        os.environ.pop("KOKPIT_GITHUB_TOKEN", None)

    def tearDown(self):
        self.env.stop(); self.srv.shutdown(); self.srv.server_close()


class SourceTests(Base):
    def test_github_facts_measured_only(self):
        cfg = make_cfg(self.tmp, self.base)
        items, warns = sources.github_items(cfg.briefing["github"], NOW)
        self.assertEqual(warns, [])
        self.assertEqual([i["title"] for i in items], ["acme/fastgrid", "acme/tinyqueue"])
        e = items[0]["extra"]
        self.assertEqual((e["stars"], e["forks"], e["age_days"], e["lang"]), (1840, 52, 10, "Rust"))
        self.assertNotIn("stars_today", e)            # not measurable from one search call: never invented
        self.assertNotIn("lang", items[1]["extra"])   # missing language is left out, not guessed
        q = parse_qs(urlparse(Api.seen[0]["path"]).query)["q"][0]
        self.assertIn("created:>=2026-09-30", q)
        self.assertIn("language:rust", q)
        self.assertIsNone(Api.seen[0]["auth"])        # no token needed

    def test_github_token_from_env(self):
        cfg = make_cfg(self.tmp, self.base)
        with mock.patch.dict(os.environ, {"KOKPIT_GITHUB_TOKEN": "gh-" + "t" * 8}):
            sources.github_items(cfg.briefing["github"], NOW)
        self.assertEqual(Api.seen[0]["auth"], "Bearer gh-" + "t" * 8)

    def test_huggingface_models_and_papers(self):
        cfg = make_cfg(self.tmp, self.base)
        items, warns = sources.huggingface_items(cfg.briefing["huggingface"], NOW)
        self.assertEqual(warns, [])
        m, bare, paper = items
        self.assertEqual((m["extra"]["likes"], m["extra"]["downloads"], m["extra"]["params"], m["extra"]["params_b"]), (420, 12000, 820000000, 0.82))
        self.assertEqual(m["tags"], ["tts"])
        self.assertNotIn("params", bare["extra"])
        self.assertNotIn("downloads", bare["extra"])
        self.assertEqual((paper["extra"]["kind"], paper["extra"]["upvotes"]), ("paper", 77))
        self.assertTrue(paper["url"].endswith("/papers/2610.00001"))

    def test_source_failure_is_a_warning(self):
        cfg = make_cfg(self.tmp, "http://127.0.0.1:9")
        items, warns = sources.github_items(cfg.briefing["github"], NOW)
        self.assertEqual(items, [])
        self.assertTrue(warns and warns[0].startswith("github / layout engine"))

    def test_off_unless_configured(self):
        cfg = make_cfg(self.tmp, self.base)
        off = {**cfg.briefing["github"], "queries": []}
        self.assertEqual(sources.github_items(off, NOW), ([], []))
        self.assertEqual(Api.seen, [])
        p = self.tmp / "plain.toml"
        p.write_text("[modules]\nbriefing = true\n")
        a = cfgmod.load(p).briefing
        self.assertEqual((a["github"]["queries"], a["huggingface"]["models"], a["huggingface"]["papers"]), ([], [], False))


class FallbackTests(Base):
    def test_first_sentence(self):
        self.assertEqual(match.first_sentence("One thing. Two things."), "One thing.")
        self.assertEqual(match.first_sentence("v1.2 is out. Next"), "v1.2 is out.")
        self.assertLessEqual(len(match.first_sentence("word " * 100)), 183)

    def test_no_model_build(self):
        cfg = make_cfg(self.tmp, self.base, topics=True, brain=THREADS)
        threads = match.brain_threads({"threads": [{"title": "Terminal layout rewrite", "source": "notes/Projects/layout-rewrite", "status": "x"}]})
        items, info, warns = collect.build(cfg, now=NOW, threads=threads)
        by = {i["title"]: i for i in items}
        self.assertEqual(by["Grid layout news"]["summary"], "First sentence here.")
        self.assertTrue(all(not i["by_model"] and i["visual"] is None for i in items))
        self.assertEqual(by["acme/fastgrid"]["topic"], "GitHub")
        self.assertEqual(by["lab/voice-small"]["topic"], "Hugging Face")
        self.assertEqual(by["acme/fastgrid"]["extra"]["personal_context"], ["Terminal layout rewrite"])
        self.assertEqual(info["topics"], {"layouts": 1, "GitHub": 2, "Hugging Face": 3})

    def test_github_only_config_collects(self):
        cfg = make_cfg(self.tmp, self.base)
        conn = connect(cfg.db)
        n, info, warns = collect.collect(cfg, conn, now=NOW, threads=[])
        self.assertEqual(n, 5)


class VisualTests(unittest.TestCase):
    SRC = "acme/fastgrid A fast grid layout engine. It renders 3 panes."
    EXTRA = {"stars": 1840, "age_days": 10}

    def spec(self, **kw):
        s = {"title": "How fastgrid lays out panes", "diagram_type": "process",
             "nodes": [{"id": "a", "label": "Parse"}, {"id": "b", "label": "Measure 3 panes"}], "edges": [{"from": "a", "to": "b"}],
             "metrics": [{"label": "Stars", "value": 1840, "unit": ""}], "takeaway": "Layout is two steps."}
        s.update(kw)
        return s

    def test_valid_measured(self):
        v = visual.validate(self.spec(), self.SRC, self.EXTRA)
        self.assertEqual(v["diagram_type"], "process")
        self.assertFalse(v["schematic"])

    def test_schematic_when_no_metrics(self):
        v = visual.validate(self.spec(metrics=[]), self.SRC, self.EXTRA)
        self.assertTrue(v["schematic"])

    def test_invented_numbers_rejected(self):
        self.assertIsNone(visual.validate(self.spec(metrics=[{"label": "Stars", "value": 2000, "unit": ""}]), self.SRC, self.EXTRA))
        self.assertIsNone(visual.validate(self.spec(takeaway="It is 40% faster."), self.SRC, self.EXTRA))
        self.assertIsNone(visual.validate(self.spec(nodes=[{"id": "a", "label": "5 workers"}], edges=[]), self.SRC, self.EXTRA))

    def test_schema_rejected(self):
        for bad in (self.spec(diagram_type="pie"), self.spec(nodes="x"), self.spec(edges=[{"from": "a", "to": "zz"}]),
                    self.spec(nodes=[{"id": str(i), "label": "n"} for i in range(5)], edges=[]), self.spec(takeaway=""),
                    self.spec(metrics=[{"label": "Stars", "value": "1840", "unit": ""}]), None, []):
            self.assertIsNone(visual.validate(bad, self.SRC, self.EXTRA), bad)

    def test_personal_context_not_a_number_source(self):
        self.assertEqual(visual.fact_numbers({"stars": 5, "personal_context": ["Plan 2027"]}), {5.0})


class ModelVisualTests(Base):
    def test_model_visual_validated_and_stored(self):
        cfg = make_cfg(self.tmp, self.base, model=True)
        good = {"title": "fastgrid stars", "diagram_type": "comparison", "nodes": [], "edges": [],
                "metrics": [{"label": "Stars", "value": 1840, "unit": ""}, {"label": "Forks", "value": 52, "unit": ""}],
                "takeaway": "Stars far ahead of forks after 10 days."}
        invented = {"title": "Queue", "diagram_type": "concept", "nodes": [{"id": "q", "label": "Queue"}], "edges": [],
                    "metrics": [{"label": "Throughput", "value": 99000, "unit": "ops/s"}], "takeaway": "Fast."}
        Api.model_reply = {"acme/fastgrid": {"summary": "Deep summary.", "why": "Why.", "visual": good},
                           "acme/tinyqueue": {"summary": "Queue summary.", "why": "Why.", "visual": invented}}
        conn = connect(cfg.db)
        collect.collect(cfg, conn, now=NOW, threads=[])
        d = api.view(cfg)
        items = {i["title"]: i for s in d["sections"] for i in s["items"]}
        self.assertEqual(items["acme/fastgrid"]["visual"]["metrics"][0]["value"], 1840)
        self.assertFalse(items["acme/fastgrid"]["visual"]["schematic"])
        self.assertIsNone(items["acme/tinyqueue"]["visual"])          # invented number: dropped
        self.assertEqual(items["acme/tinyqueue"]["summary"], "Queue summary.")


class ApiShapeTests(Base):
    def test_shape_and_old_db_migration(self):
        cfg = make_cfg(self.tmp, self.base, brain=THREADS)
        conn = connect(cfg.db)
        conn.executescript("CREATE TABLE briefing_items (id INTEGER PRIMARY KEY AUTOINCREMENT, day TEXT NOT NULL, topic TEXT NOT NULL, "
                           "url TEXT NOT NULL, title TEXT NOT NULL, tags TEXT NOT NULL DEFAULT '[]', published TEXT, score REAL NOT NULL DEFAULT 0, "
                           "headline INTEGER NOT NULL DEFAULT 0, summary TEXT NOT NULL DEFAULT '', why TEXT NOT NULL DEFAULT '', "
                           "by_model INTEGER NOT NULL DEFAULT 0, project TEXT, reason TEXT, UNIQUE(day, url));")
        threads = match.brain_threads({"threads": [{"title": "Terminal layout rewrite", "source": "notes/Projects/layout-rewrite", "status": ""}]})
        collect.collect(cfg, conn, now=NOW, threads=threads)
        d = api.view(cfg)
        it = d["sections"][0]["items"][0]
        for k in ("title", "url", "topic", "summary", "why", "extra", "visual", "personal_context", "headline", "by_model", "project", "tags"):
            self.assertIn(k, it)
        self.assertEqual(it["personal_context"], ["Terminal layout rewrite"])
        self.assertNotIn("personal_context", it["extra"])
        self.assertEqual(it["extra"]["source_type"], "github")


if __name__ == "__main__":
    unittest.main()
