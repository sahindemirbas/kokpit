"""Header summary card (/api/summary): null for disabled or missing data, one minute cache."""
import json
import shutil
import tempfile
import threading
import unittest
import urllib.request
from datetime import datetime
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

from kokpit import config as cfgmod
from kokpit.modules.brain import api as brain_api
from kokpit.modules.today import api as today_api
from kokpit.modules.today import summary
from kokpit.web import make_handler

THREADS = """# Threads
## Active Threads

### Launch
Open: on you: approve the licence; on me: write the demo.
"""
TODO = "# To-Do\n## Open\n- [ ] First\n- [ ] Second\n"
NOW = datetime(2026, 3, 2, 10, 0)
WEATHER = {"status": "ok", "city": "Testville", "days": [
    {"date": "2026-03-02", "max": 14.2, "min": 3.1, "text": "rain", "rain_pct": 70},
    {"date": "2026-03-03", "max": 12.0, "min": 2.0, "text": "clear", "rain_pct": 10}]}


class Summary(unittest.TestCase):
    def setUp(self):
        summary.clear_cache()
        self.dir = Path(tempfile.mkdtemp())
        self.cfg = cfgmod.load(cfgmod.write_default(self.dir / "kokpit.toml"))
        brain_api.init_vault(self.cfg.vault)
        (self.cfg.vault / "Threads.md").write_text(THREADS, encoding="utf-8")
        (self.cfg.vault / "To-Do.md").write_text(TODO, encoding="utf-8")

    def tearDown(self):
        summary.clear_cache()
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_counts_and_missing_weather_are_null_not_zero(self):
        s = summary.build(self.cfg, NOW)
        self.assertEqual(s["date"], "2026-03-02")
        self.assertEqual(s["on_you"], 1)
        self.assertEqual(s["tasks"], 2)
        self.assertIsNone(s["weather"])  # no place configured, no database
        self.assertIsNone(s["event"])  # no calendar connected

    def test_disabled_modules_give_null_fields(self):
        self.cfg.modules["brain"] = False
        s = summary.build(self.cfg, NOW)
        self.assertIsNone(s["on_you"])
        self.assertIsNone(s["tasks"])
        self.cfg.modules["brain"] = True
        self.cfg.modules["today"] = False
        s = summary.build(self.cfg, NOW)
        self.assertEqual(s["on_you"], 1)
        self.assertIsNone(s["tasks"])
        self.assertIsNone(s["weather"])
        self.assertIsNone(s["event"])

    def test_missing_vault_is_null(self):
        shutil.rmtree(self.cfg.vault)
        s = summary.build(self.cfg, NOW)
        self.assertIsNone(s["on_you"])
        self.assertIsNone(s["tasks"])

    def test_weather_today_and_rain_threshold(self):
        with mock.patch.object(today_api, "_weather", return_value=WEATHER):
            w = summary.build(self.cfg, NOW)["weather"]
            self.assertEqual((w["city"], w["max"], w["min"], w["rain_pct"]), ("Testville", 14.2, 3.1, 70))
            w = summary.build(self.cfg, datetime(2026, 3, 3, 9))["weather"]
            self.assertIsNone(w["rain_pct"])  # below 30 percent is not shown
            self.assertIsNone(summary.build(self.cfg, datetime(2026, 3, 9, 9))["weather"])
        with mock.patch.object(today_api, "_weather", return_value={"status": "not fetched"}):
            self.assertIsNone(summary.build(self.cfg, NOW)["weather"])

    def test_first_event_within_fourteen_days(self):
        cal = {"status": "connected",
               "today": [{"start": "08:00", "end": "09:00", "all_day": False, "title": "Already over"}],
               "upcoming": [{"date": "2026-03-05", "events": [{"start": "18:30", "end": None, "all_day": False, "title": "Dentist"}]}]}
        with mock.patch.object(today_api, "_calendar", return_value=cal):
            e = summary.build(self.cfg, NOW)["event"]
        self.assertEqual(e, {"date": "2026-03-05", "start": "18:30", "all_day": False, "title": "Dentist"})
        cal["upcoming"] = [{"date": "2026-03-20", "events": [{"start": None, "end": None, "all_day": True, "title": "Far"}]}]
        with mock.patch.object(today_api, "_calendar", return_value=cal):
            self.assertIsNone(summary.build(self.cfg, NOW)["event"])
        cal["today"].append({"start": None, "end": None, "all_day": True, "title": "Holiday"})
        with mock.patch.object(today_api, "_calendar", return_value=cal):
            self.assertEqual(summary.build(self.cfg, NOW)["event"]["title"], "Holiday")

    def test_cache_holds_for_one_minute(self):
        t = [100.0]
        clock = lambda: t[0]
        first = summary.view(self.cfg, NOW, clock=clock)
        (self.cfg.vault / "To-Do.md").write_text(TODO + "- [ ] Third\n", encoding="utf-8")
        t[0] += 30
        self.assertEqual(summary.view(self.cfg, NOW, clock=clock)["tasks"], first["tasks"])
        t[0] += 31
        self.assertEqual(summary.view(self.cfg, NOW, clock=clock)["tasks"], 3)

    def test_http_endpoint(self):
        srv = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(self.cfg))
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{srv.server_address[1]}/api/summary") as r:
                data = json.loads(r.read())
            self.assertEqual(set(data), {"date", "weather", "event", "tasks", "on_you"})
            self.assertEqual(data["on_you"], 1)
        finally:
            srv.shutdown(); srv.server_close()


if __name__ == "__main__":
    unittest.main()
