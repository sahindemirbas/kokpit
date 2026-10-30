"""Scheduler, KOKPIT_HOST and the Docker entrypoint. Synthetic data, no network."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

from kokpit import config as cfgmod, scheduler

ROOT = Path(__file__).resolve().parent.parent
ENTRYPOINT = ROOT / "docker" / "entrypoint.sh"


def _cfg(tmp: Path, **modules) -> cfgmod.Config:
    cfg = cfgmod.Config(path=tmp / "kokpit.toml")
    cfg.modules = {"today": False, "brain": False, "health": True, **modules}
    cfg.db_path = tmp / "kokpit.db"
    return cfg


def _names(cfg) -> set[str]:
    return {e.job for e in scheduler.entries_for(cfg)}


class NextRunTests(unittest.TestCase):
    def test_every_uses_grid_from_midnight(self):
        e = scheduler.Entry("weather", "today", every=30)
        self.assertEqual(scheduler.next_run(e, datetime(2026, 10, 30, 10, 7, 12)), datetime(2026, 10, 30, 10, 30))
        self.assertEqual(scheduler.next_run(e, datetime(2026, 10, 30, 10, 30)), datetime(2026, 10, 30, 11, 0))
        self.assertEqual(scheduler.next_run(e, datetime(2026, 10, 30, 23, 45)), datetime(2026, 10, 31, 0, 0))

    def test_daily_picks_next_time_today_or_tomorrow(self):
        e = scheduler.Entry("instagram", "instagram", at=("07:00", "19:00"))
        self.assertEqual(scheduler.next_run(e, datetime(2026, 10, 30, 6, 0)), datetime(2026, 10, 30, 7, 0))
        self.assertEqual(scheduler.next_run(e, datetime(2026, 10, 30, 7, 0)), datetime(2026, 10, 30, 19, 0))
        self.assertEqual(scheduler.next_run(e, datetime(2026, 10, 30, 19, 1)), datetime(2026, 10, 31, 7, 0))


class EntriesTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_today_module(self):
        self.assertEqual(_names(_cfg(self.tmp, today=True)), {"weather", "calendar"})

    def test_all_off(self):
        self.assertEqual(_names(_cfg(self.tmp)), set())

    def test_events_and_career(self):
        self.assertEqual(_names(_cfg(self.tmp, events=True)), {"events-collect"})
        self.assertEqual(_names(_cfg(self.tmp, jobs=True)), {"career"})

    def test_instagram_and_bot_combinations(self):
        self.assertEqual(_names(_cfg(self.tmp, instagram=True)), {"instagram", "instagram-plan"})
        self.assertEqual(_names(_cfg(self.tmp, bot=True)), {"bot-morning"})
        self.assertEqual(
            _names(_cfg(self.tmp, bot=True, instagram=True)),
            {"instagram", "instagram-plan", "bot-morning", "bot-instagram-remind"},
        )

    def test_bot_poll_never_listed_and_all_names_are_real_jobs(self):
        from kokpit import jobs
        cfg = _cfg(self.tmp, **{m: True for m in ("today", "brain", "events", "jobs", "ai_usage", "instagram", "bot")})
        names = _names(cfg)
        self.assertNotIn("bot-poll", names)
        self.assertTrue(names <= set(jobs.REGISTRY), names - set(jobs.REGISTRY))

    def test_morning_time_follows_config(self):
        cfg = _cfg(self.tmp, bot=True)
        cfg.bot = {**cfg.bot, "morning_time": "06:45"}
        morning = [e for e in scheduler.entries_for(cfg) if e.job == "bot-morning"][0]
        self.assertEqual(morning.at, ("06:45",))


class LoopTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_runs_in_order_survives_errors_and_stops(self):
        cfg = _cfg(self.tmp, today=True)
        calls, stop = [], threading.Event()

        def runner(_cfg, name):
            calls.append(name)
            if name == "weather":
                raise RuntimeError("boom")
            stop.set()

        scheduler.run_loop(cfg, stop, runner=runner, tick=0)
        self.assertEqual(calls, ["weather", "calendar"])
        self.assertTrue(scheduler.heartbeat_path(cfg).exists())

    def test_lock_blocks_second_scheduler(self):
        lock = self.tmp / ".scheduler.lock"
        first = scheduler.acquire_lock(lock)
        self.assertIsNotNone(first)
        self.assertIsNone(scheduler.acquire_lock(lock))
        first.close()
        again = scheduler.acquire_lock(lock)
        self.assertIsNotNone(again)
        again.close()


class HostTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.path = self.tmp / "kokpit.toml"
        cfgmod.write_default(self.path)

    def test_default_stays_loopback(self):
        with mock.patch.dict(os.environ, clear=False):
            os.environ.pop("KOKPIT_HOST", None)
            self.assertEqual(cfgmod.load(self.path).host, "127.0.0.1")

    def test_env_overrides_toml(self):
        with mock.patch.dict(os.environ, {"KOKPIT_HOST": "0.0.0.0"}):
            self.assertEqual(cfgmod.load(self.path).host, "0.0.0.0")


class EntrypointTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        shim = self.bin / "kokpit"
        shim.write_text(f'#!/bin/sh\nexec "{sys.executable}" -m kokpit "$@"\n')
        shim.chmod(0o755)
        self.data = self.tmp / "data"
        self.data.mkdir()

    def _run(self):
        env = {**os.environ, "KOKPIT_DATA": str(self.data), "PATH": f"{self.bin}:{os.environ['PATH']}",
               "PYTHONPATH": str(ROOT)}
        env.pop("KOKPIT_CONFIG", None)
        return subprocess.run(["sh", str(ENTRYPOINT), "echo", "started"], env=env, cwd=self.tmp,
                              capture_output=True, text=True, timeout=60)

    def test_first_start_creates_config_and_vault_then_keeps_it(self):
        r = self._run()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("started", r.stdout)
        conf = self.data / "kokpit.toml"
        self.assertTrue(conf.exists())
        self.assertTrue((self.data / "vault").is_dir())
        conf.write_text(conf.read_text() + "\n# user edit\n")
        before = conf.read_bytes()
        r = self._run()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(conf.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
