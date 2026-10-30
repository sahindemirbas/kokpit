"""Career lifecycle, market view and CSV export. Synthetic data only; network calls are mocked."""
from __future__ import annotations

import contextlib
import csv
import io
import sqlite3
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from datetime import datetime, timedelta
from http.server import ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from kokpit import __main__ as cli, web  # noqa: E402
from kokpit.modules.career import api, collect, lifecycle, linkedin, market, profile, store  # noqa: E402
from tests.test_career_listings import _cfg  # noqa: E402

T0 = datetime(2026, 11, 1, 9, 0)


def rec(source, ext, title="Data Analyst", company="Example Corp", **kw):
    return {"source": source, "external_id": ext, "title": title, "company": company, "remote": "unknown", **kw}


def mem():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    return c


def row(conn, ext):
    return conn.execute("SELECT * FROM job_listings WHERE external_id=?", (ext,)).fetchone()


class LifecycleTests(unittest.TestCase):
    def test_adapter_closes_after_n_days_and_reopens(self):
        conn = mem()
        store.upsert(conn, [rec("a", "1"), rec("a", "2", title="BI Developer")], T0.isoformat(timespec="seconds"))
        store.upsert(conn, [rec("a", "2", title="BI Developer")], (T0 + timedelta(days=3)).isoformat(timespec="seconds"))
        info, _ = lifecycle.apply(conn, T0 + timedelta(days=3, hours=1), 3, {"a"})
        self.assertEqual(info["closed_missing"], 1)
        self.assertEqual((row(conn, "1")["status"], row(conn, "2")["status"]), ("closed", "open"))
        self.assertIsNotNone(row(conn, "1")["closed_at"])
        # seen again: reopened
        store.upsert(conn, [rec("a", "1")], (T0 + timedelta(days=5)).isoformat(timespec="seconds"))
        info, _ = lifecycle.apply(conn, T0 + timedelta(days=5, hours=1), 3, {"a"})
        self.assertEqual(info["reopened"], 1)
        r = row(conn, "1")
        self.assertEqual(r["status"], "open")
        self.assertIsNotNone(r["reopened_at"])
        self.assertIsNone(r["closed_at"])

    def test_not_closed_before_n_days_or_when_source_is_not_live(self):
        conn = mem()
        store.upsert(conn, [rec("a", "1")], T0.isoformat(timespec="seconds"))
        self.assertEqual(lifecycle.apply(conn, T0 + timedelta(days=2), 3, {"a"})[0]["closed_missing"], 0)
        self.assertEqual(lifecycle.apply(conn, T0 + timedelta(days=9), 3, set())[0]["closed_missing"], 0)  # adapter returned nothing: no mass closure
        self.assertEqual(row(conn, "1")["status"], "open")

    def test_linkedin_not_seen_is_not_closure(self):
        conn = mem()
        store.upsert(conn, [rec("linkedin", "9")], T0.isoformat(timespec="seconds"))
        info, warns = lifecycle.apply(conn, T0 + timedelta(days=30), 3, {"linkedin"}, check=False)
        self.assertEqual(info["closed_missing"], 0)
        self.assertEqual(row(conn, "9")["status"], "open")

    def test_linkedin_page_marker_closes_and_429_stops(self):
        conn = mem()
        store.upsert(conn, [rec("linkedin", str(i), title=f"Role {i}") for i in range(1, 5)], T0.isoformat(timespec="seconds"))
        asked = []

        def get(url):
            asked.append(url)
            n = url.rsplit("/", 1)[1]
            if n == "1":
                return '<div class="closed-job">No longer accepting applications</div>'
            if n == "2":
                return "<div>apply now</div>"
            raise urllib.error.HTTPError(url, 429, "Too Many", {}, None)
        naps = []
        closed, warns = lifecycle.check_linkedin(conn, T0 + timedelta(days=10), get, naps.append, delay=5)
        self.assertEqual(closed, 1)
        self.assertEqual(len(asked), 3)  # 1, 2, then the 429 stops it
        self.assertEqual(naps, [5, 5])
        self.assertIn("429", warns[0])
        self.assertEqual([row(conn, str(i))["status"] for i in (1, 2, 3, 4)], ["closed", "open", "open", "open"])
        self.assertIsNotNone(row(conn, "2")["checked_at"])

    def test_linkedin_rows_inside_window_are_not_checked(self):
        conn = mem()
        store.upsert(conn, [rec("linkedin", "1")], T0.isoformat(timespec="seconds"))
        closed, _ = lifecycle.check_linkedin(conn, T0 + timedelta(days=2), lambda u: self.fail("must not fetch"), lambda s: None)
        self.assertEqual(closed, 0)

    def test_checks_run_only_when_guest_search_is_enabled(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        cfg = _cfg(Path(tmp.name), enabled=["example-careers"])
        conn = sqlite3.connect(cfg.db)
        conn.row_factory = sqlite3.Row
        store.upsert(conn, [rec("linkedin", "5")], T0.isoformat(timespec="seconds"))
        collect.collect(cfg, conn, linkedin_get=lambda u: self.fail("no network while guest search is off"), now=T0 + timedelta(days=20))
        self.assertEqual(row(conn, "5")["status"], "open")
        conn.close()

    def test_posting_state_codes(self):
        def err(code):
            def get(url):
                raise urllib.error.HTTPError(url, code, "x", {}, None)
            return get
        self.assertEqual(linkedin.posting_state("1", err(404)), "closed")
        self.assertEqual(linkedin.posting_state("1", err(429)), "limit")
        self.assertEqual(linkedin.posting_state("1", err(500)), "error")
        self.assertEqual(linkedin.posting_state("1", lambda u: "<html>fine</html>"), "open")


class MarketTests(unittest.TestCase):
    def test_view_companies_weekly_skills_and_labels(self):
        conn = mem()
        d1 = "2026-10-05T08:00:00"
        store.upsert(conn, [rec("a", "bulk1", title="Old A", company="Alpha", description="SQL and Python"),
                            rec("a", "bulk2", title="Old B", company="Alpha")], d1)  # undated, first run day: bulk load
        store.upsert(conn, [rec("a", "n1", title="New A", company="Alpha", description="Python, dbt; not R&D", posted_at=None),
                            rec("a", "n2", title="New B", company="Beta", description="Excel reporting", posted_at="2026-10-14")], "2026-10-15T08:00:00")
        m = market.view(conn)
        self.assertEqual((m["total"], m["open"]), (4, 4))
        self.assertEqual(m["companies"][0], {"company": "Alpha", "open": 3})
        self.assertEqual(m["companies_n"], 2)  # open companies, not capped by the top list
        weeks = {w["week"]: w["listings"] for w in m["weekly"]}
        self.assertEqual(weeks, {"2026-10-12": 2})  # bulk-load rows excluded, n1 by first_seen, n2 by posted_at
        skills = {s["skill"]: s["open"] for s in m["skills"]}
        self.assertEqual(skills["python"], 2)
        self.assertEqual(skills["sql"], 1)
        self.assertEqual(skills["dbt"], 1)
        self.assertNotIn("r", skills)  # "R&D" is not the R language
        notes = " ".join(m["notes"])
        self.assertIn("short window", notes)
        self.assertIn("2 undated listing(s)", notes)

    def test_empty(self):
        m = market.view(mem())
        self.assertEqual((m["total"], m["weekly"], m["companies_n"]), (0, [], 0))
        self.assertIn("no listings yet", m["notes"][0])

    def test_closed_not_counted_in_open_company_counts(self):
        conn = mem()
        store.upsert(conn, [rec("a", "1"), rec("a", "2", title="Other")], T0.isoformat(timespec="seconds"))
        conn.execute("UPDATE job_listings SET status='closed' WHERE external_id='1'")
        self.assertEqual(market.view(conn)["companies"], [{"company": "Example Corp", "open": 1}])


class ExportTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.cfg = _cfg(self.tmp, enabled=["example-careers"])
        profile.save(self.cfg, {"target_roles": "Data Analyst", "must_have": "SQL"})
        with contextlib.redirect_stdout(io.StringIO()):
            from kokpit import jobs
            jobs.run(self.cfg, "career")

    def tearDown(self):
        self._tmp.cleanup()

    def test_csv_has_scores_reasons_and_unknown(self):
        rows = list(csv.DictReader(io.StringIO(api.export_csv(self.cfg))))
        self.assertEqual(len(rows), 2)
        top = rows[0]
        self.assertEqual(top["title"], "Data Analyst")
        self.assertRegex(top["score"], r"^\d+$")
        self.assertIn("target role", top["reasons"].lower())
        self.assertEqual(top["source"], "example-careers")
        self.assertEqual(set(rows[0]), set(market.CSV_COLUMNS))

    def test_csv_formula_cells_are_neutralised_and_unknown_score(self):
        conn = sqlite3.connect(self.cfg.db)
        conn.execute("UPDATE job_listings SET title='=HYPERLINK(1)', score=NULL WHERE external_id='ex-001'")
        conn.commit()
        conn.close()
        rows = {r["url"]: r for r in csv.DictReader(io.StringIO(api.export_csv(self.cfg)))}
        r = rows["https://example.org/careers/ex-001"]
        self.assertEqual(r["title"], "'=HYPERLINK(1)")
        self.assertEqual(r["score"], "unknown")

    def test_status_filter_and_no_database(self):
        conn = sqlite3.connect(self.cfg.db)
        conn.execute("UPDATE job_listings SET status='closed' WHERE external_id='ex-001'")
        conn.commit()
        conn.close()
        self.assertEqual(len(list(csv.DictReader(io.StringIO(api.export_csv(self.cfg, "closed"))))), 1)
        empty = _cfg(self.tmp / "none")
        self.assertEqual(api.export_csv(empty).strip(), ",".join(market.CSV_COLUMNS))

    def test_cli_export_and_market(self):
        out = self.tmp / "x.csv"
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            self.assertEqual(cli.main(["--config", str(self.cfg.path), "career", "export", "--out", str(out)]), 0)
        self.assertIn("wrote", buf.getvalue())
        self.assertEqual(len(list(csv.DictReader(io.StringIO(out.read_text(encoding="utf-8"))))), 2)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            self.assertEqual(cli.main(["--config", str(self.cfg.path), "career", "market"]), 0)
        self.assertIn("per company", buf.getvalue())
        self.assertIn("Example Corp", buf.getvalue())

    def test_http_export_and_view_market(self):
        srv = ThreadingHTTPServer(("127.0.0.1", 0), web.make_handler(self.cfg))
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        base = f"http://127.0.0.1:{srv.server_address[1]}"
        with urllib.request.urlopen(base + "/api/career/export.csv") as r:
            self.assertTrue(r.headers["Content-Type"].startswith("text/csv"))
            self.assertIn("attachment", r.headers["Content-Disposition"])
            self.assertEqual(len(list(csv.DictReader(io.StringIO(r.read().decode())))), 2)
        import json
        with urllib.request.urlopen(base + "/api/career") as r:
            v = json.loads(r.read())
        self.assertEqual(v["market"]["open"], 2)


if __name__ == "__main__":
    unittest.main()
