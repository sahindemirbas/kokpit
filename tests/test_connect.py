"""Connecting Hermes and Instagram: kokpit hermes status|setup, kokpit instagram setup|check, the Social tab token form.

No network: Instagram answers come from a fake opener. No real ~/.hermes: HOME points to a temp folder.
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import stat
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from kokpit import __main__ as cli, config as cfgmod, hermes  # noqa: E402
from kokpit.modules.connections import api as conn_api  # noqa: E402
from kokpit.modules.instagram import connect as igc, token as ig_token  # noqa: E402
from kokpit.modules.instagram.client import Client as RealClient  # noqa: E402
from kokpit.web import make_handler  # noqa: E402

TOKEN = "FAKE-TOKEN-abc123XYZ"


class Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


def http_error(status, code, msg):
    body = json.dumps({"error": {"code": code, "message": msg}}).encode()
    return urllib.error.HTTPError("https://x/", status, "err", {}, io.BytesIO(body))


class FakeIg:
    def __init__(self, me=None, fail=None, publish_fail=None):
        self.urls: list[str] = []
        self.me = me if me is not None else {"user_id": "1789", "username": "sam.ghent", "account_type": "BUSINESS"}
        self.fail, self.publish_fail = fail, publish_fail

    def __call__(self, req, timeout=None):
        url = req.full_url if hasattr(req, "full_url") else req
        self.urls.append(url)
        if "content_publishing_limit" in url:
            if self.publish_fail:
                raise self.publish_fail
            return Resp(json.dumps({"data": [{"quota_usage": 0}]}).encode())
        if self.fail:
            raise self.fail
        return Resp(json.dumps(self.me).encode())


class Base(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.tmp = Path(self._td.name)
        self.home = self.tmp / "home"
        self.home.mkdir()
        env = {k: v for k, v in os.environ.items() if k not in ("KOKPIT_INSTAGRAM_TOKEN", "KOKPIT_CONFIG")}
        env["HOME"] = str(self.home)
        p = mock.patch.dict(os.environ, env, clear=True)
        p.start()
        self.addCleanup(p.stop)

    def cfg(self, extra: str = ""):
        p = cfgmod.write_default(self.tmp / "kokpit.toml", vault=str(self.tmp / "vault"), db=str(self.tmp / "k.db"))
        text = p.read_text(encoding="utf-8").replace('token_file = "data/instagram-token"', f'token_file = "{self.tmp / "tok"}"', 1)
        p.write_text(text + extra, encoding="utf-8")
        return cfgmod.load(p)


class HermesStatus(Base):
    def test_status_without_hermes_lists_fixes_and_writes_nothing(self):
        cfg = self.cfg()
        before = cfg.path.read_text(encoding="utf-8")
        with mock.patch("shutil.which", return_value=None):
            rows = {r["id"]: r for r in hermes.status(cfg)}
            text = hermes.status_text(cfg)
        self.assertFalse(rows["command"]["ok"])
        self.assertIn(hermes.INSTALL_URL, rows["command"]["fix"])
        self.assertIn('provider = "hermes"', rows["model"]["fix"])
        self.assertIn('readers = ["hermes"]', rows["usage"]["fix"])
        self.assertIn("ln -s", rows["plugin"]["fix"])
        self.assertIn(str(self.home / ".hermes" / "state.db"), rows["state_db"]["what"])
        self.assertIn("KOKPIT_CONFIG=" + str(cfg.path), text)
        self.assertEqual(cfg.path.read_text(encoding="utf-8"), before)
        self.assertFalse((self.home / ".hermes").exists())

    def test_respects_hermes_bin(self):
        cfg = self.cfg()
        text = cfg.path.read_text(encoding="utf-8").replace('# hermes_bin = "hermes"', 'hermes_bin = "my-hermes"', 1)
        self.assertIn('hermes_bin = "my-hermes"', text)
        cfg.path.write_text(text, encoding="utf-8")
        cfg = cfgmod.load(cfg.path)
        seen = []
        with mock.patch("shutil.which", side_effect=lambda b: seen.append(b) or None):
            hermes.status(cfg)
        self.assertEqual(seen, ["my-hermes"])


class HermesSetup(Base):
    def run_setup(self, cfg, answers=None, **kw):
        out: list[str] = []
        it = iter(answers or [])
        with mock.patch("shutil.which", return_value="/usr/local/bin/hermes"):
            rc = hermes.setup(cfg, ask=lambda q: next(it), say=out.append, **kw)
        return rc, "\n".join(out)

    def test_not_installed_stops_without_changes(self):
        cfg = self.cfg()
        before = cfg.path.read_text(encoding="utf-8")
        out: list[str] = []
        with mock.patch("shutil.which", return_value=None):
            rc = hermes.setup(cfg, ask=lambda q: "y", say=out.append, yes=True)
        self.assertEqual(rc, 1)
        self.assertIn(hermes.INSTALL_URL, "\n".join(out))
        self.assertEqual(cfg.path.read_text(encoding="utf-8"), before)
        self.assertFalse((self.home / ".hermes").exists())

    def test_dry_run_changes_nothing(self):
        cfg = self.cfg()
        before = cfg.path.read_text(encoding="utf-8")
        rc, out = self.run_setup(cfg, dry_run=True)
        self.assertEqual(rc, 0)
        self.assertIn("would ask", out)
        self.assertIn("would create the link", out)
        self.assertEqual(cfg.path.read_text(encoding="utf-8"), before)
        self.assertFalse(hermes.plugin_link().exists())

    def test_yes_sets_model_usage_and_link(self):
        cfg = self.cfg()
        rc, out = self.run_setup(cfg, yes=True)
        self.assertEqual(rc, 0)
        fresh = cfgmod.load(cfg.path)
        self.assertTrue(fresh.model["enabled"])
        self.assertEqual(fresh.model["provider"], "hermes")
        self.assertTrue(fresh.modules["ai_usage"])
        self.assertIn("hermes", fresh.ai_usage["readers"])
        link = self.home / ".hermes" / "plugins" / "kokpit-brain"
        self.assertTrue(link.is_symlink())
        self.assertEqual(link.resolve(), hermes.plugin_source().resolve())
        self.assertIn("KOKPIT_CONFIG=" + str(cfg.path), out)
        with mock.patch("shutil.which", return_value="/usr/local/bin/hermes"):
            rows = {r["id"]: r["ok"] for r in hermes.status(fresh)}
        self.assertTrue(rows["model"] and rows["usage"] and rows["plugin"])

    def test_no_answers_change_nothing(self):
        cfg = self.cfg()
        before = cfg.path.read_text(encoding="utf-8")
        rc, _ = self.run_setup(cfg, answers=["n", "", "no"])
        self.assertEqual(rc, 0)
        self.assertEqual(cfg.path.read_text(encoding="utf-8"), before)
        self.assertFalse(hermes.plugin_link().exists())

    def test_never_replaces_existing_other_path(self):
        cfg = self.cfg()
        other = self.tmp / "other-plugin"
        other.mkdir()
        link = self.home / ".hermes" / "plugins" / "kokpit-brain"
        link.parent.mkdir(parents=True)
        link.symlink_to(other)
        rc, out = self.run_setup(cfg, yes=True)
        self.assertEqual(rc, 0)
        self.assertEqual(link.resolve(), other.resolve())
        self.assertIn("left alone", out)
        with mock.patch("shutil.which", return_value="/usr/local/bin/hermes"):
            row = {r["id"]: r for r in hermes.status(cfg)}["plugin"]
        self.assertFalse(row["ok"])
        self.assertIn("does not replace", row["fix"])

    def test_cli_status_exit_code(self):
        cfg = self.cfg()
        buf = io.StringIO()
        with mock.patch("shutil.which", return_value=None), contextlib.redirect_stdout(buf):
            rc = cli.main(["--config", str(cfg.path), "hermes", "status"])
        self.assertEqual(rc, 1)
        self.assertIn("missing  hermes command", buf.getvalue())


class InstagramConnect(Base):
    def test_connect_stores_mode_600_and_switches_on(self):
        cfg = self.cfg()
        api = FakeIg()
        res = igc.connect(cfg, "  " + TOKEN + "\n", api)
        self.assertEqual((res["username"], res["account_type"], res["warning"]), ("sam.ghent", "BUSINESS", ""))
        f = self.tmp / "tok"
        self.assertEqual(f.read_text(encoding="utf-8").strip(), TOKEN)
        self.assertEqual(stat.S_IMODE(f.stat().st_mode), 0o600)
        self.assertEqual(len(api.urls), 1)
        self.assertIn("graph.instagram.com", api.urls[0])
        self.assertIn("/me?", api.urls[0])
        self.assertIn("fields=user_id%2Cusername%2Caccount_type", api.urls[0])
        fresh = cfgmod.load(cfg.path)
        self.assertTrue(fresh.modules["instagram"] and fresh.instagram["analyse"])
        self.assertNotIn(TOKEN, cfg.path.read_text(encoding="utf-8"))
        self.assertNotIn(TOKEN, json.dumps(res))

    def test_personal_account_warns_but_stores(self):
        cfg = self.cfg()
        res = igc.connect(cfg, TOKEN, FakeIg(me={"user_id": "1", "username": "sam", "account_type": "PERSONAL"}))
        self.assertFalse(res["professional"])
        self.assertIn("professional account", res["warning"])

    def test_errors_map_to_advice_and_store_nothing(self):
        cases = [
            (http_error(400, 190, "Error validating access token: Session has expired"), "expired"),
            (http_error(400, 10, "Application does not have permission for this action"), "permission"),
            (http_error(400, 100, "Insufficient developer role"), "invite"),
            (urllib.error.URLError("no route"), "internet"),
        ]
        for err, word in cases:
            with self.subTest(word=word):
                cfg = self.cfg()
                with self.assertRaises(igc.ConnectError) as cm:
                    igc.connect(cfg, TOKEN, FakeIg(fail=err))
                self.assertIn(word, cm.exception.advice)
                self.assertNotIn(TOKEN, cm.exception.advice + cm.exception.detail)
                self.assertFalse((self.tmp / "tok").exists())
                self.assertFalse(cfgmod.load(cfg.path).modules["instagram"])

    def test_bad_input_never_calls_api(self):
        cfg = self.cfg()
        api = FakeIg()
        for bad in ("", "   ", "two words", '"quoted"', None, 12, "x" * 2000):
            with self.subTest(bad=str(bad)[:10]), self.assertRaises(igc.ConnectError):
                igc.connect(cfg, bad, api)
        self.assertEqual(api.urls, [])

    def test_check_with_publish_permission(self):
        cfg = self.cfg("\n")
        text = cfg.path.read_text(encoding="utf-8").replace("publish = false", "publish = true", 1)
        cfg.path.write_text(text, encoding="utf-8")
        cfg = cfgmod.load(cfg.path)
        ig_token.write_file(self.tmp / "tok", TOKEN)
        self.assertTrue(igc.check(cfg, FakeIg())["can_publish"])
        self.assertIs(igc.check(cfg, FakeIg(publish_fail=http_error(403, 10, "no permission")))["can_publish"], False)

    def test_check_without_token(self):
        with self.assertRaises(igc.ConnectError) as cm:
            igc.check(self.cfg(), FakeIg())
        self.assertIn("kokpit instagram setup", cm.exception.advice)

    def test_cli_setup_never_prints_token(self):
        cfg = self.cfg()
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = cli._ig_setup(cfg, ask_secret=lambda p: TOKEN, opener=FakeIg())
        self.assertEqual(rc, 0)
        text = out.getvalue() + err.getvalue()
        self.assertNotIn(TOKEN, text)
        self.assertIn("developers.facebook.com/apps", text)
        self.assertIn("Connected: @sam.ghent (BUSINESS)", text)
        self.assertIn("python -m kokpit instagram collect", text)
        self.assertIn("refresh-token", text)

    def test_cli_setup_error_and_empty(self):
        cfg = self.cfg()
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            self.assertEqual(cli._ig_setup(cfg, ask_secret=lambda p: "", opener=FakeIg()), 1)
            self.assertEqual(cli._ig_setup(cfg, ask_secret=lambda p: TOKEN, opener=FakeIg(fail=http_error(400, 190, "expired"))), 3)
        self.assertIn("Nothing was stored", err.getvalue())
        self.assertNotIn(TOKEN, out.getvalue() + err.getvalue())

    def test_connections_detail_gives_next_step(self):
        cfg = self.cfg()
        text = cfg.path.read_text(encoding="utf-8").replace("instagram = false", "instagram = true", 1)
        cfg.path.write_text(text, encoding="utf-8")
        cfg = cfgmod.load(cfg.path)
        view = conn_api.view(cfg)
        rows = view if isinstance(view, list) else next(v for v in view.values() if isinstance(v, list))
        ig = [i for i in rows if isinstance(i, dict) and "instagram" in str(i.get("id"))]
        self.assertTrue(ig)
        self.assertIn("kokpit instagram setup", ig[0]["detail"])


class TokenEndpoint(Base):
    def setUp(self):
        super().setUp()
        self.c = self.cfg()
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(self.c))
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.addCleanup(self.srv.server_close)
        self.addCleanup(self.srv.shutdown)
        self.url = f"http://127.0.0.1:{self.srv.server_address[1]}/api/instagram/token"

    def post(self, body, headers=None):
        h = {"Content-Type": "application/json", "X-Kokpit": "1"} if headers is None else headers
        req = urllib.request.Request(self.url, data=json.dumps(body).encode(), headers=h, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, r.read().decode()
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode()

    def test_success_never_echoes_token(self):
        fake = FakeIg()
        with mock.patch.object(igc, "Client", lambda tok, opener=None: RealClient(tok, fake)):
            code, body = self.post({"token": TOKEN})
        self.assertEqual(code, 200)
        self.assertNotIn(TOKEN, body)
        self.assertEqual(json.loads(body)["username"], "sam.ghent")
        self.assertEqual((self.tmp / "tok").read_text(encoding="utf-8").strip(), TOKEN)

    def test_error_and_missing_header(self):
        fake = FakeIg(fail=http_error(400, 190, "expired"))
        with mock.patch.object(igc, "Client", lambda tok, opener=None: RealClient(tok, fake)):
            code, body = self.post({"token": TOKEN})
            self.assertEqual(code, 400)
            self.assertNotIn(TOKEN, body)
            self.assertIn("Generate", json.loads(body)["error"])
            code, _ = self.post({"token": TOKEN}, headers={"Content-Type": "application/json"})
            self.assertEqual(code, 403)
            code, _ = self.post({"token": TOKEN}, headers={"Content-Type": "text/plain", "X-Kokpit": "1"})
            self.assertEqual(code, 403)
        self.assertFalse((self.tmp / "tok").exists())


class Locales(unittest.TestCase):
    def test_new_keys_in_both_languages_without_docs_pointer(self):
        root = Path(__file__).resolve().parents[1] / "kokpit" / "locales"
        en = json.loads((root / "en.json").read_text(encoding="utf-8"))
        tr = json.loads((root / "tr.json").read_text(encoding="utf-8"))
        for k in ("social.connect_title", "social.connect_s6", "social.token_ok", "setup.model.hermes_install"):
            self.assertIn(k, en)
            self.assertIn(k, tr)
        for k in ("social.off_help", "social.no_data_token"):
            self.assertNotIn("docs/connections.md", en[k] + tr[k])


if __name__ == "__main__":
    unittest.main()
