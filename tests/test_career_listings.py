"""Career listings: job adapter loader, store dedupe, LinkedIn guest source (mocked), career job, view. No network."""
from __future__ import annotations

import contextlib
import io
import json
import sqlite3
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from kokpit import __main__ as cli, config as cfgmod, jobs  # noqa: E402
from kokpit.modules.career import adapters, api, collect, linkedin, profile, store  # noqa: E402
from kokpit.modules.connections import api as conn_api  # noqa: E402

GOOD = """NAME = "{name}"
def collect(settings):
    return [{{"external_id": "g1", "title": "Data Analyst (m/f/d)", "company": "Test Co Ltd", "url": "https://x.test/1", "remote": "Remote"}},
            {{"title": "No id", "company": "Test Co", "url": "https://x.test/2", "posted": "2026-02-03T10:00"}},
            {{"description": "no title"}}]
"""
BROKEN = {
    "imp-err": "import not_a_module_xyz\ndef collect(s):\n    return []\n",
    "no-collect": "NAME = 'no-collect'\n",
    "raises": "def collect(s):\n    raise ValueError('page changed')\n",
    "wrong-type": "def collect(s):\n    return {'title': 'x'}\n",
}
# Synthetic guest-search markup (structure only, fictional companies).
CARD = """<li><div class="base-card" data-entity-urn="urn:li:jobPosting:{id}"><a class="base-card__full-link" href="https://example.invalid/{id}"></a>
<h3 class="base-search-card__title">{title}</h3><h4 class="base-search-card__subtitle"><a>{company}</a></h4>
<span class="job-search-card__location">Example City</span><time datetime="2026-10-27">3 days ago</time></div></li>"""


def _page(n, start=0):
    return "<ul>" + "".join(CARD.format(id=1000 + start + i, title=f"Data Analyst {start + i}", company="Example Corp") for i in range(n)) + "</ul>"


def _write(root: Path, ident: str, code: str) -> None:
    (root / ident).mkdir(parents=True, exist_ok=True)
    (root / ident / "collect.py").write_text(code, encoding="utf-8")


def _cfg(tmp: Path, enabled=(), dirs=(), extra="") -> cfgmod.Config:
    p = cfgmod.write_default(tmp / "kokpit.toml", vault=str(tmp / "vault"), db=str(tmp / "k.db"))
    text = p.read_text(encoding="utf-8").replace("jobs = false", "jobs = true")
    text = text.replace("adapters = []                 # adapters/jobs", "adapters = [" + ", ".join(f'"{e}"' for e in enabled) + "]  # adapters/jobs", 1)
    text = text.replace("adapter_dirs = []             # extra folders holding your own <name>/collect.py job", "adapter_dirs = [" + ", ".join(f'"{d}"' for d in dirs) + "]  # extra folders holding your own <name>/collect.py job", 1)
    p.write_text(text + extra, encoding="utf-8")
    return cfgmod.load(p)


class JobAdapterTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.user = self.tmp / "mine"
        _write(self.user, "good", GOOD.format(name="good"))
        for ident, code in BROKEN.items():
            _write(self.user, ident, code)

    def tearDown(self):
        self._tmp.cleanup()

    def test_defaults_off(self):
        cfg = _cfg(self.tmp)
        self.assertFalse(cfg.career["linkedin_guest"])
        found = {a["id"]: a for a in adapters.discover(cfg)}
        self.assertIn("example-careers", found)
        self.assertFalse(found["example-careers"]["enabled"])
        self.assertEqual(adapters.plan(cfg), [])
        fresh = cfgmod.write_default(self.tmp / "other.toml")
        self.assertIn("linkedin_guest = false", fresh.read_text(encoding="utf-8"))
        self.assertIn("jobs = false", fresh.read_text(encoding="utf-8"))

    def test_example_adapter_writes_and_dedupes(self):
        cfg = _cfg(self.tmp, enabled=["example-careers"])
        conn = sqlite3.connect(cfg.db)
        conn.row_factory = sqlite3.Row
        n, info, warnings = collect.collect(cfg, conn)
        self.assertEqual(n, 2)
        self.assertEqual(info["sources"]["example-careers"], 2)
        self.assertEqual(info["new"], 2)
        collect.collect(cfg, conn)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM job_listings").fetchone()[0], 2)
        row = conn.execute("SELECT * FROM job_listings WHERE external_id='ex-001'").fetchone()
        self.assertEqual((row["company"], row["remote"], row["status"]), ("Example Corp", "hybrid", "open"))
        self.assertIsNone(row["posted_at"])
        conn.close()

    def test_broken_adapters_are_isolated(self):
        cfg = _cfg(self.tmp, enabled=["good", *BROKEN, "ghost"], dirs=[str(self.user)])
        conn = sqlite3.connect(cfg.db)
        conn.row_factory = sqlite3.Row
        n, info, warnings = collect.collect(cfg, conn)
        self.assertEqual(n, 2)  # the record without a title is dropped
        text = " | ".join(warnings)
        for ident in BROKEN:
            self.assertIn(f"{ident}:", text)
            self.assertEqual(info["sources"][ident], 0)
        self.assertIn("import failed: ModuleNotFoundError", text)
        self.assertIn("no collect(settings) function", text)
        self.assertIn("ValueError: page changed", text)
        self.assertIn("adapter ghost: enabled in kokpit.toml but not found", text)
        self.assertTrue(all(len(w) < 250 for w in warnings))
        # same job, different external id and decorated title: one row (company + normalised title)
        rows = conn.execute("SELECT * FROM job_listings").fetchall()
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[1]["posted_at"], "2026-02-03")
        conn.close()

    def test_dedupe_across_sources(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        a = {"source": "a", "external_id": "1", "title": "Data Analyst (m/w/d)", "company": "Example Corp GmbH", "remote": "unknown"}
        b = {"source": "b", "external_id": "9", "title": "Data  Analyst", "company": "Example Corp", "remote": "unknown"}
        c = {"source": "b", "external_id": "10", "title": "Data Analyst", "company": None, "remote": "unknown"}
        d = {"source": "b", "external_id": "11", "title": "Data Analyst", "company": None, "remote": "unknown"}
        self.assertEqual(store.upsert(conn, [a, b])["duplicates"], 1)
        self.assertEqual(store.upsert(conn, [c, d])["new"], 2)  # no company: never merged by title
        rows = conn.execute("SELECT sources FROM job_listings ORDER BY id").fetchall()
        self.assertEqual(rows[0]["sources"], "a,b")
        self.assertEqual(len(rows), 3)

    def test_job_records_health_with_row_count(self):
        cfg = _cfg(self.tmp, enabled=["example-careers", "raises"], dirs=[str(self.user)])
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertTrue(jobs.run(cfg, "career"))
        conn = sqlite3.connect(cfg.db)
        row = conn.execute("SELECT rows, warnings FROM runs WHERE job='career' ORDER BY rowid DESC LIMIT 1").fetchone()
        conn.close()
        self.assertEqual(row[0], 2)
        self.assertIn("raises: ValueError: page changed", row[1])
        self.assertIn("career", jobs.REGISTRY)

    def test_career_module_off_is_a_warning(self):
        cfg = _cfg(self.tmp, enabled=["example-careers"])
        cfg.modules["jobs"] = False
        conn = sqlite3.connect(cfg.db)
        n, _, warnings = collect.collect(cfg, conn)
        conn.close()
        self.assertEqual(n, 0)
        self.assertIn("career module is off", warnings[0])

    def test_scores_stored_with_reasons_and_view(self):
        cfg = _cfg(self.tmp, enabled=["example-careers"])
        profile.save(cfg, {"target_roles": "Data Analyst", "must_have": "SQL", "languages": "English:C1"})
        with contextlib.redirect_stdout(io.StringIO()):
            jobs.run(cfg, "career")
        v = api.view(cfg)
        self.assertEqual(v["counts"]["open"], 2)
        self.assertEqual(v["freshness"]["last_status"], "ok")
        self.assertEqual(v["freshness"]["records"], 2)
        top = v["listings"][0]
        self.assertEqual(top["title"], "Data Analyst")
        self.assertIsNotNone(top["score"])
        self.assertTrue(top["reasons"]["reasons"])
        self.assertNotIn("description", top)

    def test_view_before_any_run(self):
        cfg = _cfg(self.tmp)
        v = api.view(cfg)
        self.assertEqual(v["listings"], [])
        self.assertEqual(v["freshness"]["last_status"], "never run")

    def test_cli_lists_job_adapters(self):
        cfg = _cfg(self.tmp, enabled=["good", "ghost"], dirs=[str(self.user)])
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = cli.main(["--config", str(cfg.path), "adapters"])
        text = out.getvalue()
        self.assertEqual(rc, 0)
        self.assertRegex(text, r"on \s+good\s+source=good")
        self.assertRegex(text, r"off\s+example-careers")
        self.assertIn("missing  ghost: enabled in [career] adapters", text)

    def test_connections_show_job_sources_and_linkedin_off(self):
        cfg = _cfg(self.tmp)
        items = {c["id"]: c for c in conn_api.view(cfg)["connections"]}
        self.assertEqual(items["linkedin-guest"]["status"], "off")
        self.assertFalse(items["linkedin-guest"]["enabled"])
        self.assertIn("User Agreement", items["linkedin-guest"]["detail"])
        self.assertEqual(items["job-adapters"]["status"], "off")


class LinkedInTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_parse_cards(self):
        recs = linkedin.parse_cards(_page(2))
        self.assertEqual(len(recs), 2)
        r = recs[0]
        self.assertEqual((r["source"], r["external_id"], r["company"], r["location"], r["posted_at"]), ("linkedin", "1000", "Example Corp", "Example City", "2026-10-27"))
        self.assertEqual(r["url"], "https://www.linkedin.com/jobs/view/1000")

    def test_search_builds_7_day_urls_from_targets_and_sleeps(self):
        urls, naps = [], []

        def get(url):
            urls.append(url)
            return _page(25, start=len(urls) * 100)
        recs, warns = linkedin.search({"target_roles": ["Data Analyst"], "locations": ["Example City"], "remote_pref": "any"},
                                      pages=2, get=get, sleep=naps.append, delay=7)
        self.assertEqual(len(urls), 2)
        self.assertIn("f_TPR=r604800", urls[0])
        self.assertIn("keywords=Data+Analyst", urls[0])
        self.assertIn("location=Example+City", urls[0])
        self.assertIn("start=25", urls[1])
        self.assertEqual(naps, [7])
        self.assertEqual((len(recs), warns), (50, []))

    def test_429_stops_and_warns_without_losing_earlier_pages(self):
        calls = []

        def get(url):
            calls.append(url)
            if len(calls) == 2:
                raise urllib.error.HTTPError(url, 429, "Too Many Requests", {}, None)
            return _page(25)
        recs, warns = linkedin.search({"target_roles": ["A", "B"], "locations": [""]}, pages=3, get=get, sleep=lambda s: None)
        self.assertEqual(len(calls), 2)  # nothing after the 429
        self.assertEqual(len(recs), 25)
        self.assertIn("HTTP 429", warns[0])
        self.assertIn("does not bypass", warns[0])

    def test_no_targets_and_zero_records_are_warnings(self):
        self.assertIn("no target roles", linkedin.search({}, get=lambda u: "")[1][0])
        recs, warns = linkedin.search({"target_roles": ["A"]}, get=lambda u: "<ul></ul>", sleep=lambda s: None)
        self.assertEqual(recs, [])
        self.assertIn("0 records", warns[0])

    def test_disabled_by_default_enabled_by_config(self):
        cfg = _cfg(self.tmp)
        profile.save(cfg, {"target_roles": "Data Analyst"})
        conn = sqlite3.connect(cfg.db)
        conn.row_factory = sqlite3.Row

        def boom(url):
            raise AssertionError("network must not be touched while linkedin_guest is false")
        collect.collect(cfg, conn, linkedin_get=boom)
        cfg.career["linkedin_guest"] = True
        n, info, warns = collect.collect(cfg, conn, linkedin_get=lambda u: _page(3), sleep=lambda s: None)
        self.assertEqual(n, 3)
        self.assertEqual(info["sources"]["linkedin"], 3)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM job_listings WHERE source='linkedin'").fetchone()[0], 3)
        conn.close()


if __name__ == "__main__":
    unittest.main()
