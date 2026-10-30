"""Career weekly market summary and skills plan. Synthetic listings and profile only."""
from __future__ import annotations

import json
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from datetime import date
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from kokpit import config as cfgmod  # noqa: E402
from kokpit.db import connect  # noqa: E402
from kokpit.modules.career import api, profile, skills, store, weekly  # noqa: E402
from kokpit.web import ThreadingHTTPServer, make_handler  # noqa: E402

TODAY = date(2026, 3, 12)   # a Thursday
PROFILE = {"target_roles": ["Data Analyst"], "skills": ["Python", "SQL"], "locations": ["Example City"],
           "remote_pref": "hybrid", "seniority": "junior"}


def _cfg(tmp: Path, model_use: bool = False) -> cfgmod.Config:
    p = cfgmod.write_default(tmp / "kokpit.toml", vault=str(tmp / "v"), db=str(tmp / "k.db"))
    text = p.read_text(encoding="utf-8").replace("jobs = false", "jobs = true")
    if model_use:
        text = (text.replace("[model]                       # optional", "[model]  # optional", 1)
                .replace("enabled = false\nbase_url = \"\"", "enabled = true\nbase_url = \"https://provider.example/v1\"", 1)
                .replace("model = \"\"\nkey_env", "model = \"example-model\"\nkey_env", 1)
                .replace("career_weekly = false", "career_weekly = true"))
    p.write_text(text, encoding="utf-8")
    return cfgmod.load(p)


def _listing(conn, n: int, day: str, title: str, company: str, desc: str = "", score: int | None = 40, source: str = "example"):
    conn.execute("INSERT INTO job_listings(source, external_id, title, company, description, posted_at, first_seen, last_seen, score) "
                 "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", (source, f"x{n}", title, company, desc, day, "2026-01-01T08:00:00", day, score))


def _fill(conn) -> None:
    store.ensure_schema(conn)
    # this week (Mon 2026-03-09 .. today): 3 listings, 2 good fits
    _listing(conn, 1, "2026-03-09", "Data Analyst", "Acme Data", "Python, SQL and Power BI dashboards", 70)
    _listing(conn, 2, "2026-03-10", "BI Developer", "Acme Data", "Tableau and dbt", 60)
    _listing(conn, 3, "2026-03-11", "Data Engineer", "Northwind", "Spark, Airflow, dbt", 30)
    # previous week: 1 listing
    _listing(conn, 4, "2026-03-03", "Analyst", "Contoso", "Excel and Power BI", 50)
    # four weeks back
    _listing(conn, 5, "2026-02-12", "Junior Analyst", "Fabrikam", "Power BI reports", 58)
    # outside the 8-week window
    _listing(conn, 6, "2025-11-01", "Old role", "Oldco", "Power BI", 90)
    conn.commit()


class WeeklyTrendTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.cfg = _cfg(self.tmp)
        self.conn = connect(self.cfg.db)
        _fill(self.conn)

    def tearDown(self):
        self.conn.close()

    def test_eight_weeks_oldest_first(self):
        weeks = weekly.trend(self.conn, TODAY)
        self.assertEqual(len(weeks), 8)
        self.assertEqual(weeks[-1]["start"], "2026-03-09")
        self.assertEqual(weeks[-1]["end"], "2026-03-15")
        self.assertEqual(weeks[0]["start"], "2026-01-19")
        self.assertEqual((weeks[-1]["listings"], weeks[-1]["companies"], weeks[-1]["good"]), (3, 2, 2))
        self.assertEqual((weeks[-2]["listings"], weeks[-2]["good"]), (1, 0))
        self.assertEqual(sum(w["listings"] for w in weeks), 5)   # the old one is outside the window

    def test_undated_first_run_rows_are_left_out(self):
        self.conn.execute("INSERT INTO job_listings(source, external_id, title, first_seen, last_seen) VALUES "
                          "('bulk', 'b1', 'Bulk', '2026-03-10T07:00:00', '2026-03-10'), "
                          "('bulk', 'b2', 'Later', '2026-03-11T07:00:00', '2026-03-11')")
        weeks = weekly.trend(self.conn, TODAY)
        self.assertEqual(weeks[-1]["listings"], 4)   # b2 counts (first seen after the source's first day), b1 does not

    def test_stats_compare_with_week_before(self):
        stats = weekly.build_stats(self.conn, PROFILE, TODAY)
        self.assertEqual(stats["start"], "2026-03-06")
        self.assertEqual(stats["this_week"]["listings"], 3)
        self.assertEqual(stats["previous_week"]["listings"], 1)
        self.assertIn("power bi", stats["this_week"]["skills_not_had"])
        self.assertNotIn("python", stats["this_week"]["skills_not_had"])

    def test_model_off_numbers_only(self):
        v = weekly.view(self.cfg, self.conn, TODAY)
        self.assertFalse(v["model_on"])
        self.assertIsNone(v["note"])
        self.assertEqual(len(v["weeks"]), 8)
        self.assertIn("numbers only", v["reason"])
        self.assertEqual(v["good_score"], skills.GOOD_SCORE)  # the Market leaf names the good-fit line
        called = []
        self.assertEqual(weekly.write(self.cfg, self.conn, PROFILE, TODAY, complete=lambda *a: called.append(a)), (None, []))
        self.assertEqual(called, [])

    def test_career_view_has_weekly_key(self):
        v = api.view(self.cfg)
        self.assertIn("weekly", v)
        self.assertEqual(len(v["weekly"]["weeks"]), 8)

    def test_weekly_key_without_database(self):
        cfg = _cfg(self.tmp / "nodb")
        v = api.view(cfg)
        self.assertEqual(len(v["weekly"]["weeks"]), 8)
        self.assertTrue(all(w["listings"] == 0 for w in v["weekly"]["weeks"]))

    def test_reader_from_profile_only(self):
        r = weekly.reader(PROFILE)
        self.assertIn("Data Analyst", r)
        self.assertIn("Example City", r)
        self.assertIn("junior", r)
        self.assertEqual(weekly.reader({}), "A job seeker; no target roles set yet.")
        p = weekly.prompt({**PROFILE, "name": "Jane Example"}, weekly.build_stats(self.conn, PROFILE, TODAY), {"python"})
        self.assertNotIn("Jane Example", p)

    def test_model_on_writes_once_per_week(self):
        cfg = _cfg(self.tmp / "on", model_use=True)
        conn = connect(cfg.db)
        _fill(conn)
        answer = json.dumps({"title": "More BI roles this week", "summary": "s", "points": ["a", "b", "c", "d"], "advice": "x"})
        calls = []

        def fake(cfg_, purpose, system, user, n):
            calls.append((purpose, user))
            return "Here: " + answer

        with mock.patch.dict("os.environ", {cfg.model["key_env"]: "test-value"}):
            note, warn = weekly.write(cfg, conn, PROFILE, TODAY, complete=fake)
            self.assertEqual(warn, [])
            assert note is not None
            self.assertEqual(note["title"], "More BI roles this week")
            self.assertEqual(len(note["points"]), 3)
            self.assertEqual(calls[0][0], "career_weekly")
            self.assertEqual(weekly.write(cfg, conn, PROFILE, TODAY, complete=fake), (None, []))
            self.assertEqual(len(calls), 1)
            v = weekly.view(cfg, conn, TODAY)
            self.assertTrue(v["model_on"])
            self.assertEqual(v["note"]["note"]["title"], "More BI roles this week")
        conn.close()

    def test_bad_model_answer_is_a_warning(self):
        cfg = _cfg(self.tmp / "bad", model_use=True)
        conn = connect(cfg.db)
        _fill(conn)
        with mock.patch.dict("os.environ", {cfg.model["key_env"]: "test-value"}):
            note, warn = weekly.write(cfg, conn, PROFILE, TODAY, complete=lambda *a: "not json")
        self.assertIsNone(note)
        self.assertIn("expected JSON", warn[0])
        self.assertIsNone(weekly.latest(conn))
        conn.close()


class SkillsPlanTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.cfg = _cfg(self.tmp)
        self.conn = connect(self.cfg.db)
        _fill(self.conn)

    def tearDown(self):
        self.conn.close()

    def test_gaps_exclude_profile_skills(self):
        plan = skills.plan(self.conn, PROFILE, TODAY)
        names = [i["skill"] for i in plan["items"]]
        self.assertIn("power bi", names)
        self.assertNotIn("python", names)
        self.assertNotIn("sql", names)
        pbi = next(i for i in plan["items"] if i["skill"] == "power bi")
        self.assertEqual(pbi["n_all"], 3)   # 90-day window drops the November listing
        self.assertEqual(pbi["n_good"], 2)
        self.assertEqual(plan["good_n"], 3)
        self.assertEqual(names[0], "power bi")   # most good-fit listings first

    def test_mark_persists_and_moves(self):
        skills.save(self.conn, "  Power BI ", "known", "certificate done")
        self.conn.close()
        self.conn = connect(self.cfg.db)
        self.assertEqual(skills.load(self.conn)["power bi"]["status"], "known")
        self.assertEqual(skills.known(self.conn), {"power bi"})
        plan = skills.plan(self.conn, PROFILE, TODAY)
        self.assertNotIn("power bi", [i["skill"] for i in plan["items"]])
        self.assertEqual(plan["hidden"][0]["skill"], "power bi")
        self.assertEqual(plan["summary"]["known"], 1)
        # known counts as had in the weekly stats too
        stats = weekly.build_stats(self.conn, PROFILE, TODAY)
        self.assertNotIn("power bi", stats["this_week"]["skills_not_had"])

    def test_learning_sorts_first_and_note_only_update(self):
        skills.save(self.conn, "spark", "learning")
        skills.save(self.conn, "spark", note="x" * 900)
        cur = skills.load(self.conn)["spark"]
        self.assertEqual(cur["status"], "learning")
        self.assertEqual(len(cur["note"]), skills.MAX_NOTE)
        self.assertEqual(skills.plan(self.conn, PROFILE, TODAY)["items"][0]["skill"], "spark")

    def test_clear_and_errors(self):
        skills.save(self.conn, "dbt", "skip")
        self.assertEqual(skills.save(self.conn, "dbt", ""), {"skill": "dbt", "deleted": True})
        self.assertEqual(skills.load(self.conn), {})
        with self.assertRaises(ValueError):
            skills.save(self.conn, "", "known")
        with self.assertRaises(ValueError):
            skills.save(self.conn, "dbt", "maybe")
        with self.assertRaises(ValueError):
            skills.save(self.conn, "dbt", note="no status yet")

    def test_plan_without_tables(self):
        import sqlite3
        plan = skills.plan(sqlite3.connect(":memory:"), PROFILE, TODAY)
        self.assertEqual((plan["items"], plan["hidden"], plan["good_n"]), ([], [], 0))


class SkillsWebTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.cfg = _cfg(self.tmp)
        profile.save(self.cfg, PROFILE)
        conn = connect(self.cfg.db)
        _fill(conn)
        conn.close()
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(self.cfg))
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.srv.server_address[1]}"

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()

    def _post(self, path: str, body: dict):
        req = urllib.request.Request(self.base + path, data=json.dumps(body).encode(), method="POST",
                                     headers={"Content-Type": "application/json", "Origin": self.base})
        return urllib.request.urlopen(req, timeout=5)

    def test_get_and_post(self):
        with urllib.request.urlopen(self.base + "/api/career/skills", timeout=5) as r:
            self.assertEqual(r.status, 200)
            self.assertTrue(json.loads(r.read())["enabled"])
        with self._post("/api/career/skills", {"skill": "tableau", "status": "learning"}) as r:
            self.assertEqual(json.loads(r.read())["status"], "learning")
        with urllib.request.urlopen(self.base + "/api/career/skills", timeout=5) as r:
            items = json.loads(r.read())["plan"]["items"]
        self.assertEqual(items[0]["skill"], "tableau")
        with self.assertRaises(urllib.error.HTTPError) as e:
            self._post("/api/career/skills", {"skill": "tableau", "status": "maybe"})
        self.assertEqual(e.exception.code, 400)
        with urllib.request.urlopen(self.base + "/api/career", timeout=5) as r:
            self.assertIn("weekly", json.loads(r.read()))


class CareerLeavesFrontend(unittest.TestCase):
    """The Career group has three leaves; the Market and Skills leaves read the existing endpoints."""

    def setUp(self):
        self.html = (Path(__file__).resolve().parents[1] / "kokpit" / "static" / "index.html").read_text(encoding="utf-8")

    def test_three_leaves_and_hashes(self):
        self.assertIn("k:[['jb','Jobs'],['jb/market','Market'],['jb/skills','Skills']]", self.html)
        for alias in ("jobs:'jb'", "'career/market':'jb/market'", "market:'jb/market'", "'career/skills':'jb/skills'", "skills:'jb/skills'"):
            self.assertIn(alias, self.html)
        self.assertIn("jb:S.sub=='market'?jmView:S.sub=='skills'?jsView:jbView", self.html)
        self.assertIn("jb:'jobs'", self.html)   # one [modules] key hides all three leaves (navOn reads the part before '/')

    def test_leaf_guards_and_endpoints(self):
        self.assertIn("if(S.view!='jb'||S.sub)return;", self.html)
        self.assertIn("if(S.view!='jb'||S.sub!='market')return;", self.html)
        self.assertIn("if(S.view!='jb'||S.sub!='skills')return;", self.html)
        self.assertIn("J('/api/career/skills')", self.html)
        self.assertIn("jbPost('/api/career/skills'", self.html)
        self.assertIn('href="/api/career/export.csv"', self.html)

    def test_weekly_note_states(self):
        self.assertIn("No note for this week yet; written by the career job once per week.", self.html)
        self.assertIn("esc(n.title)", self.html)
        self.assertIn("esc(n.advice)", self.html)
        self.assertNotIn("function jbMarket", self.html)   # the plain-text market block left the Jobs leaf
        self.assertNotIn("'#'.repeat", self.html)

    def test_skills_marks(self):
        self.assertIn("Asked for and you do not have", self.html)
        self.assertIn("Hidden (known / skip)", self.html)
        self.assertIn("status=s&&s!=x.status?s:''", self.html)   # pressing the active mark clears it


if __name__ == "__main__":
    unittest.main()
