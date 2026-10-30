"""Old "agenda" names keep working after the rename to briefing."""
from __future__ import annotations

import re
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from kokpit import config as cfgmod, jobs
from kokpit.modules.briefing import collect

OLD = "agenda"
INDEX = Path(__file__).resolve().parents[1] / "kokpit" / "static" / "index.html"


class ConfigCompat(unittest.TestCase):
    def load(self, text):
        d = tempfile.mkdtemp()
        p = Path(d) / "kokpit.toml"
        p.write_text(text)
        with self.assertLogs("kokpit.config", "WARNING") as logs:
            cfg = cfgmod.load(p)
        return cfg, logs.output

    def test_old_section_and_module_flag(self):
        cfg, out = self.load(f"[modules]\n{OLD} = true\n\n[{OLD}]\nmax_per_topic = 2\n")
        self.assertTrue(cfg.modules.get("briefing"))
        self.assertNotIn(OLD, cfg.modules)
        self.assertEqual(cfg.briefing["max_per_topic"], 2)
        self.assertEqual(len(out), 2)  # one warning per old name
        self.assertTrue(all("deprecated" in m for m in out))

    def test_new_name_wins_without_warning(self):
        d = tempfile.mkdtemp()
        p = Path(d) / "kokpit.toml"
        p.write_text(f"[modules]\nbriefing = true\n{OLD} = false\n")
        with self.assertNoLogs("kokpit.config", "WARNING"):
            cfg = cfgmod.load(p)
        self.assertTrue(cfg.modules.get("briefing"))


class JobAlias(unittest.TestCase):
    def test_old_job_name_runs_briefing(self):
        called = []
        with mock.patch.dict(jobs.REGISTRY, {"briefing": lambda cfg: called.append(1)}):
            with mock.patch.object(jobs, "store") as st:
                st.now_iso.return_value = "x"
                try:
                    jobs.run(mock.MagicMock(), OLD)
                except Exception:
                    pass
        self.assertEqual(called, [1])

    def test_alias_not_listed(self):
        self.assertNotIn(OLD, jobs.REGISTRY)


class TableMigration(unittest.TestCase):
    def test_rename_once_and_idempotent(self):
        conn = sqlite3.connect(":memory:")
        conn.execute(f"CREATE TABLE {OLD}_items (day TEXT, score REAL, title TEXT)")
        conn.execute(f"CREATE INDEX idx_{OLD}_day ON {OLD}_items(day, score)")
        conn.execute(f"INSERT INTO {OLD}_items VALUES ('2026-10-30', 1.0, 'kept')")
        self.assertEqual(collect.table_name(conn), f"{OLD}_items")
        collect.ensure_schema(conn)
        collect.ensure_schema(conn)
        names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master")}
        self.assertIn("briefing_items", names)
        self.assertIn("idx_briefing_day", names)
        self.assertNotIn(f"{OLD}_items", names)
        self.assertNotIn(f"idx_{OLD}_day", names)
        self.assertEqual(conn.execute("SELECT title FROM briefing_items").fetchone()[0], "kept")
        self.assertEqual(collect.table_name(conn), "briefing_items")

    def test_fresh_database(self):
        conn = sqlite3.connect(":memory:")
        collect.ensure_schema(conn)
        names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertIn("briefing_items", names)


class UiCompat(unittest.TestCase):
    def test_hash_and_storage_shims(self):
        s = INDEX.read_text()
        self.assertIn(f"location.hash=='#{OLD}'", s)
        self.assertIn("'#briefing'", s)
        self.assertRegex(s, rf"getItem\('kokpit_{OLD}_read'\)")
        self.assertRegex(s, rf"removeItem\('kokpit_{OLD}_read'\)")
        self.assertIn("legacyHash();const n=", s)  # also on hashchange

    def test_routes_alias(self):
        src = (INDEX.parents[1] / "web.py").read_text()
        self.assertTrue(re.search(rf'"/api/briefing", "/api/{OLD}"', src))
        self.assertTrue(re.search(rf'"/api/briefing/audio", "/api/{OLD}/audio"', src))


if __name__ == "__main__":
    unittest.main()
