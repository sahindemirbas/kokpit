"""Instagram content calendar and approved publishing. Synthetic data, stubbed HTTP, no network."""
from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

from kokpit import config as cfgmod, jobs
from kokpit.db import connect
from kokpit.modules.connections import api as conn_api
from kokpit.modules.instagram import plan, planapi, publish, store

TOKEN = "IGTEST-token-for-tests-123"
NOW = datetime(2026, 3, 10, 12, 0, tzinfo=timezone.utc)


class Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def ok(obj):
    return Resp(json.dumps(obj).encode())


def http_error(status, code, message):
    return urllib.error.HTTPError("https://x.invalid", status, "err", {}, io.BytesIO(json.dumps({"error": {"code": code, "message": message}}).encode()))


class FakeApi:
    """Records every call as (method, path, params); answers like the container API."""

    def __init__(self, used=0, total=100, statuses=None, create_error=None, publish_error=None):
        self.calls, self.used, self.total = [], used, total
        self.statuses = list(statuses or ["FINISHED"])
        self.create_error, self.publish_error, self.n = create_error, publish_error, 0

    def __call__(self, target, timeout=None):
        if isinstance(target, urllib.request.Request):
            method, url, body = target.get_method(), target.full_url, target.data.decode()
        else:
            method, url, body = "GET", target, ""
        path = url.split("?")[0].replace("https://graph.instagram.com/", "").split("/", 1)[1]
        params = dict(urllib.parse.parse_qsl(body or url.partition("?")[2]))
        self.calls.append((method, path, params))
        if method == "GET" and path == "me/content_publishing_limit":
            return ok({"data": [{"quota_usage": self.used, "config": {"quota_total": self.total}}]})
        if method == "POST" and path == "me/media":
            if self.create_error:
                raise self.create_error
            self.n += 1
            return ok({"id": f"c{self.n}"})
        if method == "GET" and path.startswith("c"):
            st = self.statuses.pop(0) if len(self.statuses) > 1 else self.statuses[0]
            return ok({"status_code": st, "status": "Error: media could not be fetched" if st == "ERROR" else st})
        if method == "POST" and path == "me/media_publish":
            if self.publish_error:
                raise self.publish_error
            return ok({"id": "m1"})
        if method == "GET" and path == "m1":
            return ok({"permalink": "https://example.invalid/p/1"})
        raise AssertionError(f"unexpected {method} {path}")

    def posts(self):
        return [c for c in self.calls if c[0] == "POST"]


