"""Companion compact: whole entries move to the archive, nothing is lost. Synthetic vaults only."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from kokpit.modules.brain import compact, reader

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 3, 15, 12, 0)
HEAD = "# Last session\n\nShort cards, newest first.\n\n"


def card(day: int, title: str, size: int = 400) -> str:
    body = f"Done: worked on {title}.\n" + ("Verified: synthetic filler line.\n" * (size // 33))
    return f"## 2026-03-{day:02d} 10:00 · {title} · s{day}\n{body}\n"


def sessions(n: int = 6, size: int = 400) -> str:
    return HEAD + "".join(card(10 - i, f"Task {10 - i}", size) for i in range(n))


THREADS = """# Threads

## Active Threads

### Garden timer
Where we left off (2026-03-10): the timer note exists.
Open: on you: pick a sensor.

## Closed Threads

### Old project A
Closed 2026-01-02. """ + "Long history line. " * 30 + """

### Old project B
Closed 2026-02-02. """ + "Long history line. " * 30 + "\n"


def stripped(text: str) -> str:
    return "".join(ln for ln in text.splitlines(True) if not ln.startswith(compact.MARKER))


class Compact(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.v = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def put(self, name, text):
        (self.v / name).write_text(text, encoding="utf-8")

    def test_within_limit_untouched(self):
        self.put("Last-Session.md", sessions(2))
        before = (self.v / "Last-Session.md").read_bytes()
        rep = compact.compact(self.v, now=NOW)
        self.assertEqual(rep["Last-Session.md"]["status"], "within_limit")
        self.assertEqual(rep["Threads.md"]["status"], "missing")
        self.assertEqual((self.v / "Last-Session.md").read_bytes(), before)
        self.assertFalse((self.v / "archive").exists())

    def test_six_cards_newest_stays(self):
        orig = sessions(6)
        self.put("Last-Session.md", orig)
        rep = compact.compact(self.v, now=NOW, limits={"Last-Session.md": 1200})["Last-Session.md"]
        self.assertEqual(rep["status"], "compacted")
        live = (self.v / "Last-Session.md").read_text(encoding="utf-8")
        arch_path = self.v / "archive" / "Last-Session-2026-03.md"
        self.assertEqual(rep["archive"], "archive/Last-Session-2026-03.md")
        arch = arch_path.read_text(encoding="utf-8")
        self.assertLessEqual(len(live), 1200)
        self.assertEqual(rep["chars_after"], len(live))
        self.assertIn("Task 10", live)
        self.assertNotIn("Task 5", live)
        self.assertIn("## 2026-03-05 10:00 · Task 5", arch)
        self.assertEqual(live.count("Archived text: `archive/Last-Session-2026-03.md`"), 1)
        self.assertIn("\n## Earlier sessions\n", live)
        # every original card is either still live or archived, whole
        for i in range(6):
            c = card(10 - i, f"Task {10 - i}")
            self.assertTrue(c in live or c in arch, c[:30])
        self.assertEqual(rep["moved_chars"] + len(live.split("## Earlier sessions")[0].rstrip("\n")),
                         len(orig.rstrip("\n")))
        names = [s["title"] for s in reader.parse_sessions(live)]
        self.assertEqual(names[0], "Task 10")
        self.assertEqual(len(names), 6 - rep["moved_entries"])

    def test_dry_run_writes_nothing(self):
        self.put("Last-Session.md", sessions(6))
        before = (self.v / "Last-Session.md").read_bytes()
        rep = compact.compact(self.v, dry_run=True, now=NOW, limits={"Last-Session.md": 1200})["Last-Session.md"]
        self.assertEqual(rep["status"], "would_compact")
        self.assertGreater(rep["would_move_entries"], 0)
        self.assertLessEqual(rep["chars_after"], 1200)
        self.assertEqual(rep["moved_entries"], 0)
        self.assertEqual((self.v / "Last-Session.md").read_bytes(), before)
        self.assertFalse((self.v / "archive").exists())

    def test_second_compact_appends_same_month(self):
        self.put("Last-Session.md", sessions(6))
        compact.compact(self.v, now=NOW, limits={"Last-Session.md": 1200})
        live = (self.v / "Last-Session.md").read_text(encoding="utf-8")
        newer = card(20, "Task 20") + card(19, "Task 19")
        self.put("Last-Session.md", live.replace("## 2026-03-10", newer + "## 2026-03-10", 1))
        rep = compact.compact(self.v, now=NOW, limits={"Last-Session.md": 1200})["Last-Session.md"]
        self.assertEqual(rep["status"], "compacted")
        live = (self.v / "Last-Session.md").read_text(encoding="utf-8")
        arch = (self.v / "archive" / "Last-Session-2026-03.md").read_text(encoding="utf-8")
        self.assertIn("Task 20", live)
        self.assertIn("Task 5", arch)
        self.assertIn("Task 10", arch)
        self.assertEqual(arch.count("# Archive: Last-Session.md"), 1)
        self.assertEqual(live.count(compact.MARKER), 1)

    def test_loose_earlier_text_moves_first(self):
        text = sessions(2) + "## Earlier sessions\n" + "Old loose line.\n" * 60
        self.put("Last-Session.md", text)
        rep = compact.compact(self.v, now=NOW, limits={"Last-Session.md": 1200})["Last-Session.md"]
        self.assertEqual(rep["status"], "compacted")
        self.assertEqual(rep["moved_entries"], 1)
        live = (self.v / "Last-Session.md").read_text(encoding="utf-8")
        self.assertNotIn("Old loose line", live)
        self.assertIn("Task 9", live)
        arch = (self.v / "archive" / "Last-Session-2026-03.md").read_text(encoding="utf-8")
        self.assertEqual(arch.count("Old loose line."), 60)

    def test_threads_closed_moves_active_kept(self):
        self.put("Threads.md", THREADS)
        rep = compact.compact(self.v, now=NOW, limits={"Threads.md": 900})["Threads.md"]
        self.assertEqual(rep["status"], "compacted")
        live = (self.v / "Threads.md").read_text(encoding="utf-8")
        arch = (self.v / "archive" / "Threads-2026-03.md").read_text(encoding="utf-8")
        active = THREADS.split("## Closed Threads")[0]
        self.assertTrue(live.startswith(active))
        self.assertIn("### Old project A", arch)
        self.assertNotIn("### Old project A", live)
        self.assertEqual(live.count("## Closed Threads"), 1)
        self.assertEqual(live.count("Archived text: `archive/Threads-2026-03.md`"), 1)
        self.assertLessEqual(len(live), 900)
        self.assertEqual([t["title"] for t in reader.parse_threads(live)], ["Garden timer"])
        self.assertEqual(stripped(live).count("Long history"), THREADS.count("Long history") - arch.count("Long history"))

    def test_threads_active_alone_over_limit(self):
        self.put("Threads.md", THREADS)
        before = (self.v / "Threads.md").read_bytes()
        rep = compact.compact(self.v, now=NOW, limits={"Threads.md": 100})["Threads.md"]
        self.assertEqual(rep["status"], "needs_rewrite")
        self.assertEqual((self.v / "Threads.md").read_bytes(), before)

    def test_single_huge_card_needs_rewrite(self):
        self.put("Last-Session.md", HEAD + card(10, "Huge", 3000))
        before = (self.v / "Last-Session.md").read_bytes()
        rep = compact.compact(self.v, now=NOW, limits={"Last-Session.md": 1000})["Last-Session.md"]
        self.assertEqual(rep["status"], "needs_rewrite")
        self.assertEqual((self.v / "Last-Session.md").read_bytes(), before)
        self.assertFalse((self.v / "archive").exists())

    def test_non_utf8_needs_attention(self):
        (self.v / "Last-Session.md").write_bytes(b"# Last\n\xff\xfe broken " * 500)
        before = (self.v / "Last-Session.md").read_bytes()
        rep = compact.compact(self.v, now=NOW)["Last-Session.md"]
        self.assertEqual(rep["status"], "needs_attention")
        self.assertEqual((self.v / "Last-Session.md").read_bytes(), before)

    def test_archive_folder_blocked_from_context(self):
        from kokpit.modules.brain import context
        self.put("Last-Session.md", sessions(6))
        compact.compact(self.v, now=NOW, limits={"Last-Session.md": 1200})
        arch = self.v / "archive" / "Last-Session-2026-03.md"
        self.assertEqual(context.safe_read(self.v, arch), "")

    def test_cli_compact(self):
        self.put("Last-Session.md", sessions(12))
        cfg = self.v / "kokpit.toml"
        cfg.write_text(f'[brain]\nvault_path = "{self.v}"\n', encoding="utf-8")
        env = {**os.environ, "PYTHONPATH": str(ROOT)}

        def run(*a):
            return subprocess.run([sys.executable, "-m", "kokpit", "--config", str(cfg), "brain", "compact", *a],
                                  cwd=ROOT, env=env, capture_output=True, text=True)
        out = run("--dry-run")
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(json.loads(out.stdout)["Last-Session.md"]["status"], "would_compact")
        self.assertFalse((self.v / "archive").exists())
        out = run()
        self.assertEqual(out.returncode, 0, out.stderr)
        rep = json.loads(out.stdout)["Last-Session.md"]
        self.assertEqual(rep["status"], "compacted")
        self.assertTrue((self.v / rep["archive"]).exists())
        self.assertLessEqual(len((self.v / "Last-Session.md").read_text(encoding="utf-8")), reader.LIMITS["Last-Session.md"])


if __name__ == "__main__":
    unittest.main()
