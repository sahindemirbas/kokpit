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


if __name__ == "__main__":
    unittest.main()
