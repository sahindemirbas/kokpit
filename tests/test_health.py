import sqlite3
import unittest

from kokpit.modules.health import store


class HealthTest(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row

    def test_warnings(self):
        c = self.conn
        store.record(c, "events", "t0", True, rows=0, day="2026-03-03")
        store.record(c, "digest", "t0", False, day="2026-03-03")
        store.record(c, "stats", "t0", True, rows=12, warnings=["token expires in 3 days"], day="2026-03-03")
        store.record(c, "stats", "t1", True, rows=12, day="2026-03-03")  # last run of the day wins
        w = store.today_warnings(c, expected=["events", "morning"], today="2026-03-03")
        self.assertEqual(w, ["morning: did not run today",
                             "events: last successful update: 0 records",
                             "digest: finished with an error"])

    def test_latest_runs(self):
        c = self.conn
        store.record(c, "a", "t0", False, day="2026-03-02")
        store.record(c, "a", "t1", True, rows=3, info={"x": 1}, day="2026-03-03")
        store.record(c, "b", "t0", True, rows=1, day="2026-03-03")
        runs = store.latest_runs(c)
        self.assertEqual([(r["job"], r["ok"], r["rows"]) for r in runs], [("a", True, 3), ("b", True, 1)])
        self.assertEqual(runs[0]["info"], {"x": 1})

    def test_failing_jobs(self):
        c = self.conn
        store.record(c, "a", "d1", False, info={"error": "old"}, day="2026-03-01")
        store.record(c, "a", "d2", True, rows=1, day="2026-03-02")
        store.record(c, "a", "d3", False, info={"error": "timeout"}, day="2026-03-03")
        store.record(c, "a", "d4", False, info={"error": "HTTP 503"}, day="2026-03-04")
        store.record(c, "b", "d4", False, day="2026-03-04")  # one failure is not a streak
        store.record(c, "z", "d1", False, day="2026-03-01")
        store.record(c, "z", "d2", False, day="2026-03-02")
        store.record(c, "z", "d3", False, day="2026-03-03")
        self.assertEqual(store.failing_jobs(c), [
            {"job": "z", "streak": 3, "since": "d1", "error": ""},
            {"job": "a", "streak": 2, "since": "d3", "error": "HTTP 503"},
        ])
        self.assertEqual([x["job"] for x in store.failing_jobs(c, min_streak=1)], ["z", "a", "b"])


if __name__ == "__main__":
    unittest.main()
