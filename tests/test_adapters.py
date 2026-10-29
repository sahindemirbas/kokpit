"""Local event adapter loader: discovery, off by default, DB insert, isolation, user dirs, CLI. No network."""
from __future__ import annotations

import contextlib
import io
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from kokpit import __main__ as cli, config as cfgmod  # noqa: E402
from kokpit.modules.events import adapters, api  # noqa: E402

GOOD = """NAME = "{name}"
CITY = "Testville"
def collect(settings):
    return [{{"source_id": "a1", "title": "Mostra", "start": "2099-02-01", "city": settings["city"], "url": "https://x.test/1"}},
            {{"title": "No id", "start": "2099-02-02", "url": "https://x.test/2", "source": "spoofed"}},
            {{"title": "Far away", "start": "2099-02-03", "lat": 60.0, "lon": 20.0}}]
"""
BROKEN = {
    "imp-err": "import not_a_module_xyz\ndef collect(s):\n    return []\n",
    "no-collect": "NAME = 'no-collect'\n",
    "raises": "def collect(s):\n    raise ValueError('page changed')\n",
    "wrong-type": "def collect(s):\n    return {'title': 'x'}\n",
    "syntax": "def collect(s) return []\n",
}


def _write(root: Path, ident: str, code: str) -> None:
    (root / ident).mkdir(parents=True, exist_ok=True)
    (root / ident / "collect.py").write_text(code, encoding="utf-8")


def _cfg(tmp: Path, enabled=(), dirs=()) -> cfgmod.Config:
    place = {"city": "Testville", "country": "IT", "lat": 45.0, "lon": 9.0}
    p = cfgmod.write_default(tmp / "kokpit.toml", vault=str(tmp / "vault"), db=str(tmp / "k.db"), place=place)
    text = p.read_text(encoding="utf-8")
    text = text.replace("adapters = []", "adapters = [" + ", ".join(f'"{e}"' for e in enabled) + "]", 1)
    text = text.replace("adapter_dirs = []", "adapter_dirs = [" + ", ".join(f'"{d}"' for d in dirs) + "]", 1)
    p.write_text(text, encoding="utf-8")
    return cfgmod.load(p)


def _fetchers_none():
    return {"allevents": lambda *a: [], "eventbrite": lambda *a: []}


class AdapterTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.user = self.tmp / "my-adapters"
        _write(self.user, "testville-good", GOOD.format(name="testville-good"))
        for ident, code in BROKEN.items():
            _write(self.user, ident, code)

    def tearDown(self):
        self._tmp.cleanup()

    def test_discovery_repo_example_and_off_by_default(self):
        cfg = _cfg(self.tmp)
        found = {a["id"]: a for a in adapters.discover(cfg)}
        self.assertIn("example-city", found)
        self.assertFalse(found["example-city"]["enabled"])
        self.assertEqual(found["example-city"]["city"], "Example City")
        self.assertEqual(adapters.plan(cfg), [])

    def test_user_dir_discovered_without_import(self):
        cfg = _cfg(self.tmp, dirs=[str(self.user)])
        found = {a["id"]: a for a in adapters.discover(cfg)}
        self.assertTrue({"testville-good", "imp-err", "syntax", "example-city"} <= set(found))
        self.assertTrue(found["testville-good"]["user"] and not found["example-city"]["user"])
        self.assertNotIn("kokpit_adapter_imp_err", sys.modules)

    def test_enabled_example_writes_to_db(self):
        cfg = _cfg(self.tmp, enabled=["example-city"])
        conn = sqlite3.connect(cfg.db)
        conn.row_factory = sqlite3.Row
        n, info, warnings = api.collect(cfg, conn, fetchers=_fetchers_none())
        rows = conn.execute("SELECT source, source_id, title, category FROM events ORDER BY source_id").fetchall()
        self.assertEqual(n, 2)
        self.assertEqual(info["sources"]["example-city"], 2)
        self.assertEqual({r["source"] for r in rows}, {"example-city"})
        self.assertEqual(rows[0]["category"], "concert")
        api.collect(cfg, conn, fetchers=_fetchers_none())  # dedupe by (source, source_id)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM events").fetchone()[0], 2)
        conn.close()

    def test_broken_adapters_are_isolated(self):
        cfg = _cfg(self.tmp, enabled=["testville-good", *BROKEN, "ghost"], dirs=[str(self.user)])
        conn = sqlite3.connect(cfg.db)
        n, info, warnings = api.collect(cfg, conn, fetchers=_fetchers_none())
        rows = conn.execute("SELECT source, source_id, title FROM events ORDER BY title").fetchall()
        conn.close()
        self.assertEqual(n, 2)  # "Far away" is outside the radius
        self.assertEqual({r[0] for r in rows}, {"testville-good"})
        self.assertIn(("testville-good", "https://x.test/2", "No id"), rows)
        text = " | ".join(warnings)
        for ident in BROKEN:
            self.assertIn(f"{ident}:", text)
            self.assertEqual(info["sources"][ident], 0)
        self.assertIn("import failed: ModuleNotFoundError", text)
        self.assertIn("no collect(settings) function", text)
        self.assertIn("ValueError: page changed", text)
        self.assertIn("must return list[dict], got dict", text)
        self.assertIn("adapter ghost: enabled in kokpit.toml but not found", text)
        self.assertTrue(all(len(w) < 250 for w in warnings))

    def test_job_records_warnings_in_health(self):
        from kokpit import jobs
        cfg = _cfg(self.tmp, enabled=["raises"], dirs=[str(self.user)])
        cfg.events["allevents_slug"] = cfg.events["eventbrite_slug"] = ""
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertTrue(jobs.run(cfg, "events-collect"))
        conn = sqlite3.connect(cfg.db)
        row = conn.execute("SELECT * FROM runs WHERE job='events-collect' ORDER BY rowid DESC LIMIT 1").fetchone()
        conn.close()
        self.assertIn("raises: ValueError: page changed", " ".join(str(x) for x in row))

    def test_cli_lists_adapters(self):
        cfg = _cfg(self.tmp, enabled=["testville-good", "ghost"], dirs=[str(self.user)])
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = cli.main(["--config", str(cfg.path), "adapters"])
        text = out.getvalue()
        self.assertEqual(rc, 0)
        self.assertRegex(text, r"on \s+testville-good")
        self.assertRegex(text, r"off\s+example-city")
        self.assertIn("missing  ghost", text)

    def test_enabled_adapters_alias(self):
        p = self.tmp / "alias.toml"
        p.write_text("[events]\nenabled_adapters = [\"events/example-city\"]\n", encoding="utf-8")
        cfg = cfgmod.load(p)
        self.assertIn("example-city", adapters.enabled_names(cfg))


if __name__ == "__main__":
    unittest.main()
