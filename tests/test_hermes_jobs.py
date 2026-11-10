"""Tests for the plugin's opt-in job listings (jobs.py): profile, CV proposal, LinkedIn guest search, scoring,
lifecycle, market view, tools, commands, brief, hooks and demo.

Synthetic pages, profiles and CV text only. fetch and the pause between requests are replaced in every test
that could reach them: nothing touches the network, sleeps for real or needs a key. No live LinkedIn request.
"""
from __future__ import annotations

import importlib
import json
import sqlite3
import sys
import unittest
import urllib.error
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests.test_hermes_plugin import CMD, PLUGIN_DIR, C, H, P, T, Base  # noqa: E402

K = importlib.import_module(P.__name__ + ".collector")
J = importlib.import_module(P.__name__ + ".jobs")
B = importlib.import_module(P.__name__ + ".brief")

PROFILE = {"target_roles": ["Data Analyst"], "locations": ["Example City"], "skills": ["sql", "python", "tableau"],
           "languages": [{"language": "English", "level": "C1"}], "remote_pref": "any", "seniority": "junior",
           "experience_years": 2, "must_have": ["sql"], "nice_to_have": ["power bi"], "exclude": ["unpaid"]}
CV_TEXT = """Alex Example
alex.example@example.com | +39 333 123 4567 | https://www.linkedin.com/in/alex-example-000 | github.com/alex-example
Summary: data analyst working with SQL, Python and Tableau dashboards for a food company.
Languages: English C1, Italian B1, Spanish basic
"""
SECRETS = ("alex.example@example.com", "333 123 4567", "+39", "linkedin.com/in", "github.com/alex")


def card(i: int, title: str, company: str = "Example Foods", loc: str = "Example City", day: str = "2026-11-08") -> str:
    return (f'<li><div class="base-card" data-entity-urn="urn:li:jobPosting:{i}">'
            f'<h3 class="base-search-card__title">{title}</h3>'
            f'<h4 class="base-search-card__subtitle"><a href="#">{company}</a></h4>'
            f'<span class="job-search-card__location">{loc}</span>'
            f'<time class="job-search-card__listdate" datetime="{day}">2 days ago</time></div></li>')


PAGE1 = "".join(card(1000 + i, f"Data Analyst {i}", f"Example Foods {i % 3}") for i in range(10))  # full page
PAGE2 = "".join(card(2000 + i, f"Junior Data Analyst {i}", "Sample Logistics") for i in range(3))  # last page


class FakeGet:
    """Serves synthetic guest-search pages by the start= offset; an Exception value is raised."""

    def __init__(self, pages):
        self.pages, self.calls = pages, []

    def __call__(self, url, timeout=K.TIMEOUT, max_bytes=K.MAX_BYTES):
        self.calls.append(url)
        if callable(self.pages):
            v = self.pages(len(self.calls), url)
        else:
            start = int(url.split("start=")[1].split("&")[0]) if "start=" in url else 0
            v = self.pages.get(start, "")
        if isinstance(v, Exception):
            raise v
        return v


