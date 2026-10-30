"""Instagram analysis: fake HTTP through a stubbed opener (no network), per-metric errors, stop on auth/rate, token refresh, views, CLI, Connections."""
from __future__ import annotations

import io
import json
import os
import sqlite3
import sys
import tempfile
import unittest
import urllib.error
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from kokpit import config as cfgmod, jobs  # noqa: E402
from kokpit.db import connect  # noqa: E402
from kokpit.modules.connections import api as conn_api  # noqa: E402
from kokpit.modules.instagram import api, collect, token  # noqa: E402
from kokpit.modules.instagram.client import ApiError, Client  # noqa: E402

TOKEN = "FAKE-TOKEN-0123456789"
NOW = datetime(2026, 3, 10, 9, 0, tzinfo=timezone.utc)


class Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


def ok(obj):
    return Resp(json.dumps(obj).encode())


def http_error(status, code=None, msg="bad"):
    body = json.dumps({"error": {"code": code, "message": msg}}).encode() if code is not None else b"{}"
    return urllib.error.HTTPError("https://x/", status, "err", {}, io.BytesIO(body))


def metric(name, value):
    return {"name": name, "total_value": {"value": value}}


class FakeApi:
    """Routes by path; ``fail`` maps a substring of the URL to an exception raised for it. Records every URL."""

    def __init__(self, media=None, stories=None, fail=None):
        self.urls: list[str] = []
        self.media = media if media is not None else [
            {"id": "m1", "media_type": "IMAGE", "media_product_type": "FEED", "timestamp": "2026-03-08T10:00:00+0000", "permalink": "https://example.invalid/p/1"},
            {"id": "m2", "media_type": "VIDEO", "media_product_type": "REELS", "timestamp": "2026-03-09T10:00:00+0000", "permalink": "https://example.invalid/p/2"},
            {"id": "m3", "media_type": "IMAGE", "media_product_type": "FEED", "timestamp": "2026-01-01T10:00:00+0000", "permalink": "https://example.invalid/p/3"},
        ]
        self.stories = stories if stories is not None else [
            {"id": "s1", "media_type": "IMAGE", "media_product_type": "STORY", "timestamp": "2026-03-10T05:00:00+0000", "permalink": "https://example.invalid/s/1"}]
        self.fail = fail or {}

    def __call__(self, url, timeout=None):
        self.urls.append(url)
        for key, exc in self.fail.items():
            if key in url:
                raise exc
        path = url.split("?")[0].replace("https://graph.instagram.com/", "").split("/", 1)[1]
        if path == "me":
            return ok({"followers_count": 120, "media_count": 3, "id": "1"})
        if path == "me/insights":
            name = url.split("metric=")[1].split("&")[0]
            return ok({"data": [metric(name, 10)]})
        if path == "me/media":
            return ok({"data": self.media})
        if path == "me/stories":
            return ok({"data": self.stories})
        if path.endswith("/insights"):
            names = url.split("metric=")[1].split("&")[0].replace("%2C", ",").split(",")
            return ok({"data": [{"name": n, "values": [{"value": 5}]} for n in names]})
        raise AssertionError("unexpected path " + path)