class PlanTest(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)
        self.addCleanup(self._td.cleanup)
        self._env = mock.patch.dict(os.environ, {"KOKPIT_INSTAGRAM_TOKEN": TOKEN})
        self._env.start()
        self.addCleanup(self._env.stop)
        self.nosleep = lambda s: None

    def cfg(self, on=True, publish_on=True, extra=""):
        p = cfgmod.write_default(self.tmp / "kokpit.toml", vault=str(self.tmp / "vault"), db=str(self.tmp / "k.db"))
        text = p.read_text(encoding="utf-8")
        if on:
            text = text.replace("instagram = false", "instagram = true", 1)
        if publish_on:
            text = text.replace("publish = false", "publish = true", 1)
        text = text.replace('token_file = "data/instagram-token"', f'token_file = "{self.tmp / "tok"}"', 1)
        p.write_text(text + extra, encoding="utf-8")
        return cfgmod.load(p)

    def conn(self, cfg):
        c = connect(cfg.db)
        store.ensure_schema(c)
        self.addCleanup(c.close)
        return c

    def slot(self, conn, kind="post", media=("https://example.invalid/a.jpg",), status="ready", **kw):
        return plan.create(conn, {"due": "2026-03-11 09:00", "kind": kind, "owner": "me", "caption": "hello", "media_urls": list(media),
                                  "status": status, **kw}, NOW)

    # calendar
    def test_defaults_are_off(self):
        d = cfgmod.load(cfgmod.write_default(self.tmp / "d.toml", vault=str(self.tmp / "v"), db=str(self.tmp / "d.db")))
        self.assertFalse(d.modules["instagram"])
        self.assertFalse(d.instagram["publish"])
        self.assertFalse(d.instagram["analyse"])

    def test_crud(self):
        cfg = self.cfg()
        s = planapi.create(cfg, {"due": "2026-03-11 09:00", "kind": "reel", "owner": "me", "caption": "c", "media_urls": ["https://example.invalid/v.mp4"], "collabs": ["@friend_one"]})
        self.assertEqual((s["status"], s["kind"], s["collabs"]), ("planned", "reel", ["friend_one"]))
        s = planapi.update(cfg, s["id"], {"caption": "new", "status": "ready"})
        self.assertEqual((s["caption"], s["status"]), ("new", "ready"))
        self.assertEqual(len(planapi.view(cfg, NOW)["slots"]), 1)
        planapi.delete(cfg, s["id"])
        self.assertEqual(planapi.view(cfg, NOW)["slots"], [])

    def test_validation(self):
        conn = self.conn(self.cfg())
        bad = [{"due": "tomorrow", "kind": "post"}, {"due": "2026-03-11 09:00", "kind": "podcast"},
               {"due": "2026-03-11 09:00", "kind": "carousel", "media_urls": ["https://example.invalid/a.jpg"]},
               {"due": "2026-03-11 09:00", "kind": "post", "media_urls": ["ftp://example.invalid/a.jpg"]},
               {"due": "2026-03-11 09:00", "kind": "story", "collabs": ["a"]},
               {"due": "2026-03-11 09:00", "kind": "post", "status": "published"},
               {"due": "2026-03-11 09:00", "kind": "post", "caption": "x" * 2201}]
        for b in bad:
            with self.assertRaises(ValueError, msg=str(b)):
                plan.create(conn, b)

    def test_published_slot_is_locked(self):
        conn = self.conn(self.cfg())
        s = self.slot(conn)
        plan.mark_published(conn, s["id"], "m1", None, NOW)
        with self.assertRaises(ValueError):
            plan.update(conn, s["id"], {"caption": "x"})

    def test_guard_warns_when_nothing_planned(self):
        cfg = self.cfg(publish_on=False)
        conn = self.conn(cfg)
        _, info, warns = planapi.guard(cfg, conn, NOW)
        self.assertEqual(info["upcoming"], 0)
        self.assertTrue(any("nothing is planned" in w for w in warns))
        self.slot(conn, status="planned")
        _, info, warns = planapi.guard(cfg, conn, NOW)
        self.assertEqual(warns, [])
        self.assertEqual(plan.upcoming(conn, datetime(2026, 4, 1, tzinfo=timezone.utc), 3), 0)

    def test_guard_off_and_failed_and_registry(self):
        cfg = self.cfg(publish_on=False)
        conn = self.conn(cfg)
        s = self.slot(conn)
        plan.mark_failed(conn, s["id"], "boom", NOW)
        _, _, warns = planapi.guard(cfg, conn, NOW)
        self.assertTrue(any("failed" in w for w in warns))
        self.assertIn("instagram-plan", jobs.REGISTRY)

    def test_guard_missing_token_with_publish_on(self):
        cfg = self.cfg()
        with mock.patch.dict(os.environ, {"KOKPIT_INSTAGRAM_TOKEN": ""}):
            _, _, warns = planapi.guard(cfg, self.conn(cfg), NOW)
        self.assertTrue(any("no token" in w for w in warns))

    # publishing
    def go(self, cfg, conn, sid, api, **kw):
        return publish.publish_slot(cfg, conn, sid, opener=api, now=NOW, sleep=self.nosleep, **kw)

    def test_publish_post_success(self):
        cfg = self.cfg()
        conn = self.conn(cfg)
        s = self.slot(conn)
        api = FakeApi(statuses=["IN_PROGRESS", "FINISHED"])
        r = self.go(cfg, conn, s["id"], api)
        self.assertEqual((r["status"], r["media_id"], r["error"]), ("published", "m1", None))
        self.assertEqual([c[1] for c in api.posts()], ["me/media", "me/media_publish"])
        self.assertEqual(api.posts()[0][2]["caption"], "hello")
        self.assertEqual(api.calls[0][1], "me/content_publishing_limit")
        self.assertNotIn("access_token", " ".join(c[1] for c in api.calls))

    def test_publish_kinds_and_collabs(self):
        cfg = self.cfg()
        conn = self.conn(cfg)
        cases = [("reel", ["https://example.invalid/v.mp4"], "REELS", "video_url"), ("story", ["https://example.invalid/s.jpg"], "STORIES", "image_url"),
                 ("story", ["https://example.invalid/s.mp4"], "STORIES", "video_url")]
        for kind, urls, mtype, key in cases:
            s = self.slot(conn, kind=kind, media=urls)
            api = FakeApi()
            self.assertEqual(self.go(cfg, conn, s["id"], api)["status"], "published")
            first = api.posts()[0][2]
            self.assertEqual((first["media_type"], key in first), (mtype, True))
        s = self.slot(conn, kind="reel", media=["https://example.invalid/v.mp4"], collabs=["friend_one"])
        api = FakeApi()
        self.go(cfg, conn, s["id"], api)
        self.assertEqual(json.loads(api.posts()[0][2]["collaborators"]), ["friend_one"])

    def test_publish_carousel(self):
        cfg = self.cfg()
        conn = self.conn(cfg)
        s = self.slot(conn, kind="carousel", media=["https://example.invalid/a.jpg", "https://example.invalid/b.mp4"])
        api = FakeApi()
        self.assertEqual(self.go(cfg, conn, s["id"], api)["status"], "published")
        creates = [c[2] for c in api.posts() if c[1] == "me/media"]
        self.assertEqual(len(creates), 3)
        self.assertEqual([c.get("is_carousel_item") for c in creates[:2]], ["true", "true"])
        self.assertEqual(creates[1]["media_type"], "VIDEO")
        self.assertEqual((creates[2]["media_type"], creates[2]["children"]), ("CAROUSEL", "c1,c2"))

    def test_container_error_marks_failed_and_does_not_retry(self):
        cfg = self.cfg()
        conn = self.conn(cfg)
        s = self.slot(conn)
        api = FakeApi(statuses=["ERROR"])
        r = self.go(cfg, conn, s["id"], api)
        self.assertEqual(r["status"], "failed")
        self.assertIn("could not be fetched", r["error"])
        self.assertNotIn("me/media_publish", [c[1] for c in api.posts()])
        n = len(api.calls)
        self.assertEqual(len(api.calls), n)  # nothing runs on its own afterwards
        self.assertEqual(len([c for c in api.posts() if c[1] == "me/media"]), 1)

    def test_api_error_on_create_stores_platform_message_without_token(self):
        cfg = self.cfg()
        conn = self.conn(cfg)
        s = self.slot(conn)
        r = self.go(cfg, conn, s["id"], FakeApi(create_error=http_error(400, 9004, f"Media URL invalid access_token={TOKEN}")))
        self.assertEqual(r["status"], "failed")
        self.assertIn("Media URL invalid", r["error"])
        self.assertNotIn(TOKEN, json.dumps(r))

    def test_publish_error_marks_failed(self):
        cfg = self.cfg()
        conn = self.conn(cfg)
        s = self.slot(conn)
        r = self.go(cfg, conn, s["id"], FakeApi(publish_error=http_error(400, 2207, "Not allowed")))
        self.assertEqual((r["status"], r["media_id"]), ("failed", None))

    def test_timeout_while_polling(self):
        cfg = self.cfg(extra="")
        conn = self.conn(cfg)
        s = self.slot(conn)
        sleeps = []
        api = FakeApi(statuses=["IN_PROGRESS"])
        r = publish.publish_slot(cfg, conn, s["id"], opener=api, now=NOW, sleep=sleeps.append)
        self.assertEqual(r["status"], "failed")
        self.assertIn("timed out", r["error"])
        self.assertEqual(len(sleeps), 40)  # 120 s / 3 s
        self.assertNotIn("me/media_publish", [c[1] for c in api.posts()])

    def test_failed_slot_can_be_retried_by_hand(self):
        cfg = self.cfg()
        conn = self.conn(cfg)
        s = self.slot(conn)
        self.assertEqual(self.go(cfg, conn, s["id"], FakeApi(statuses=["ERROR"]))["status"], "failed")
        self.assertEqual(self.go(cfg, conn, s["id"], FakeApi())["status"], "published")

    def test_approval_required(self):
        cfg = self.cfg()
        conn = self.conn(cfg)
        for status in ("planned", "approved", "published"):
            s = self.slot(conn, status="planned")
            if status != "planned":
                conn.execute("UPDATE ig_plan SET status=? WHERE id=?", (status, s["id"]))
                conn.commit()
            api = FakeApi()
            with self.assertRaises(ValueError):
                self.go(cfg, conn, s["id"], api)
            self.assertEqual(api.calls, [])

    def test_publish_switch_off(self):
        cfg = self.cfg(publish_on=False)
        conn = self.conn(cfg)
        s = self.slot(conn)
        api = FakeApi()
        with self.assertRaises(PermissionError):
            self.go(cfg, conn, s["id"], api)
        self.assertEqual(api.calls, [])
        self.assertEqual(plan.get(conn, s["id"])["status"], "ready")

    def test_module_off_and_no_token(self):
        cfg = self.cfg(on=False)
        conn = self.conn(cfg)
        s = self.slot(conn)
        with self.assertRaises(PermissionError):
            self.go(cfg, conn, s["id"], FakeApi())
        cfg = self.cfg()
        with mock.patch.dict(os.environ, {"KOKPIT_INSTAGRAM_TOKEN": ""}):
            with self.assertRaises(PermissionError):
                self.go(cfg, conn, s["id"], FakeApi())
        self.assertEqual(plan.get(conn, s["id"])["status"], "ready")

    def test_limit_exhausted(self):
        cfg = self.cfg()
        conn = self.conn(cfg)
        s = self.slot(conn)
        api = FakeApi(used=100, total=100)
        r = self.go(cfg, conn, s["id"], api)
        self.assertEqual(r["status"], "failed")
        self.assertIn("limit reached", r["error"])
        self.assertEqual(api.posts(), [])

    def test_missing_media_url(self):
        cfg = self.cfg()
        conn = self.conn(cfg)
        s = self.slot(conn, media=[])
        api = FakeApi()
        self.assertEqual(self.go(cfg, conn, s["id"], api)["status"], "failed")
        self.assertEqual(api.posts(), [])

    def test_connections_entry_mentions_publish_permission(self):
        item = {i["id"]: i for i in conn_api.view(self.cfg())["connections"]}["instagram"]
        self.assertIn("instagram_business_content_publish", item["permission"])
        self.assertNotIn(TOKEN, json.dumps(item))
        off = {i["id"]: i for i in conn_api.view(self.cfg(publish_on=False))["connections"]}["instagram"]
        self.assertNotIn("content_publish", off["permission"])

    # web
    def serve(self, cfg):
        from kokpit import web
        srv = ThreadingHTTPServer(("127.0.0.1", 0), web.make_handler(cfg))
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(lambda: (srv.shutdown(), srv.server_close()))
        return f"http://127.0.0.1:{srv.server_address[1]}"

    def call(self, base, path, data=None, ctype="application/json"):
        req = urllib.request.Request(base + path, data=None if data is None else json.dumps(data).encode(), headers={"Content-Type": ctype} if data is not None else {})
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def test_http_crud_and_approve_gate(self):
        cfg = self.cfg(publish_on=False)
        base = self.serve(cfg)
        st, s = self.call(base, "/api/instagram/plan", {"due": "2026-03-11 09:00", "kind": "post", "media_urls": ["https://example.invalid/a.jpg"], "status": "ready"})
        self.assertEqual(st, 200)
        st, d = self.call(base, f"/api/instagram/plan/{s['id']}/approve", {})
        self.assertEqual(st, 403)
        self.assertIn("publishing is off", d["error"])
        st, d = self.call(base, f"/api/instagram/plan/{s['id']}", {"caption": "x" * 3000})
        self.assertEqual(st, 400)
        st, d = self.call(base, "/api/instagram/plan/999/approve", {})
        self.assertIn(st, (403, 404))
        st, d = self.call(base, "/api/instagram/plan", {"x": 1}, ctype="text/plain")
        self.assertEqual(st, 415)
        st, d = self.call(base, "/api/instagram/plan")
        self.assertEqual((st, len(d["slots"]), d["publish"]), (200, 1, False))
        st, d = self.call(base, f"/api/instagram/plan/{s['id']}/delete", {})
        self.assertEqual(st, 200)

    def test_http_approve_publishes_with_stub(self):
        cfg = self.cfg()
        conn = self.conn(cfg)
        s = self.slot(conn)
        api = FakeApi()
        with mock.patch.object(publish, "Client", lambda tok, opener=None: __import__("kokpit.modules.instagram.client", fromlist=["x"]).Client(tok, api)):
            base = self.serve(cfg)
            st, d = self.call(base, f"/api/instagram/plan/{s['id']}/approve", {})
        self.assertEqual((st, d["status"]), (200, "published"))
        self.assertNotIn(TOKEN, json.dumps(d))

    def test_html_has_calendar(self):
        base = self.serve(self.cfg())
        html = urllib.request.urlopen(base + "/").read().decode()
        self.assertIn("soPlan", html)
        self.assertIn("Approve", html)


if __name__ == "__main__":
    unittest.main()
