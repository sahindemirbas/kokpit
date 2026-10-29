"""Calendar (ICS): parser, recurrence, job, Today view and secret handling. No network."""
import json
import os
import shutil
import tempfile
import unittest
import urllib.error
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest import mock
from zoneinfo import ZoneInfo

from kokpit import config as cfgmod, jobs
from kokpit.db import connect
from kokpit.modules.connections import api as conn_api
from kokpit.modules.health import api as health_api
from kokpit.modules.today import api as today_api
from kokpit.modules.today import calendar as cal

UTC = timezone.utc
ROME = ZoneInfo("Europe/Rome")
SECRET = "https://calendar.example.org/calendar/ical/synthetic%40example.org/private-0123456789abcdef/basic.ics"


def ics(*events: str) -> str:
    body = "\r\n".join(["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//test//EN", *events, "END:VCALENDAR"])
    return body + "\r\n"


def vevent(*lines: str) -> str:
    return "\r\n".join(["BEGIN:VEVENT", *lines, "END:VEVENT"])


class Parser(unittest.TestCase):
    def test_unfolding_and_escapes(self):
        text = ics(vevent("UID:a", "SUMMARY:Italian les", " son\\, level B1", "LOCATION:Room 2\\;", "\tfloor 1",
                          "DTSTART:20261102T180000Z", "DTEND:20261102T190000Z"))
        (ev,), problems = cal.parse(text)
        self.assertEqual(ev["title"], "Italian lesson, level B1")
        self.assertEqual(ev["location"], "Room 2;floor 1")
        self.assertEqual(problems, set())

    def test_all_day_single_and_multi_day(self):
        text = ics(vevent("UID:a", "SUMMARY:Watering timer check", "DTSTART;VALUE=DATE:20261102", "DTEND;VALUE=DATE:20261103"),
                   vevent("UID:b", "SUMMARY:Trip", "DTSTART;VALUE=DATE:20261101", "DTEND;VALUE=DATE:20261104"))
        events, _ = cal.parse(text)
        self.assertEqual(events[0]["start"], date(2026, 11, 2))
        rows, _ = cal.expand(events, date(2026, 11, 2), tz=ROME)
        self.assertEqual([(r["date"], r["title"], r["all_day"]) for r in rows],
                         [("2026-11-02", "Trip", True), ("2026-11-02", "Watering timer check", True), ("2026-11-03", "Trip", True)])

    def test_utc_z_converted_to_display_zone(self):
        events, _ = cal.parse(ics(vevent("UID:a", "SUMMARY:Call", "DTSTART:20261102T080000Z", "DTEND:20261102T083000Z")))
        self.assertEqual(events[0]["start"], datetime(2026, 11, 2, 8, 0, tzinfo=UTC))
        (row,), _ = cal.expand(events, date(2026, 11, 2), tz=ROME)
        self.assertEqual((row["start"], row["end"]), ("09:00", "09:30"))  # CET = UTC+1

    def test_tzid(self):
        events, _ = cal.parse(ics(vevent("UID:a", "SUMMARY:Standup", 'DTSTART;TZID="America/New_York":20261102T090000',
                                         "DTEND;TZID=America/New_York:20261102T091500")))
        self.assertEqual(events[0]["start"].utcoffset(), timedelta(hours=-5))
        (row,), _ = cal.expand(events, date(2026, 11, 2), tz=ROME)
        self.assertEqual((row["start"], row["end"]), ("15:00", "15:15"))

    def test_unknown_tzid_is_floating_with_a_note(self):
        events, problems = cal.parse(ics(vevent("UID:a", "SUMMARY:x", "DTSTART;TZID=Mars/Olympus:20261102T090000")))
        self.assertIsNone(events[0]["start"].tzinfo)
        self.assertTrue(any("unknown TZID" in p for p in problems))

    def test_weekly_byday_count_and_exdate(self):
        events, _ = cal.parse(ics(vevent(
            "UID:w", "SUMMARY:Italian lesson", "DTSTART;TZID=Europe/Rome:20261026T180000", "DTEND;TZID=Europe/Rome:20261026T193000",
            "RRULE:FREQ=WEEKLY;BYDAY=MO,WE;COUNT=5", "EXDATE;TZID=Europe/Rome:20261104T180000")))
        rows, bad = cal.expand(events, date(2026, 11, 1), days=14, tz=ROME)
        # Oct 26, 28, Nov 2, 4 (excluded), 9 = five occurrences; window from Nov 1
        self.assertEqual(bad, 0)
        self.assertEqual([r["date"] for r in rows], ["2026-11-02", "2026-11-09"])
        self.assertEqual(rows[0]["start"], "18:00")

    def test_daily_interval_until_and_monthly(self):
        events, _ = cal.parse(ics(
            vevent("UID:d", "SUMMARY:Watering timer", "DTSTART:20261101T060000Z", "RRULE:FREQ=DAILY;INTERVAL=2;UNTIL=20261105T235959Z"),
            vevent("UID:m", "SUMMARY:Rent", "DTSTART;VALUE=DATE:20260903", "RRULE:FREQ=MONTHLY"),
            vevent("UID:y", "SUMMARY:Birthday", "DTSTART;VALUE=DATE:20201104", "RRULE:FREQ=YEARLY")))
        rows, bad = cal.expand(events, date(2026, 11, 1), tz=ROME)
        got = sorted((r["title"], r["date"]) for r in rows)
        self.assertEqual(got, [("Birthday", "2026-11-04"), ("Rent", "2026-11-03"), ("Watering timer", "2026-11-01"),
                               ("Watering timer", "2026-11-03"), ("Watering timer", "2026-11-05")])
        self.assertEqual(bad, 0)

    def test_unsupported_rrule_shows_first_occurrence_and_counts(self):
        events, _ = cal.parse(ics(vevent("UID:u", "SUMMARY:Club", "DTSTART:20261102T170000Z",
                                         "RRULE:FREQ=MONTHLY;BYDAY=1MO")))
        rows, bad = cal.expand(events, date(2026, 11, 1), tz=ROME)
        self.assertEqual(bad, 1)
        self.assertEqual([r["date"] for r in rows], ["2026-11-02"])

    def test_recurrence_override_and_cancelled(self):
        events, _ = cal.parse(ics(
            vevent("UID:r", "SUMMARY:Lesson", "DTSTART:20261102T170000Z", "DTEND:20261102T180000Z", "RRULE:FREQ=DAILY;COUNT=3"),
            vevent("UID:r", "RECURRENCE-ID:20261103T170000Z", "SUMMARY:Lesson (moved)", "DTSTART:20261103T190000Z", "DTEND:20261103T200000Z"),
            vevent("UID:c", "SUMMARY:Gone", "STATUS:CANCELLED", "DTSTART:20261102T100000Z")))
        rows, _ = cal.expand(events, date(2026, 11, 2), tz=ROME)
        self.assertEqual([(r["date"], r["title"], r["start"]) for r in rows],
                         [("2026-11-02", "Lesson", "18:00"), ("2026-11-03", "Lesson (moved)", "20:00"), ("2026-11-04", "Lesson", "18:00")])

    def test_window_bounds(self):
        events, _ = cal.parse(ics(vevent("UID:a", "SUMMARY:old", "DTSTART:20261020T100000Z"),
                                  vevent("UID:b", "SUMMARY:far", "DTSTART:20261120T100000Z"),
                                  vevent("UID:c", "SUMMARY:last day", "DTSTART;VALUE=DATE:20261108")))
        rows, _ = cal.expand(events, date(2026, 11, 2), tz=ROME)
        self.assertEqual([r["title"] for r in rows], ["last day"])

    def test_broken_files(self):
        with self.assertRaises(cal.ICSError):
            cal.parse("<html>not a calendar</html>")
        with self.assertRaises(cal.ICSError):
            cal.parse("")
        events, problems = cal.parse(ics(vevent("UID:a", "SUMMARY:no start"), vevent("UID:b", "SUMMARY:bad", "DTSTART:2026-11-02"),
                                         "garbage line", vevent("UID:c", "SUMMARY:ok", "DTSTART:20261102T100000Z")))
        self.assertEqual([e["title"] for e in events], ["ok"])
        self.assertTrue(problems)

    def test_mask_hides_path_and_query(self):
        self.assertEqual(cal.mask(SECRET), "https://calendar.example.org/…")
        self.assertNotIn("private", cal.mask(SECRET + "?token=x"))
        self.assertEqual(cal.mask("not a url"), "calendar")


