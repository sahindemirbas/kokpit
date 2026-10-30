"""Personal context projection (brain/context.personal_context) on a synthetic vault. Never writes."""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from kokpit.modules.brain import context  # noqa: E402

TODAY = date(2026, 3, 12)
HOME_PATH = "/" + "home" + "/someone/work/file.txt"   # built at run time so the source holds no literal home path

CORE = f"""# Core
Works with Python and SQL; learning dbt.
Contact: someone@example.org
Draft lives at {HOME_PATH} and ~/drafts/plan.md and /Users/someone/x.md
"""

THREADS = """# Threads
## Active Threads

### Garden timer
Where we left off (2026-03-02): relay wired, schedule not set.
Open: on you: pick hours
Source: [[notes/projects/garden-timer]]

### Old shelf
Where we left off (2025-06-01): paint chosen.

### Secret thread
Where we left off (2026-03-01): blocked by its private note.
Source: [[notes/projects/hidden]]

## Closed Threads
### Done thing
Where we left off (2026-03-05): never shown.
"""

NOTE = """# Garden timer

## Background
Long history that must not be sent.

## 2 March 2026
Relay clicks on GPIO 17.
Call +12 345 678 9012 for parts.
"""

HIDDEN = """---
visibility: private
---
# Hidden
Private plan.
"""


def make_vault(tmp: str) -> Path:
    v = Path(tmp)
    (v / "Core.md").write_text(CORE, encoding="utf-8")
    (v / "Threads.md").write_text(THREADS, encoding="utf-8")
    (v / "notes" / "projects").mkdir(parents=True)
    (v / "notes" / "projects" / "garden-timer.md").write_text(NOTE, encoding="utf-8")
    (v / "notes" / "projects" / "hidden.md").write_text(HIDDEN, encoding="utf-8")
    return v


class PersonalContextTests(unittest.TestCase):
    def test_sources_masking_and_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            ctx = context.personal_context(make_vault(tmp), TODAY)
        self.assertEqual(ctx.status, "ok")
        self.assertEqual(ctx.sources, ("Core.md", "Threads.md", "notes/projects/garden-timer.md"))
        p = ctx.prompt
        self.assertIn("untrusted DATA", p)
        self.assertIn("Python and SQL", p)
        self.assertNotIn("example.org", p)          # contact line dropped
        self.assertNotIn("someone", p)               # every path form masked
        self.assertIn("[path removed]", p)
        self.assertNotIn("Secret thread", p)         # its linked note is private
        self.assertNotIn("Done thing", p)            # closed threads are out
        self.assertLess(p.index("Garden timer"), p.index("Old shelf"))   # newest stated date first
        self.assertIn("Relay clicks", p)
        self.assertNotIn("Long history", p)          # only the latest dated section of a project note
        self.assertNotIn("345 678", p)
        self.assertEqual(len(ctx.fingerprint), 64)

    def test_stable_fingerprint_and_no_writes(self):
        with tempfile.TemporaryDirectory() as tmp:
            v = make_vault(tmp)
            before = sorted((str(p), p.stat().st_mtime_ns) for p in v.rglob("*"))
            a = context.personal_context(v, TODAY)
            b = context.personal_context(v, TODAY)
            after = sorted((str(p), p.stat().st_mtime_ns) for p in v.rglob("*"))
        self.assertEqual(a.fingerprint, b.fingerprint)
        self.assertEqual(before, after)

    def test_missing_empty_stale(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(context.personal_context(Path(tmp) / "nope", TODAY).status, "missing")
            self.assertEqual(context.personal_context(Path(tmp), TODAY).status, "empty")
            v = make_vault(tmp)
            self.assertIn("stale", context.personal_context(v, date(2026, 9, 1)).status)

    def test_symlink_and_blocked_folders_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            v = make_vault(tmp)
            (v / "archive").mkdir()
            (v / "archive" / "a.md").write_text("# A\nold", encoding="utf-8")
            self.assertEqual(context.safe_read(v, v / "archive" / "a.md"), "")
            self.assertEqual(context.safe_read(v, v / "Core.md")[:6], "# Core")
            self.assertEqual(context.safe_read(v, v / ".." / "x.md"), "")
            if hasattr(os, "symlink"):
                os.symlink(v / "Core.md", v / "link.md")
                self.assertEqual(context.safe_read(v, v / "link.md"), "")

    def test_truncation_flag(self):
        with tempfile.TemporaryDirectory() as tmp:
            v = make_vault(tmp)
            many = "".join(f"\n### Item {i}\nWhere we left off (2026-03-0{i % 9 + 1}): {'word ' * 70}\n" for i in range(40))
            (v / "Threads.md").write_text("## Active Threads\n" + many, encoding="utf-8")
            old = context.MAX_PROMPT
            try:
                context.MAX_PROMPT = len(context.GUIDANCE) + 1200
                ctx = context.personal_context(v, TODAY)
            finally:
                context.MAX_PROMPT = old
        self.assertIn("truncated", ctx.status)
        self.assertEqual(ctx.sources, ("Core.md",))

    def test_date_key_formats(self):
        self.assertEqual(context.date_key("x 2026-01-05 y 2026-02-01"), date(2026, 2, 1))
        self.assertEqual(context.date_key("## 3 March 2026"), date(2026, 3, 3))
        self.assertEqual(context.date_key("## 4 Mart, notes from 2026"), date(2026, 3, 4))
        self.assertIsNone(context.date_key("no date"))


if __name__ == "__main__":
    unittest.main()