def http_error(code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(J.SEARCH, code, "blocked", hdrs=None, fp=None)


def no_network(*a, **k):
    raise AssertionError(f"fetch called: {a}")


class Jobs(Base):
    def setUp(self):
        super().setUp()
        self.sleeps = []
        self._p = [mock.patch.object(J, "pause", lambda s: self.sleeps.append(s))]  # never sleep for real
        for p in self._p:
            p.start()

    def tearDown(self):
        for p in self._p:
            p.stop()
        super().tearDown()

    def jenv(self, **extra):
        settings = {"kokpit_config": str(self.config()), "demo": "off", "jobs": True, "jobs_linkedin": True, **extra}
        return self.env(**settings)

    def store(self):
        return self.tmp / "plugin-data" / "collector.db"

    def profile_path(self):
        return self.tmp / "plugin-data" / "profile.json"

    def save_profile(self, prof=None):
        return J.save_profile(self.profile_path(), prof if prof is not None else PROFILE)

    def q(self, sql, args=()):
        conn = sqlite3.connect(self.store())
        try:
            return conn.execute(sql, args).fetchall()
        finally:
            conn.close()

    def collect(self, env, get, now=None):
        with mock.patch.object(K, "fetch", get):
            if now is None:
                return self.call(env, "kokpit_jobs", {"action": "collect"})
            return J.collect(env.state(), now=now)

    # B1 ------------------------------------------------------------ defaults never fetch
    def test_b1_defaults_never_fetch(self):
        self.save_profile()
        env = self.live_env()  # kokpit.toml has jobs = true, but jobs_linkedin is off
        with mock.patch.object(K, "fetch", no_network), mock.patch.object(K, "_open", no_network):
            for name in ("kokpit_jobs", "kokpit_market", "kokpit_health", "kokpit_today"):
                self.assertTrue(self.call(env, name, {})["ok"], name)
            self.assertIn("Kokpit brief", CMD.run(env, "brief"))
            CMD.run(env, "jobs")
            CMD.run(env, "jobs market")
            CMD.run(env, "jobs profile")
            h = H.Hooks(env)
            h.on_session_start(session_id="s0")
            self.assertIsNone(h.last_collect)
            # module on, source off: nothing fetched, says no source is enabled
            r = self.call(env, "kokpit_jobs", {"action": "collect"})
            self.assertFalse(r["ok"])
            self.assertEqual(r["error"], "no job source enabled")
            self.assertIn("at your own risk", r["fix"])
            self.assertIn("jobs_linkedin true", r["fix"])
            self.assertIn("no job source enabled", CMD.run(env, "jobs run"))
            # module off: the standard disabled shape with a fix line
            off = self.env(kokpit_config=str(self.config()), demo="off", jobs=False, jobs_linkedin=True)
            r = self.call(off, "kokpit_jobs", {"action": "collect"})
            self.assertEqual((r["ok"], r["error"]), (False, "module disabled"))
            self.assertIn("plugins.entries.kokpit.settings.jobs true", r["fix"])
            self.assertEqual(self.call(off, "kokpit_market", {})["error"], "module disabled")
        self.assertFalse(self.store().exists())

    def test_b1_explicit_false_wins_over_kokpit_toml(self):
        st = self.env(kokpit_config=str(self.config()), demo="off", jobs=False).state()
        self.assertFalse(st["cfg"]["modules"]["jobs"])
        st = self.env(kokpit_config=str(self.config(modules={"jobs": False})), demo="off", jobs=True).state()
        self.assertTrue(st["cfg"]["modules"]["jobs"])
        st = self.env(kokpit_config=str(self.config(modules={"jobs": False})), demo="off").state()
        self.assertFalse(st["cfg"]["modules"]["jobs"])

    # B2 ------------------------------------------------------------ collect, store, score, list, no duplicates
    def test_b2_collect_stores_scores_and_lists_without_duplicates(self):
        self.save_profile()
        env = self.jenv()
        get = FakeGet({0: PAGE1, 10: PAGE2})
        r = self.collect(env, get)
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["status"], "ok")
        self.assertEqual((r["fetched"], r["new"], r["requests"]), (13, 13, 2))
        self.assertIn("at your own risk", r["notice"])  # first run says what the source is
        self.assertEqual(len(get.calls), 2)
        self.assertTrue(all(u.startswith("https://www.linkedin.com/jobs-guest/") for u in get.calls), get.calls)
        self.assertIn("f_TPR=r604800", get.calls[0])  # the 7-day window
        self.assertEqual(self.sleeps, [J.DELAY_S])  # one pause, between the two requests
        lst = self.call(env, "kokpit_jobs", {})
        self.assertEqual(lst["source"], "plugin store")
        self.assertEqual(lst["freshness"]["last_status"], "ok")
        self.assertEqual(lst["freshness"]["records"], 13)
        self.assertEqual(lst["open_total"], 13)
        self.assertTrue(all(isinstance(x["score"], int) for x in lst["listings"]))
        self.assertTrue(any("role" in s for s in lst["listings"][0]["reasons"]))
        self.assertEqual(set(lst), {"ok", "mode", "listings", "count", "open_total", "freshness", "source", "store"})
        # the same day again: updates, no duplicates
        r2 = self.collect(env, FakeGet({0: PAGE1, 10: PAGE2}))
        self.assertEqual((r2["new"], r2["updated"]), (0, 13))
        self.assertNotIn("notice", r2)
        self.assertEqual(self.q("SELECT COUNT(*) FROM job_listings")[0][0], 13)
        self.assertEqual(self.q("SELECT COUNT(*) FROM runs WHERE job='career' AND ok=1")[0][0], 2)
        text = CMD.run(env, "jobs")
        self.assertIn("13 open", text)
        self.assertIn("plugin store", text)

    def test_b2_request_caps(self):
        prof = dict(PROFILE, target_roles=["Data Analyst", "BI Developer", "Data Engineer", "Analytics Engineer"],
                    locations=["Example City", "Remote", "Sample Town"])
        self.save_profile(prof)
        get = FakeGet(lambda n, url: PAGE1)  # every page full: only the caps stop it
        r = self.collect(self.jenv(), get)
        self.assertEqual(len(get.calls), J.MAX_QUERIES * J.MAX_PAGES)
        self.assertEqual(r["requests"], J.MAX_QUERIES * J.MAX_PAGES)
        self.assertEqual(len(self.sleeps), len(get.calls) - 1)
        self.assertEqual(len({u.split("keywords=")[1].split("&")[0] + u.split("location=")[1].split("&")[0]
                              for u in get.calls}), J.MAX_QUERIES)

    def test_b2_dedupe_by_company_and_title_across_ids(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript(J.SCHEMA)
        a = {"source": "linkedin", "external_id": "1", "title": "Data Analyst (m/f/d)", "company": "Example Foods S.p.A."}
        b = {"source": "other", "external_id": "9", "title": "Data analyst", "company": "Example Foods"}
        self.assertEqual(J.upsert(conn, [a, b], "2026-11-10T08:00:00"), {"new": 1, "updated": 0, "duplicates": 1})
        self.assertEqual(conn.execute("SELECT sources FROM job_listings").fetchone()[0], "linkedin,other")

    # B3 ------------------------------------------------------------ a block stops the run at once
    def test_b3_block_stops_the_run_and_keeps_the_first_page(self):
        for code in (429, 999):
            with self.subTest(code=code):
                if self.store().exists():
                    self.store().unlink()
                self.save_profile()
                self.sleeps.clear()
                get = FakeGet(lambda n, url: PAGE1 if n == 1 else http_error(code))
                r = self.collect(self.jenv(), get)
                self.assertTrue(r["ok"], r)
                self.assertEqual(r["status"], "blocked")
                self.assertEqual(len(get.calls), 2, "no request after the block")
                self.assertEqual(r["blocked"], f"HTTP {code}")
                self.assertIn("does not bypass blocks", r["note"])
                self.assertTrue(any(f"HTTP {code}" in w for w in r["warnings"]))
                self.assertEqual(r["fetched"], 10)
                self.assertEqual(self.q("SELECT COUNT(*) FROM job_listings")[0][0], 10)
                row = self.q("SELECT warnings, info FROM runs WHERE job='career' ORDER BY id DESC LIMIT 1")[0]
                self.assertIn(f"HTTP {code}", row[0])
                self.assertIn(f"HTTP {code}", row[1])
                with mock.patch.object(K, "fetch", FakeGet(lambda n, url: PAGE1 if n == 1 else http_error(code))):
                    text = CMD.run(self.jenv(), "jobs run")
                self.assertIn("status blocked", text)
                self.assertIn("does not bypass blocks", text)

    def test_b3_other_http_error_is_a_failed_run_without_listings(self):
        self.save_profile()
        get = FakeGet(lambda n, url: http_error(500))
        r = self.collect(self.jenv(), get)
        self.assertEqual(r["status"], "failed")
        self.assertEqual(len(get.calls), 1)
        self.assertEqual(self.q("SELECT ok FROM runs WHERE job='career'")[0][0], 0)

    # B4 ------------------------------------------------------------ no profile, unknown score
    def test_b4_no_target_roles_means_zero_requests(self):
        for prof in (None, dict(PROFILE, target_roles=[])):
            with self.subTest(prof=bool(prof)):
                if prof is not None:
                    self.save_profile(prof)
                with mock.patch.object(K, "fetch", no_network):
                    r = self.call(self.jenv(), "kokpit_jobs", {"action": "collect"})
                self.assertFalse(r["ok"])
                self.assertIn("no target roles", r["error"])
                self.assertIn("profile set target_roles", r["fix"])
        self.assertFalse(self.store().exists())
        self.assertEqual(J.search(J.empty(), get=no_network)[2]["requests"], 0)

    def test_b4_too_little_information_is_unknown_not_zero(self):
        res = J.score(J.empty(), {"title": "Data Analyst"})
        self.assertIsNone(res["score"])
        self.assertGreater(res["unknowns"], 0)
        self.save_profile()
        env = self.jenv()
        self.collect(env, FakeGet({0: PAGE2}))
        conn = sqlite3.connect(self.store())
        conn.row_factory = sqlite3.Row
        J.set_scores(conn, J.empty())
        conn.commit()
        conn.close()
        lst = self.call(env, "kokpit_jobs", {})
        self.assertEqual({x["score"] for x in lst["listings"]}, {"unknown"})
        self.assertIn("[unknown]", CMD.run(env, "jobs"))

    # B5 ------------------------------------------------------------ lifecycle
    def test_b5_absent_listing_closes_only_after_a_good_run(self):
        self.save_profile()
        env = self.jenv()
        now = datetime.now()
        two = card(1, "Data Analyst", "Alpha Foods") + card(2, "BI Analyst", "Beta Foods")
        self.collect(env, FakeGet({0: two}), now=now - timedelta(days=5))
        # blocked run: nothing closes
        r = self.collect(env, FakeGet(lambda n, u: http_error(429)), now=now)
        self.assertEqual(r["closed"], 0)
        # a run that returned nothing: nothing closes
        r = self.collect(env, FakeGet({0: ""}), now=now)
        self.assertEqual(r["closed"], 0)
        self.assertEqual(self.q("SELECT COUNT(*) FROM job_listings WHERE status='open'")[0][0], 2)
        # a good run without listing 2: it is closed, listing 1 stays open
        r = self.collect(env, FakeGet({0: card(1, "Data Analyst", "Alpha Foods")}), now=now)
        self.assertEqual(r["closed"], 1)
        rows = dict(self.q("SELECT external_id, status FROM job_listings"))
        self.assertEqual(rows, {"1": "open", "2": "closed"})
        # seen again later: reopened
        r = self.collect(env, FakeGet({0: two}), now=now + timedelta(days=1))
        self.assertEqual(r["reopened"], 1)
        self.assertEqual(self.q("SELECT status FROM job_listings WHERE external_id='2'")[0][0], "open")

    # B6 ------------------------------------------------------------ the app database wins and stays untouched
    def test_b6_app_database_wins_and_is_never_written(self):
        conn = self.make_db()
        conn.execute("INSERT INTO job_listings(source, external_id, title, company, status, first_seen, last_seen, "
                     "score, reasons) VALUES ('app', 'a1', 'App Listing', 'App Co', 'open', '2026-11-01', "
                     "'2026-11-09', 77, '[]')")
        conn.commit()
        conn.close()
        before = (self.db.read_bytes(), self.db.stat().st_mtime_ns)
        self.save_profile()
        env = self.jenv()
        r = self.collect(env, FakeGet({0: PAGE2}))
        self.assertEqual(r["status"], "ok")
        lst = self.call(env, "kokpit_jobs", {})
        self.assertEqual(lst["source"], "kokpit app database")
        self.assertEqual([x["title"] for x in lst["listings"]], ["App Listing"])
        m = self.call(env, "kokpit_market", {})
        self.assertEqual(m["source"], "kokpit app database")
        self.assertEqual(m["total"], 1)
        self.call(env, "kokpit_health", {})
        self.assertIn("Jobs: 1 open", CMD.run(env, "brief"))
        CMD.run(env, "jobs market")
        self.assertEqual((self.db.read_bytes(), self.db.stat().st_mtime_ns), before)

    # B7 ------------------------------------------------------------ market view
    def test_b7_market_view_matches_hand_computed_values(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript(J.SCHEMA)
        rows = [  # (id, company, status, posted_at, first_seen, description)
            ("1", "Example Foods", "open", "2026-11-02", "2026-11-03", "SQL and Python, Power BI dashboards"),
            ("2", "Example Foods", "open", "2026-11-04", "2026-11-04", "SQL and Excel reporting"),
            ("3", "Sample Logistics", "open", "2026-11-10", "2026-11-10", "Python and Airflow"),
            ("4", "Sample Logistics", "closed", "2026-10-28", "2026-11-03", "SQL"),
            ("5", None, "open", None, "2026-11-03", None),  # undated, first run day: bulk load, out of the trend
        ]
        for i, co, st, posted, first, desc in rows:
            conn.execute("INSERT INTO job_listings(source, external_id, title, company, status, posted_at, first_seen, "
                         "last_seen, description) VALUES ('linkedin', ?, 'Data Analyst', ?, ?, ?, ?, ?, ?)",
                         (i, co, st, posted, first, first, desc))
        v = J.view(conn)
        self.assertEqual((v["status"], v["total"], v["open"], v["companies_n"]), ("ok", 5, 4, 3))
        self.assertEqual(v["companies"][:2], [{"company": "Example Foods", "open": 2},
                                              {"company": "Sample Logistics", "open": 1}])
        self.assertIn({"company": "company unknown", "open": 1}, v["companies"])
        # weeks start on Monday: 2026-10-26 (row 4), 2026-11-02 (rows 1, 2), 2026-11-09 (row 3)
        self.assertEqual(v["weekly"], [{"week": "2026-10-26", "listings": 1}, {"week": "2026-11-02", "listings": 2},
                                       {"week": "2026-11-09", "listings": 1}])
        skills = {x["skill"]: x["open"] for x in v["skills"]}
        self.assertEqual(skills["sql"], 2)  # open rows 1 and 2 (row 4 is closed)
        self.assertEqual(skills["python"], 2)
        self.assertEqual((skills["power bi"], skills["excel"], skills["airflow"]), (1, 1, 1))
        self.assertEqual(v["window_days"], 14)  # 2026-10-28 .. 2026-11-10
        self.assertTrue(any(n.startswith("short window") for n in v["notes"]))
        self.assertTrue(any("1 undated listing" in n for n in v["notes"]))
        self.assertTrue(any("1 open listing(s) have no description" in n for n in v["notes"]))
        empty = sqlite3.connect(":memory:")
        empty.row_factory = sqlite3.Row
        empty.executescript(J.SCHEMA)
        self.assertEqual(J.view(empty)["status"], "no listings yet")
        self.assertNotIn("total", J.view(empty))

    def test_b7_market_tool_never_run_is_not_zeros(self):
        r = self.call(self.jenv(), "kokpit_market", {})
        self.assertTrue(r["ok"])
        self.assertEqual(r["status"], "never run")
        self.assertNotIn("total", r)
        self.assertIn("never run", CMD.run(self.jenv(), "jobs market"))
        self.assertEqual(self.call(self.jenv(), "kokpit_jobs", {})["status"], "never run")

    # B8 ------------------------------------------------------------ profile commands and the CV proposal
    def test_b8_profile_set_and_show_round_trip(self):
        env = self.jenv()
        self.assertIn("target_roles: not set", CMD.run(env, "jobs profile"))
        out = CMD.run(env, "jobs profile set target_roles Data Analyst, BI Developer")
        self.assertIn("Saved target_roles", out)
        CMD.run(env, "jobs profile set languages English:C1, Italian:B1")
        CMD.run(env, "jobs profile set remote_pref hybrid")
        CMD.run(env, "jobs profile set experience_years 3")
        shown = CMD.run(env, "jobs profile")
        self.assertIn("target_roles: Data Analyst, BI Developer", shown)
        self.assertIn("languages: English:C1, Italian:B1", shown)
        self.assertIn("remote_pref: hybrid", shown)
        saved = json.loads(self.profile_path().read_text(encoding="utf-8"))
        self.assertEqual(saved["target_roles"], ["Data Analyst", "BI Developer"])
        self.assertEqual(saved["experience_years"], 3)
        self.assertEqual(set(saved), set(J.FIELDS))
        before = self.profile_path().read_text(encoding="utf-8")
        for bad in ("jobs profile set salary 100", "jobs profile set remote_pref sometimes",
                    "jobs profile set seniority guru", "jobs profile set languages English:fluent",
                    "jobs profile set experience_years many"):
            self.assertIn("profile not changed", CMD.run(env, bad), bad)
        self.assertEqual(self.profile_path().read_text(encoding="utf-8"), before)

    def test_b8_cv_proposal_writes_nothing_without_apply_and_hides_contact_data(self):
        env = self.jenv()
        cv = self.tmp / "cv.txt"
        cv.write_text(CV_TEXT, encoding="utf-8")
        out = CMD.run(env, f"jobs profile cv {cv}")
        self.assertIn("sql", out)
        self.assertIn("tableau", out)
        self.assertIn("English:C1", out)
        self.assertIn("Italian:B1", out)
        self.assertIn("Nothing saved", out)
        self.assertFalse(self.profile_path().exists())
        out2 = CMD.run(env, f"jobs profile cv {cv} apply")
        self.assertIn("Added to your profile", out2)
        prof = json.loads(self.profile_path().read_text(encoding="utf-8"))
        self.assertIn("python", prof["skills"])
        self.assertIn({"language": "Italian", "level": "B1"}, prof["languages"])
        written = "".join(p.read_text(encoding="utf-8", errors="replace") for p in (self.tmp / "plugin-data").rglob("*")
                          if p.is_file())
        for s in SECRETS:
            for text in (out, out2, written):
                self.assertNotIn(s, text, s)
        self.assertNotIn("Alex Example", written)  # the CV text itself is never stored
        self.assertNotIn("Summary:", written)

    def test_b8_scrub_and_pdf_without_pdftotext(self):
        clean = J.scrub(CV_TEXT)
        for s in ("alex.example@example.com", "333 123 4567", "linkedin.com/in", "github.com/alex"):
            self.assertNotIn(s, clean)
        pdf = self.tmp / "cv.pdf"
        pdf.write_bytes(b"%PDF-1.4 synthetic")
        with mock.patch.object(J.shutil, "which", return_value=None), \
                mock.patch.object(J.subprocess, "run", side_effect=AssertionError("must not run")):
            out = CMD.run(self.jenv(), f"jobs profile cv {pdf}")
        self.assertIn("pdftotext is not installed", out)
        docx = self.tmp / "cv.docx"
        docx.write_text("synthetic", encoding="utf-8")
        self.assertIn("CV not read: unsupported file type", CMD.run(self.jenv(), f"jobs profile cv {docx}"))
        self.assertIn("file not found", CMD.run(self.jenv(), "jobs profile cv /nonexistent/cv.txt"))

    # B9 ------------------------------------------------------------ demo
    def test_b9_demo_shows_seeded_listings_and_never_fetches(self):
        env = self.env(demo="on", jobs_linkedin=True)
        with mock.patch.object(K, "fetch", no_network), mock.patch.object(K, "_open", no_network):
            lst = self.call(env, "kokpit_jobs", {})
            self.assertEqual(lst["mode"], "demo")
            self.assertGreaterEqual(lst["open_total"], 4)
            self.assertEqual(lst["source"], "kokpit app database")
            m = self.call(env, "kokpit_market", {})
            self.assertEqual(m["status"], "ok")
            self.assertTrue(m["companies"])
            self.assertTrue(m["skills"])
            r = self.call(env, "kokpit_jobs", {"action": "collect"})
            self.assertEqual((r["ok"], r["error"]), (False, "demo mode never fetches"))
            self.assertIn("demo data", CMD.run(env, "jobs"))
            self.assertIn("Job market (demo data)", CMD.run(env, "jobs market"))
            self.assertIn("Jobs:", CMD.run(env, "brief"))
            h = H.Hooks(self.env(demo="on", jobs=True, jobs_linkedin=True, collect_auto=True))
            h.on_session_start(session_id="d")
            self.assertIsNone(h.last_collect)
        self.assertFalse(self.store().exists())

    # B10 ----------------------------------------------------------- health
    def test_b10_health_lists_the_career_job_only_after_a_run(self):
        self.save_profile()
        env = self.jenv()
        self.assertNotIn("career", [j["job"] for j in self.call(env, "kokpit_health", {})["plugin_jobs"]])
        self.collect(env, FakeGet({0: PAGE2}))
        jobs = {j["job"]: j for j in self.call(env, "kokpit_health", {})["plugin_jobs"]}
        self.assertIn("career", jobs)
        self.assertEqual(jobs["career"]["source"], "plugin")
        self.assertEqual(jobs["career"]["records"], 3)
        self.assertEqual(jobs["career"]["status"], "ok")

    # collect_auto covers career ------------------------------------
    def test_collect_auto_runs_career_when_jobs_and_source_are_on(self):
        self.save_profile()
        env = self.jenv(collect_auto=True)
        h = H.Hooks(env)
        get = FakeGet({0: PAGE2})
        with mock.patch.object(K, "fetch", get):
            h.on_session_start(session_id="a")
            self.assertIsNotNone(h.last_collect)
            h.last_collect.join(30)
            self.assertEqual(len(get.calls), 1)
            h.last_collect = None
            h.on_session_start(session_id="b")
            self.assertIsNone(h.last_collect, "within the threshold: no second collection")
        self.assertEqual(self.q("SELECT COUNT(*) FROM runs WHERE job='career' AND ok=1")[0][0], 1)
        for env in (self.jenv(collect_auto=True, jobs_linkedin=False), self.jenv(collect_auto=True, jobs=False)):
            h = H.Hooks(env)
            self.assertEqual(h.auto_jobs(env.state()), [])

    # commands ------------------------------------------------------
    def test_commands_on_off_and_help(self):
        saved = {}
        env = C.Env(lambda k, default=None: {"kokpit_config": str(self.config()), "demo": "off"}.get(k, default),
                    data_dir=self.tmp / "plugin-data", set_config=lambda k, v: saved.__setitem__(k, v))
        self.assertIn("Saved to the plugin settings", CMD.run(env, "jobs off"))
        self.assertEqual(saved["jobs"], False)
        out = CMD.run(env, "jobs linkedin on")
        self.assertIn("at your own risk", out)
        self.assertEqual(saved["jobs_linkedin"], True)
        plain = self.env(kokpit_config=str(self.config()), demo="off")
        self.assertIn("hermes config set plugins.entries.kokpit.settings.jobs true", CMD.run(plain, "jobs on"))
        help_ = CMD.run(env, "jobs help")
        self.assertIn("profile cv <path>", help_)
        self.assertIn("does not work around a block", help_)
        self.assertIn("/kokpit jobs", CMD.run(env, "help"))
        self.assertIn("Usage: /kokpit jobs", CMD.run(env, "jobs nonsense"))

    # F1 ------------------------------------------------------------ a comma stays inside one location
    def test_f1_locations_split_on_semicolons_only(self):
        p = J.set_field(J.empty(), "locations", "Milan, Italy; Turin")
        self.assertEqual(p["locations"], ["Milan, Italy", "Turin"])
        self.assertEqual(J.set_field(J.empty(), "locations", "Milan, Italy")["locations"], ["Milan, Italy"])
        self.assertEqual(J.set_field(J.empty(), "locations", "Milan")["locations"], ["Milan"])
        self.assertEqual(J.set_field(J.empty(), "locations", "Milan, Italy\nTurin")["locations"],
                         ["Milan, Italy", "Turin"])
        self.assertEqual(J.set_field(J.empty(), "skills", "sql, python")["skills"], ["sql", "python"])
        # a stored JSON list is kept entry by entry; a legacy string splits on ';' only
        path = self.profile_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"locations": ["Milan, Italy", "Turin"], "skills": ["sql", "python"]}),
                        encoding="utf-8")
        self.assertEqual(J.load_profile(path)["locations"], ["Milan, Italy", "Turin"])
        self.assertEqual(J.normalise({"locations": ["Milan, Italy"]})["locations"], ["Milan, Italy"])
        self.assertEqual(J.normalise({"locations": "Milan, Italy; Turin"})["locations"], ["Milan, Italy", "Turin"])
        self.assertEqual(J.normalise({"skills": "sql, python"})["skills"], ["sql", "python"])

    def test_f1_profile_command_round_trips_locations(self):
        env = self.jenv()
        CMD.run(env, "jobs profile set locations Milan, Italy; Turin")
        shown = CMD.run(env, "jobs profile")
        self.assertIn("locations: Milan, Italy; Turin", shown)
        self.assertIn("separated by ';'", shown)
        saved = json.loads(self.profile_path().read_text(encoding="utf-8"))
        self.assertEqual(saved["locations"], ["Milan, Italy", "Turin"])
        # pasting the shown value back gives the same list
        CMD.run(env, "jobs profile set locations " + shown.split("locations: ")[1].splitlines()[0])
        saved = json.loads(self.profile_path().read_text(encoding="utf-8"))
        self.assertEqual(saved["locations"], ["Milan, Italy", "Turin"])
        self.assertIn("locations use ';'", CMD.run(env, "jobs help"))
        self.assertIn("locations separated by ';'", CMD.run(env, "jobs profile set"))

    # F2 ------------------------------------------------------------ one query sends the full location
    def test_f2_one_location_with_a_comma_makes_one_query(self):
        prof = J.set_field(J.set_field(J.empty(), "target_roles", "Data Analyst"), "locations", "Milan, Italy")
        self.assertEqual(J.queries(prof), [("Data Analyst", "Milan, Italy")])
        get = FakeGet({0: PAGE2})  # a short page: one request per query
        records, _warnings, meta = J.search(prof, get=get, sleep=lambda s: None)
        self.assertEqual(meta["requests"], 1)
        self.assertEqual(len(get.calls), 1)
        self.assertIn("location=Milan%2C+Italy", get.calls[0])
        self.assertEqual(len(records), 3)

    # F3 ------------------------------------------------------------ location match on the city part
    def test_f3_location_matches_on_the_first_comma_part(self):
        def loc_effect(profile_loc, listing_loc):
            prof = J.normalise({"target_roles": ["Data Analyst"], "locations": [profile_loc]})
            res = J.score(prof, {"title": "Data Analyst", "location": listing_loc})
            return [r["effect"] for r in res["reasons"] if r["kind"] == "location"]

        self.assertEqual(loc_effect("Milan, Italy", "Milan, Lombardy, Italy"), ["plus"])
        self.assertEqual(loc_effect("Milan, Italy", "Greater Milan Metropolitan Area"), ["plus"])
        self.assertEqual(loc_effect("Milan, Italy", "Rome, Latium, Italy"), ["minus"])
        self.assertEqual(loc_effect("Italy", "Milan, Lombardy, Italy"), ["plus"])
        self.assertEqual(loc_effect("Milan", "Milan, Lombardy, Italy"), ["plus"])

    # F4 ------------------------------------------------------------ coverage scaling
    def test_f4_low_coverage_scales_the_score(self):
        base = {"target_roles": ["Data Analyst"], "locations": ["Milan, Italy"]}
        listing = {"title": "Data Analyst", "location": "Milan, Lombardy, Italy"}
        self.assertEqual((J.COVERAGE_MIN, J.LOW_COVERAGE_CAP, J.FULL_SCALE), (50, 60, 100))

        def cov(res):
            return [r for r in res["reasons"] if r["kind"] == "coverage"]

        # G1: role and location match, nothing else judgeable: raw 100 -> 60
        g1 = J.score(J.normalise(base), listing)
        self.assertEqual((g1["score"], g1["max_known"]), (60, 36))
        self.assertEqual(cov(g1), [{"kind": "coverage", "effect": "neutral", "text":
                                    "scaled to 60 percent: only 36 of 100 points could be judged (unscaled 100)"}])
        # G2: role matches, location does not: raw 83 -> 50, strictly below G1
        g2 = J.score(J.normalise(base), {**listing, "location": "Rome, Latium, Italy"})
        self.assertEqual((g2["score"], g2["max_known"]), (50, 36))
        self.assertEqual(len(cov(g2)), 1)
        self.assertIn("(unscaled 83)", cov(g2)[0]["text"])
        self.assertGreater(g1["score"], g2["score"])
        # G3: a raw score below 60 is scaled too: raw 58 -> 35
        g3 = J.score(J.normalise(base), {"title": "Business Analyst", "location": "Milan, Italy"})
        self.assertEqual((g3["score"], g3["max_known"]), (35, 36))
        self.assertEqual(len(cov(g3)), 1)
        self.assertIn("(unscaled 58)", cov(g3)[0]["text"])
        low = J.score(J.normalise(base), {"title": "Warehouse Operator", "location": "Rome, Latium, Italy"})
        self.assertEqual(low["score"], 0)
        self.assertIn("(unscaled 0)", cov(low)[0]["text"])
        # G4: enough judged: not scaled, no coverage reason
        full = J.normalise({**base, "must_have": ["sql"], "skills": ["python"]})
        res = J.score(full, {**listing, "description": "SQL and Python for a food company."})
        self.assertGreaterEqual(res["max_known"], J.COVERAGE_MIN)
        self.assertGreater(res["score"], J.LOW_COVERAGE_CAP)
        self.assertFalse(cov(res))
        # G5: the exclude cap still wins, and an exclude hit shows no coverage reason
        ex = J.score(J.normalise({**base, "exclude": ["unpaid"]}), {**listing, "title": "Unpaid Data Analyst"})
        self.assertLess(ex["max_known"], J.COVERAGE_MIN)
        self.assertLessEqual(ex["score"], J.EXCLUDE_CAP)
        self.assertFalse(cov(ex))
        # nothing judgeable still gives None
        self.assertIsNone(J.score(J.empty(), listing)["score"])

    # F5 ------------------------------------------------------------ the list shows the judged share
    def test_f5_jobs_list_shows_judged_share_when_scaled(self):
        self.save_profile({"target_roles": ["Data Analyst"], "locations": ["Milan, Italy"]})
        env = self.jenv()
        r = self.collect(env, FakeGet({0: card(1, "Data Analyst", loc="Milan, Lombardy, Italy")}))
        self.assertTrue(r["ok"], r)
        lst = self.call(env, "kokpit_jobs", {})
        self.assertEqual([(x["score"], x["max_known"]) for x in lst["listings"]], [(60, 36)])
        self.assertIn("[60, judged 36/100]", CMD.run(env, "jobs"))
        # a well covered listing shows the plain score
        self.save_profile({"target_roles": ["Data Analyst"], "locations": ["Milan, Italy"], "must_have": ["data"],
                           "nice_to_have": ["analyst"], "remote_pref": "onsite"})
        self.collect(env, FakeGet({0: card(1, "Data Analyst", loc="Milan, Lombardy, Italy")}))
        out = CMD.run(env, "jobs")
        self.assertNotIn("judged", out)

    # G6 ------------------------------------------------------------ matching location ranks first
    def test_g6_jobs_list_orders_low_coverage_listings_by_location(self):
        self.save_profile({"target_roles": ["Data Analyst"], "locations": ["Milan, Italy"]})
        env = self.jenv()
        page = (card(1, "Data Analyst", company="Rome Foods", loc="Rome, Latium, Italy")
                + card(2, "Data Analyst", company="Milan Foods", loc="Milan, Lombardy, Italy"))
        r = self.collect(env, FakeGet({0: page}))
        self.assertTrue(r["ok"], r)
        lst = self.call(env, "kokpit_jobs", {})
        self.assertEqual([(x["company"], x["score"], x["max_known"]) for x in lst["listings"]],
                         [("Milan Foods", 60, 36), ("Rome Foods", 50, 36)])
        out = CMD.run(env, "jobs")
        milan = out.index("[60, judged 36/100] Data Analyst (Milan Foods")
        rome = out.index("[50, judged 36/100] Data Analyst (Rome Foods")
        self.assertLess(milan, rome)