class JobAndView(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(dir=os.environ.get("TMPDIR")))
        self.cfg_path = cfgmod.write_default(self.dir / "kokpit.toml")
        os.environ.pop("KOKPIT_CALENDAR_ICS_URLS", None)

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def _cfg(self, urls):
        text = self.cfg_path.read_text().replace("calendar_ics_urls = []", "calendar_ics_urls = " + json.dumps(urls))
        self.cfg_path.write_text(text)
        return cfgmod.load(self.cfg_path)

    def _feed(self):
        t = date.today()
        d = lambda n: (t + timedelta(days=n)).strftime("%Y%m%d")
        return ics(vevent("UID:1", "SUMMARY:Italian lesson", f"DTSTART:{d(0)}T120000", f"DTEND:{d(0)}T130000"),
                   vevent("UID:2", "SUMMARY:Watering timer", f"DTSTART;VALUE=DATE:{d(2)}"),
                   vevent("UID:3", "SUMMARY:Weekly call", f"DTSTART:{d(1)}T100000", "RRULE:FREQ=WEEKLY;COUNT=4"))

    def test_not_connected_without_urls(self):
        cfg = cfgmod.load(self.cfg_path)
        self.assertEqual(cfg.calendar_ics_urls, [])
        self.assertEqual(today_api.view(cfg)["sections"]["calendar"]["status"], "not connected")
        self.assertTrue(jobs.run(cfg, "calendar"))  # ok with 0 rows -> health shows "0 records"
        run = [r for r in health_api.view(cfg)["runs"] if r["job"] == "calendar"][0]
        self.assertEqual(run["rows"], 0)

    def test_job_caches_and_view_groups_by_day(self):
        cfg = self._cfg([SECRET, SECRET.replace("private-0", "private-1")])
        self.assertEqual(today_api.view(cfg)["sections"]["calendar"]["status"], "not fetched")
        feed = self._feed()
        with mock.patch("kokpit.fetch.get", side_effect=[feed, urllib.error.HTTPError(SECRET, 404, "nf", {}, None)]):
            self.assertTrue(jobs.run(cfg, "calendar"))
        c = today_api.view(cfg)["sections"]["calendar"]
        self.assertEqual(c["status"], "connected")
        self.assertEqual([e["title"] for e in c["today"]], ["Italian lesson"])
        self.assertEqual([g["events"][0]["title"] for g in c["upcoming"]], ["Weekly call", "Watering timer"])
        self.assertLess(c["age_hours"], 1)
        self.assertIn("HTTP 404", c["error"])
        self._assert_no_secret(cfg)

    def test_all_feeds_failing_is_an_error_without_the_url(self):
        cfg = self._cfg([SECRET])
        with mock.patch("kokpit.fetch.get", side_effect=urllib.error.URLError(f"cannot reach {SECRET}")):
            self.assertFalse(jobs.run(cfg, "calendar"))
        c = today_api.view(cfg)["sections"]["calendar"]
        self.assertEqual(c["status"], "error")
        self.assertIn("calendar 1 (https://calendar.example.org/…)", c["error"])
        self._assert_no_secret(cfg)

    def test_stale_cache_kept_when_a_later_fetch_fails(self):
        cfg = self._cfg([SECRET])
        with mock.patch("kokpit.fetch.get", return_value=self._feed()):
            jobs.run(cfg, "calendar")
        with mock.patch("kokpit.fetch.get", return_value="<html>login</html>"):
            jobs.run(cfg, "calendar")
        c = today_api.view(cfg, now=datetime.now() + timedelta(hours=30))["sections"]["calendar"]
        self.assertEqual(c["status"], "connected")
        self.assertTrue(c["stale"])
        self.assertIn("not an iCalendar file", c["error"])

    def test_env_var_overrides_config(self):
        os.environ["KOKPIT_CALENDAR_ICS_URLS"] = "https://a.example.org/x.ics  https://b.example.org/y.ics"
        try:
            cfg = cfgmod.load(self.cfg_path)
        finally:
            os.environ.pop("KOKPIT_CALENDAR_ICS_URLS")
        self.assertEqual(len(cfg.calendar_ics_urls), 2)
        self.assertNotIn("x.ics", repr(cfg))

    def _assert_no_secret(self, cfg):
        blobs = [json.dumps(today_api.view(cfg)), json.dumps(conn_api.view(cfg)), json.dumps(health_api.view(cfg))]
        conn = connect(cfg.db, readonly=True)
        try:
            for table in ("calendar", "runs"):
                blobs.extend(json.dumps([tuple(r) for r in conn.execute(f"SELECT * FROM {table}")]) for _ in [0])
        finally:
            conn.close()
        for b in blobs:
            self.assertNotIn("private-0123456789abcdef", b)
            self.assertNotIn("/calendar/ical", b)


if __name__ == "__main__":
    unittest.main()
