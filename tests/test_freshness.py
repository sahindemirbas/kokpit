"""Freshness: every data card says when it last updated and how many records it got."""
import json
import shutil
import sqlite3
import tempfile
import threading
import unittest
import urllib.request
from pathlib import Path
from unittest import mock

from kokpit import config as cfgmod, jobs
from kokpit.modules.health import api as health_api, store
from kokpit.web import ThreadingHTTPServer, make_handler

SECRET = "https://calendar.example.org/private/SECRETTOKEN123/basic.ics"


class StoreFreshness(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row

    def test_never_run(self):
        f = store.freshness(self.conn, "weather")
        self.assertEqual(f, {"job": "weather", "last_status": "never run", "last_success": None, "records": None})

    def test_zero_records_is_kept_as_zero(self):
        store.record(self.conn, "events-collect", "t0", True, rows=0)
        f = store.freshness(self.conn, "events-collect")
        self.assertEqual((f["last_status"], f["records"]), ("ok", 0))
        self.assertTrue(f["last_success"])

    def test_last_run_failed_keeps_last_success(self):
        store.record(self.conn, "weather", "t0", True, rows=5)
        good = store.freshness(self.conn, "weather")["last_success"]
        store.record(self.conn, "weather", "t1", False, info={"error": "TimeoutError()"})
        f = store.freshness(self.conn, "weather")
        self.assertEqual((f["last_status"], f["records"], f["last_success"]), ("error", 5, good))
        self.assertEqual(f["error"], "TimeoutError()")

    def test_failed_without_any_success(self):
        store.record(self.conn, "calendar", "t0", False, info={"error": "x"})
        f = store.freshness(self.conn, "calendar")
        self.assertEqual((f["last_status"], f["last_success"], f["records"]), ("error", None, None))


class HttpFreshness(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.cfg = cfgmod.load(cfgmod.write_default(self.dir / "kokpit.toml"))

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_no_database_is_never_run(self):
        self.assertEqual(health_api.freshness(self.cfg, "weather")["last_status"], "never run")

    def test_today_and_events_over_http(self):
        self.cfg.calendar_ics_urls = [SECRET]
        self.cfg.modules["events"] = True

        def bad_calendar(cfg):
            raise jobs.CalendarError("calendar 1 (https://calendar.example.org/…): HTTP 404")

        fake = {"weather": lambda cfg: (0, {}, []), "calendar": bad_calendar}
        with mock.patch.dict(jobs.REGISTRY, fake):
            jobs.run(self.cfg, "heartbeat")
            jobs.run(self.cfg, "weather")
            jobs.run(self.cfg, "calendar")
        self.cfg.setup_done = True  # past the first-run setup screen
        srv = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(self.cfg))
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            base = f"http://127.0.0.1:{srv.server_address[1]}"
            raw = urllib.request.urlopen(base + "/api/today").read().decode()
            ev = json.loads(urllib.request.urlopen(base + "/api/events").read())
            page = urllib.request.urlopen(base + "/").read().decode()
        finally:
            srv.shutdown(); srv.server_close()
        self.assertNotIn("SECRETTOKEN123", raw)
        s = json.loads(raw)["sections"]
        self.assertEqual((s["weather"]["freshness"]["last_status"], s["weather"]["freshness"]["records"]), ("ok", 0))
        cal = s["calendar"]["freshness"]
        self.assertEqual((cal["last_status"], cal["last_success"]), ("error", None))
        self.assertIn("HTTP 404", cal["error"])
        self.assertEqual(s["brain"]["freshness"]["last_status"], "never run")
        self.assertEqual(s["events"]["freshness"]["last_status"], "never run")
        self.assertEqual(ev["freshness"]["job"], "events-collect")
        self.assertIn("function afFresh", page)
        self.assertIn("Last successful update", page)


if __name__ == "__main__":
    unittest.main()
