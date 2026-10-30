"""Today view: new sections appear only for enabled modules and degrade without a database."""
import shutil
import tempfile
import unittest
from pathlib import Path

from kokpit import config as cfgmod
from kokpit.modules.brain import api as brain_api
from kokpit.modules.today import api as today_api


class TodaySections(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.cfg = cfgmod.load(cfgmod.write_default(self.dir / "kokpit.toml"))
        brain_api.init_vault(self.cfg.vault)

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_default_has_no_optional_sections(self):
        s = today_api.view(self.cfg)["sections"]
        for name in ("career", "briefing", "ai_usage", "organiser"):
            self.assertNotIn(name, s)
        self.assertIsInstance(s["brain"]["tasks"], list)
        self.assertIsInstance(s["brain"]["sessions"], list)
        self.assertLessEqual(len(s["brain"]["tasks"]), 6)

    def test_enabled_modules_without_database(self):
        for m in ("jobs", "briefing", "ai_usage", "organiser"):
            self.cfg.modules[m] = True
        s = today_api.view(self.cfg)["sections"]
        self.assertEqual(s["career"]["top"], [])
        self.assertEqual(s["career"]["counts"]["open"], 0)
        self.assertIn("run-job career", s["career"]["reason"])
        self.assertEqual(s["briefing"]["top"], [])
        self.assertEqual(s["briefing"]["audio"], "")
        self.assertIn("today", s["ai_usage"])
        self.assertIn("7d", s["ai_usage"])
        self.assertIn("text", s["organiser"])
        self.assertEqual(s["organiser"]["text"], "")

    def test_brain_tasks_and_sessions_are_listed(self):
        (self.cfg.vault / "To-Do.md").write_text("# To-Do\n## Open\n- [ ] Write the release notes\n", encoding="utf-8")
        b = today_api.view(self.cfg)["sections"]["brain"]
        self.assertEqual(b["open_tasks"], 1)
        self.assertEqual(b["tasks"][0]["text"], "Write the release notes")


if __name__ == "__main__":
    unittest.main()