class InstagramTest(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)
        self.addCleanup(self._td.cleanup)
        self._env = mock.patch.dict(os.environ, {"KOKPIT_INSTAGRAM_TOKEN": TOKEN})
        self._env.start()
        self.addCleanup(self._env.stop)

    def cfg(self, on=True, analyse=True, extra=""):
        p = cfgmod.write_default(self.tmp / "kokpit.toml", vault=str(self.tmp / "vault"), db=str(self.tmp / "k.db"))
        text = p.read_text(encoding="utf-8")
        if on:
            text = text.replace("instagram = false", "instagram = true", 1)
        if analyse:
            text = text.replace("analyse = false", "analyse = true", 1)
        text = text.replace('token_file = "data/instagram-token"', f'token_file = "{self.tmp / "tok"}"', 1)
        p.write_text(text + extra, encoding="utf-8")
        return cfgmod.load(p)

    def run_collect(self, cfg, opener):
        conn = connect(cfg.db)
        try:
            return collect.collect(cfg, conn, opener, NOW)
        finally:
            conn.close()

    def test_defaults_are_off(self):
        cfg = self.cfg(on=False, analyse=False)
        self.assertFalse(cfg.modules["instagram"])
        self.assertFalse(cfg.instagram["analyse"])
        self.assertFalse(cfg.instagram["publish"])
        self.assertEqual(api.view(cfg), {"enabled": False})
        rows, info, warns = self.run_collect(cfg, FakeApi())
        self.assertEqual(rows, 0)
        self.assertIn("module is off", warns[0])

    def test_analysis_switch_is_separate(self):
        cfg = self.cfg(on=True, analyse=False)
        fake = FakeApi()
        rows, _, warns = self.run_collect(cfg, fake)
        self.assertEqual((rows, fake.urls), (0, []))
        self.assertIn("analysis is off", warns[0])

    def test_missing_token_is_a_warning(self):
        cfg = self.cfg()
        with mock.patch.dict(os.environ, {"KOKPIT_INSTAGRAM_TOKEN": ""}):
            fake = FakeApi()
            rows, info, warns = self.run_collect(cfg, fake)
            self.assertFalse(api.view(cfg)["configured"])
        self.assertEqual((rows, fake.urls), (0, []))
        self.assertIn("no token", warns[0])

    def test_collect_stores_account_media_and_stories(self):
        cfg = self.cfg()
        fake = FakeApi()
        rows, info, warns = self.run_collect(cfg, fake)
        self.assertEqual(warns, [])
        self.assertEqual((info["media"], info["stories"], info["metrics_failed"]), (3, 1, 0))
        self.assertEqual(rows, 5)
        self.assertTrue(all(u.startswith("https://graph.instagram.com/v") for u in fake.urls))
        d = api.view(cfg, NOW)
        self.assertEqual(d["followers"], [{"day": "2026-03-10", "followers": 120}])
        self.assertEqual((len(d["posts"]), len(d["reels"]), len(d["stories"])), (2, 1, 1))
        self.assertEqual(d["reels"][0]["avg_watch_ms"], 5)  # reel-only metric requested
        self.assertIsNone(d["posts"][0]["avg_watch_ms"])
        self.assertEqual((d["totals_30d"]["posts"], d["totals_30d"]["reels"], d["totals_30d"]["stories"]), (1, 1, 1))  # m3 is older than 30 days
        self.assertIn(d["best_30d"]["product"], ("FEED", "REELS"))
        self.assertEqual(d["last_success"], "2026-03-10T09:00:00Z")
        self.assertNotIn("avg_watch", [u for u in fake.urls if "m1/insights" in u][0])
        self.assertIn("replies", [u for u in fake.urls if "s1/insights" in u][0])

    def test_second_run_same_day_updates_instead_of_duplicating(self):
        cfg = self.cfg()
        self.run_collect(cfg, FakeApi())
        self.run_collect(cfg, FakeApi())
        c = sqlite3.connect(cfg.db)
        self.assertEqual(c.execute("SELECT COUNT(*) FROM ig_account").fetchone()[0], 1)
        self.assertEqual(c.execute("SELECT COUNT(*) FROM ig_media").fetchone()[0], 4)
        self.assertEqual(c.execute("SELECT COUNT(*) FROM ig_media_snap").fetchone()[0], 4)
        c.close()

    def test_one_bad_metric_does_not_break_the_rest(self):
        cfg = self.cfg()
        fake = FakeApi(fail={"metric=profile_views": http_error(400, 100, "metric not supported")})
        # batch call for m2 (reel) fails, then metric by metric: only avg watch time is rejected
        fake.fail["m2/insights?metric=views%2Creach"] = http_error(400, 100, "unsupported combination")
        fake.fail["m2/insights?metric=ig_reels_avg_watch_time"] = http_error(400, 100, "deprecated metric")
        rows, info, warns = self.run_collect(cfg, fake)
        self.assertEqual(info["media"], 3)
        self.assertEqual(info["metrics_failed"], 2)  # one account metric, one reel metric
        errs = {(e["scope"], e["name"]) for e in api.view(cfg, NOW)["errors"]}
        self.assertEqual(errs, {("account", "profile_views"), ("media", "reels:ig_reels_avg_watch_time")})
        self.assertTrue(any("2 call(s) failed" in w for w in warns))
        d = api.view(cfg, NOW)
        self.assertEqual(d["reels"][0]["reach"], 5)  # the other reel metrics survived
        self.assertIsNone(d["reels"][0]["avg_watch_ms"])
        self.assertIsNotNone(d["last_success"])

    def test_auth_error_stops_the_run_without_retry(self):
        cfg = self.cfg()
        fake = FakeApi(fail={"me/media": http_error(400, 190, f"Invalid OAuth access token {TOKEN}")})
        rows, info, warns = self.run_collect(cfg, fake)
        self.assertTrue(warns and "stopped (auth)" in warns[0])
        self.assertEqual(sum("me/media" in u for u in fake.urls), 1)  # no retry
        self.assertEqual(info["media"], 0)
        self.assertNotIn("me/stories", " ".join(fake.urls))
        d = api.view(cfg, NOW)
        self.assertEqual(d["followers"][0]["followers"], 120)  # what was read before the error is kept
        self.assertIsNone(d["last_success"])
        self.assertNotIn(TOKEN, json.dumps(d))
        self.assertNotIn(TOKEN, " ".join(warns))

    def test_rate_limit_stops_the_run(self):
        cfg = self.cfg()
        fake = FakeApi(fail={"m2/insights": http_error(429)})
        rows, info, warns = self.run_collect(cfg, fake)
        self.assertIn("stopped (rate)", warns[0])
        self.assertEqual(info["media"], 1)  # m1 saved, run stopped at m2
        self.assertEqual(sum("m2/insights" in u for u in fake.urls), 1)

    def test_network_error_is_a_warning_not_a_crash(self):
        cfg = self.cfg()
        rows, info, warns = self.run_collect(cfg, FakeApi(fail={"graph.instagram.com": urllib.error.URLError("no route")}))
        self.assertIn("stopped (network)", warns[0])

    def test_job_registry_records_health_warning(self):
        cfg = self.cfg()
        with mock.patch.object(collect, "Client", lambda tok, opener=None: Client(tok, FakeApi(fail={"graph.instagram.com": http_error(401)}))):
            self.assertTrue(jobs.run(cfg, "instagram"))
        c = sqlite3.connect(cfg.db)
        okv, warns = c.execute("SELECT ok, warnings FROM runs WHERE job='instagram'").fetchone()
        c.close()
        self.assertEqual(okv, 1)
        self.assertIn("stopped (auth)", warns)
        self.assertNotIn(TOKEN, warns)

    def test_client_scrubs_token_from_errors(self):
        c = Client(TOKEN, FakeApi(fail={"graph.instagram.com": http_error(400, 100, f"echo {TOKEN} access_token={TOKEN}")}))
        with self.assertRaises(ApiError) as cm:
            c.get("me")
        self.assertNotIn(TOKEN, str(cm.exception))

    def test_only_get_requests(self):
        cfg = self.cfg()
        seen = []

        def opener(url, timeout=None):
            seen.append(url)
            return FakeApi()(url, timeout)

        self.run_collect(cfg, opener)
        self.assertTrue(seen)
        self.assertTrue(all(isinstance(u, str) for u in seen))  # plain URLs, never a Request object with a body or method

    def test_refresh_token_stores_file_and_never_returns_token(self):
        cfg = self.cfg()
        new = "NEW-TOKEN-ABCDEF"
        seen = []

        def opener(url, timeout=None):
            seen.append(url)
            return ok({"access_token": new, "token_type": "bearer", "expires_in": 5184000})

        res = token.refresh(cfg, opener, NOW)
        self.assertEqual(res["expires_at"], "2026-05-09")
        self.assertEqual(res["expires_in_days"], 60)
        self.assertNotIn(new, json.dumps(res))
        self.assertIn("grant_type=ig_refresh_token", seen[0])
        f = token.token_file(cfg)
        self.assertEqual(f.read_text().strip(), new)
        self.assertEqual(f.stat().st_mode & 0o777, 0o600)
        self.assertEqual(token.resolve(cfg), new)  # file wins over the environment

    def test_refresh_failure_keeps_old_token(self):
        cfg = self.cfg()
        with self.assertRaises(ApiError):
            token.refresh(cfg, FakeApi(fail={"refresh_access_token": http_error(400, 190, "expired")}))
        self.assertFalse(token.token_file(cfg).exists())

    def test_cli_summary_has_aggregates_only(self):
        cfg = self.cfg()
        self.run_collect(cfg, FakeApi())
        text = api.summary_text(cfg, NOW)
        self.assertIn("token configured: yes", text)
        self.assertIn("followers: 120", text)
        self.assertIn("1 posts, 1 reels, 1 stories", text)
        for secret in ("example.invalid", "m1", TOKEN):
            self.assertNotIn(secret, text)

    def test_cli_commands(self):
        from kokpit import __main__ as cli
        cfg = self.cfg()
        out = io.StringIO()
        with mock.patch.object(sys, "stdout", out):
            self.assertEqual(cli.main(["--config", str(cfg.path), "instagram", "summary"]), 0)
        self.assertIn("no data yet", out.getvalue())

    def test_connections_entry(self):
        cfg = self.cfg()
        items = {i["id"]: i for i in conn_api.view(cfg)["connections"]}
        self.assertEqual(items["instagram"]["status"], "not run yet")
        self.assertIn("Token configured: yes", items["instagram"]["detail"])
        self.run_collect(cfg, FakeApi())
        items = {i["id"]: i for i in conn_api.view(cfg)["connections"]}
        self.assertNotIn(TOKEN, json.dumps(conn_api.view(cfg)))
        self.assertIn("Last success: 2026-03-10", items["instagram"]["detail"])
        off = {i["id"]: i for i in conn_api.view(self.cfg(on=False, analyse=False))["connections"]}
        self.assertEqual(off["instagram"]["status"], "off")

    def test_http_endpoint(self):
        import threading
        from http.server import ThreadingHTTPServer
        from urllib.request import urlopen
        from kokpit import web
        cfg = self.cfg()
        self.run_collect(cfg, FakeApi())
        srv = ThreadingHTTPServer(("127.0.0.1", 0), web.make_handler(cfg))
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            d = json.loads(urlopen(f"http://127.0.0.1:{srv.server_address[1]}/api/instagram").read())
            self.assertTrue(d["enabled"])
            self.assertEqual(d["followers"][-1]["followers"], 120)
            html = urlopen(f"http://127.0.0.1:{srv.server_address[1]}/").read().decode()
            self.assertIn("soView", html)
        finally:
            srv.shutdown()
            srv.server_close()


if __name__ == "__main__":
    unittest.main()
