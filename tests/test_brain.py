"""Brain reader tests on synthetic fixtures. Run: python -m unittest discover tests"""
import tempfile
import unittest
from datetime import date
from pathlib import Path

from kokpit.modules.brain import capture, reader

THREADS = """# Threads
## Active Threads

### Dashboard launch
Where we left off (2026-03-02): skeleton runs, README has a screenshot.
Open: on you: approve the licence; on me: write the demo GIF (2026-03-05); on Ada: review the README.
Source: [[notes/projects/launch]]

### Quiet thread
Where we left off (2026-02-20): nothing open.

## Closed Threads
### Old one
Open: on you: should not appear.
"""

TODO = """# To-Do
## Open
- [ ] Renew the passport (due: 2026-03-10)
- [x] Done thing (due: 2026-01-01)
- [ ] No date task
- [ ] Late one (due: 2026-02-01)

## Done
- [ ] Not in open section
"""

SESSIONS = """# Last session
## 2026-03-02 18:40 · Skeleton · 752b8120
Done: today, brain, health. Open: licence.

## 2026-03-01 10:00 · Docs · 11aa22bb
README tour.

## Earlier sessions
## 2025-01-01 · ancient · 0000
"""

TODAY = date(2026, 3, 3)


class ThreadsTest(unittest.TestCase):
    def test_parses_active_only(self):
        th = reader.parse_threads(THREADS)
        self.assertEqual([t["title"] for t in th], ["Dashboard launch", "Quiet thread"])

    def test_owner_lanes(self):
        th = reader.parse_threads(THREADS)[0]
        self.assertEqual(th["date"], "2026-03-02")
        self.assertIn("skeleton runs", th["status"])
        owners = [(o["owner"], o["on_you"], o["text"]) for o in th["open"]]
        self.assertEqual(owners, [("you", True, "approve the licence"),
                                  ("me", False, "write the demo GIF (2026-03-05)"),
                                  ("ada", False, "review the README")])
        self.assertEqual(th["open"][1]["due"], "2026-03-05")
        self.assertEqual(th["source"], "notes/projects/launch")

    def test_turkish_owner_words(self):
        th = reader.parse_threads("## Active Threads\n### X\nNerede kaldık (29 Ekim): a.\nAçık: Sende: onay; Bende: iş.\n")
        self.assertEqual([o["owner"] for o in th[0]["open"]], ["you", "me"])


class TodoTest(unittest.TestCase):
    def test_open_tasks_and_due(self):
        t = reader.parse_todo(TODO, TODAY)
        self.assertEqual([x["text"] for x in t], ["Late one", "Renew the passport", "No date task"])
        self.assertEqual(t[0]["days_left"], -30)
        self.assertTrue(t[0]["overdue"])
        self.assertEqual(t[1]["days_left"], 7)
        self.assertIsNone(t[2]["days_left"])


class SessionsTest(unittest.TestCase):
    def test_cards_newest_first_and_cut(self):
        s = reader.parse_sessions(SESSIONS)
        self.assertEqual([x["session"] for x in s], ["752b8120", "11aa22bb"])
        self.assertEqual(s[0]["at"], "2026-03-02 18:40")


class UpcomingTest(unittest.TestCase):
    def test_only_plans_not_logs(self):
        th = reader.parse_threads(THREADS)
        todo = reader.parse_todo(TODO, TODAY)
        up = reader.upcoming(th, todo, TODAY)
        self.assertEqual([u["date"] for u in up], ["2026-03-05", "2026-03-10"])  # late task is outside -1 day


class VaultTest(unittest.TestCase):
    def test_read_vault_and_capture(self):
        with tempfile.TemporaryDirectory() as d:
            v = Path(d)
            (v / "Threads.md").write_text(THREADS, encoding="utf-8")
            (v / "To-Do.md").write_text(TODO, encoding="utf-8")
            (v / "Last-Session.md").write_text(SESSIONS, encoding="utf-8")
            data = reader.read_vault(v, TODAY)
            self.assertEqual(len(data["lanes"]["on_you"]), 1)
            self.assertEqual(len(data["lanes"]["others"]), 1)
            self.assertTrue(all(f["exists"] and not f["over"] for f in data["files"]))

            line = capture.capture_task(v, "Buy  milk", "2026-03-04")
            self.assertEqual(line, "- [ ] Buy milk (due: 2026-03-04)")
            text = (v / "To-Do.md").read_text(encoding="utf-8")
            self.assertLess(text.index("Buy milk"), text.index("## Done"))  # inside Open, not after Done
            self.assertIn("Buy milk", [t["text"] for t in reader.parse_todo(text, TODAY)])

            p = capture.capture_note(v, "An idea\nwith a second line")
            self.assertTrue(p.exists() and p.parent.name == "inbox")
            self.assertIn("An idea", p.read_text(encoding="utf-8"))
            p2 = capture.capture_note(v, "An idea\nwith a second line")
            self.assertNotEqual(p, p2)  # never overwrites
            with self.assertRaises(ValueError):
                capture.capture_task(v, "x", "tomorrow")


if __name__ == "__main__":
    unittest.main()