class Parser(unittest.TestCase):
    def test_parse_cards_reads_the_app_markup(self):
        cards = J.parse_cards(card(42, "Data &amp; BI Analyst", "Example <b>Foods</b>", "Example City", "2026-11-01")
                              + "<li>no posting id</li>")
        self.assertEqual(len(cards), 1)
        c = cards[0]
        self.assertEqual((c["external_id"], c["title"], c["company"], c["posted_at"]),
                         ("42", "Data & BI Analyst", "Example Foods", "2026-11-01"))
        self.assertEqual(c["url"], "https://www.linkedin.com/jobs/view/42")

    def test_fetch_keeps_its_checks(self):
        for bad in ("file:///etc/passwd", "ftp://example.org/x"):
            with self.assertRaises(ValueError):
                K.fetch(bad)

    def test_store_table_matches_the_app_schema(self):
        def cols(script):
            conn = sqlite3.connect(":memory:")
            try:
                conn.executescript(script)
                return [r[1] for r in conn.execute('PRAGMA table_info("job_listings")')]
            finally:
                conn.close()
        app = (ROOT / "kokpit" / "modules" / "career" / "schema.sql").read_text(encoding="utf-8")
        self.assertEqual(cols(J.SCHEMA), cols(app))

    def test_manifest_lists_the_new_settings_and_tool(self):
        manifest = (PLUGIN_DIR / "plugin.yaml").read_text(encoding="utf-8")
        for key in ("  jobs:", "  jobs_linkedin:", "  jobs_close_days:", "  - kokpit_market"):
            self.assertIn(key, manifest)
        self.assertIn('"kokpit_market"', (PLUGIN_DIR / "dashboard" / "plugin_api.py").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
