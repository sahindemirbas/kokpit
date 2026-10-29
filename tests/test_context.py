"""Context hook tests on a synthetic vault. Run: python -m unittest discover tests"""
import importlib.util
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

from kokpit.modules.brain import context

ROOT = Path(__file__).resolve().parents[1]

THREADS = """# Threads
## Active Threads

### Garden timer
Where we left off (2026-03-02): relay wired, schedule not set.
Open: on you: pick watering hours; on me: write the cron line (due: 2026-03-06)
Source: [[notes/projects/garden-timer]]
"""

LAST = """# Last session

## 2026-03-02 21:10 · Garden timer: relay test
Result: relay clicks on GPIO 17; schedule still missing.
"""

NOTE = """---
kind: fact
aliases: [irrigation timer, sulama zamanlayıcısı]
---
# Garden timer

## Parts
Relay board, Pi Zero, 12V valve.
"""

OTHER = """# Italian lesson notes

## Verbs
essere, avere.
"""


def make_vault(tmp: str) -> Path:
    v = Path(tmp)
    (v / "Threads.md").write_text(THREADS, encoding="utf-8")
    (v / "Last-Session.md").write_text(LAST, encoding="utf-8")
    (v / "To-Do.md").write_text("# To-Do\n## Open\n- [ ] Buy valve (due: 2026-03-09)\n", encoding="utf-8")
    (v / "notes" / "projects").mkdir(parents=True)
    (v / "notes" / "projects" / "garden-timer.md").write_text(NOTE, encoding="utf-8")
    (v / "notes" / "italian.md").write_text(OTHER, encoding="utf-8")
    return v


class ContextTests(unittest.TestCase):
    def test_block_has_threads_session_upcoming(self):
        with tempfile.TemporaryDirectory() as tmp:
            v = make_vault(tmp)
            text = context.build_context(v, "", today=date(2026, 3, 3))
        self.assertIn("## Active threads", text)
        self.assertIn("on you: pick watering hours", text)
        self.assertIn("on me: write the cron line (due: 2026-03-06)", text)
        self.assertIn("## Last session", text)
        self.assertIn("relay clicks", text)
        self.assertIn("## Upcoming", text)
        self.assertIn("2026-03-06 (3d)", text)
        self.assertNotIn("## Related notes", text)
        self.assertNotIn("Reminder:", text)

    def test_related_notes_by_alias_not_by_noise(self):
        with tempfile.TemporaryDirectory() as tmp:
            v = make_vault(tmp)
            hits = context.match_notes(v, "sulama zamanlayıcısı için relay")
            self.assertEqual([h["path"] for h in hits], ["notes/projects/garden-timer.md"])
            text = context.build_context(v, "irrigation timer schedule", today=date(2026, 3, 3))
        self.assertIn("## Related notes", text)
        self.assertIn("garden-timer.md", text)
        self.assertNotIn("italian.md", text)

    def test_done_message_adds_reminder_and_never_writes(self):
        with tempfile.TemporaryDirectory() as tmp:
            v = make_vault(tmp)
            before = {p: p.stat().st_mtime_ns for p in v.rglob("*.md")}
            text = context.build_context(v, "cron line is done and pushed", today=date(2026, 3, 3))
            after = {p: p.stat().st_mtime_ns for p in v.rglob("*.md")}
        self.assertIn("Reminder:", text)
        self.assertEqual(before, after)

    def test_budget_is_respected(self):
        with tempfile.TemporaryDirectory() as tmp:
            v = make_vault(tmp)
            text = context.build_context(v, "", max_chars=300, today=date(2026, 3, 3))
        self.assertLessEqual(len(text), 300)
        self.assertTrue(text.startswith("[Kokpit brain"))

    def test_empty_vault_gives_empty_string(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(context.build_context(Path(tmp), "hello"), "")

    def test_cli_context_prints_block(self):
        with tempfile.TemporaryDirectory() as tmp:
            v = make_vault(tmp)
            cfg = Path(tmp) / "kokpit.toml"
            cfg.write_text(f'[brain]\nvault_path = "{v}"\n', encoding="utf-8")
            out = subprocess.run([sys.executable, "-m", "kokpit", "--config", str(cfg), "context", "garden"],
                                 cwd=ROOT, capture_output=True, text=True, check=True).stdout
        self.assertIn("## Active threads", out)
        self.assertIn("garden-timer.md", out)

    def test_hermes_plugin_returns_context_dict(self):
        spec = importlib.util.spec_from_file_location(
            "kokpit_brain_plugin", ROOT / "integrations" / "hermes" / "kokpit-brain" / "__init__.py")
        assert spec and spec.loader
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        hooks = {}

        class Ctx:
            def register_hook(self, name, cb):
                hooks[name] = cb

        mod.register(Ctx())
        self.assertEqual(list(hooks), ["pre_llm_call"])
        with tempfile.TemporaryDirectory() as tmp:
            v = make_vault(tmp)
            old = os.environ.get("KOKPIT_VAULT")
            os.environ["KOKPIT_VAULT"] = str(v)
            try:
                res = hooks["pre_llm_call"](user_message="garden timer status?", session_id="t")
            finally:
                if old is None:
                    del os.environ["KOKPIT_VAULT"]
                else:
                    os.environ["KOKPIT_VAULT"] = old
        self.assertIsInstance(res, dict)
        self.assertIn("## Active threads", res["context"])
        self.assertIn("garden-timer.md", res["context"])


if __name__ == "__main__":
    unittest.main()
