"""Setup wizard: toml editing, .env keys, the setup API, the first-run redirect and `kokpit init --yes`."""
import json
import os
import stat
import subprocess
import sys
import tempfile
import threading
import tomllib
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from kokpit import config as cfgmod, secrets, tomledit, wizard  # noqa: E402
from kokpit.web import make_handler  # noqa: E402

KEY = "fake-key-for-tests-only"
SAMPLE = """# top comment
[place]
city = "" # the city
lat = 1.0

[briefing]
enabled = true

[[briefing.topics]]
name = "a"
keywords = ["x"]

# kept comment
[model]
enabled = false
"""


class TomlEditTests(unittest.TestCase):
    def test_replace_keeps_comment(self):
        out = tomledit.set_value(SAMPLE, "place", "city", "Example City")
        self.assertIn('city = "Example City" # the city', out)
        self.assertIn("# top comment", out)

    def test_add_key_and_new_section(self):
        out = tomledit.set_value(SAMPLE, "place", "country", "XX")
        out = tomledit.set_value(out, "ui", "language", "tr")
        data = tomllib.loads(out)
        self.assertEqual(data["place"]["country"], "XX")
        self.assertEqual(data["ui"]["language"], "tr")
        self.assertLess(out.index("country"), out.index("[briefing]"))

    def test_table_vs_array_of_tables(self):
        out = tomledit.set_value(SAMPLE, "briefing", "max_per_topic", 3)
        data = tomllib.loads(out)
        self.assertEqual(data["briefing"]["max_per_topic"], 3)
        self.assertEqual(data["briefing"]["topics"][0]["name"], "a")

    def test_append_and_remove_tables(self):
        out = tomledit.append_table(SAMPLE, "briefing.topics", {"name": "b", "keywords": ["k"], "feeds": []})
        self.assertEqual([t["name"] for t in tomllib.loads(out)["briefing"]["topics"]], ["a", "b"])
        out = tomledit.remove_tables(out, "briefing.topics")
        data = tomllib.loads(out)
        self.assertNotIn("topics", data["briefing"])
        self.assertIn("# kept comment", out)
        self.assertFalse(data["model"]["enabled"])

    def test_quotes_escaped(self):
        out = tomledit.set_value(SAMPLE, "user", "name", 'A "quoted" \\ name\nnext')
        self.assertEqual(tomllib.loads(out)["user"]["name"], 'A "quoted" \\ name\nnext')
        out = tomledit.set_value(out, "user", "tags", ['a"b', "c"])
        self.assertEqual(tomllib.loads(out)["user"]["tags"], ['a"b', "c"])

    def test_invalid_result_not_written(self):
        d = Path(tempfile.mkdtemp())
        p = d / "k.toml"
        p.write_text(SAMPLE)
        with self.assertRaises(ValueError):
            tomledit.write_atomic(p, SAMPLE + "\n[place]\nx = 1\n")  # duplicate table
        self.assertEqual(p.read_text(), SAMPLE)
        with self.assertRaises(ValueError):
            tomledit.set_value("[a]\nb = [1,\n", "a", "c", 1)


class SecretsTests(unittest.TestCase):
    def setUp(self):
        self.p = Path(tempfile.mkdtemp()) / ".env"

    def test_load_does_not_override(self):
        self.p.write_text('# c\nKOKPIT_T1="one"\nexport KOKPIT_T2=\'two\'\nnot a line\n')
        with mock.patch.dict(os.environ, {"KOKPIT_T1": "env"}, clear=False):
            secrets.load_dotenv(self.p)
            self.assertEqual(os.environ["KOKPIT_T1"], "env")
            self.assertEqual(os.environ["KOKPIT_T2"], "two")
            os.environ.pop("KOKPIT_T2")

    def test_set_delete_and_mode(self):
        secrets.set_env_value(self.p, "A_KEY", "v1")
        secrets.set_env_value(self.p, "B_KEY", "v two")
        secrets.set_env_value(self.p, "A_KEY", "v3")
        self.assertEqual(secrets.read(self.p), {"A_KEY": "v3", "B_KEY": "v two"})
        self.assertEqual(stat.S_IMODE(self.p.stat().st_mode), 0o600)
        secrets.set_env_value(self.p, "A_KEY", "")
        self.assertEqual(secrets.names(self.p), ["B_KEY"])
        with self.assertRaises(ValueError):
            secrets.set_env_value(self.p, "bad name", "x")


class ConfigTests(unittest.TestCase):
    def test_defaults_and_fallback(self):
        d = Path(tempfile.mkdtemp())
        cfg = cfgmod.load(cfgmod.write_default(d / "kokpit.toml", name="Ada Example", language="tr"))
        self.assertEqual((cfg.ui_language, cfg.user_name, cfg.setup_done), ("tr", "Ada Example", False))
        p = d / "old.toml"
        p.write_text('[ui]\nlanguage = "xx"\n')
        with self.assertLogs("kokpit.config", "WARNING"):
            cfg = cfgmod.load(p)
        self.assertEqual(cfg.ui_language, "en")
        self.assertTrue(cfg.setup_done)  # no [setup] table: an older config counts as done


class SetupApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.cfg = cfgmod.load(cfgmod.write_default(self.tmp / "kokpit.toml", vault=str(self.tmp / "v"), db=str(self.tmp / "k.db")))
        self.env = mock.patch.dict(os.environ, {}, clear=False)
        self.env.start()
        os.environ.pop("KOKPIT_MODEL_API_KEY", None)
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(self.cfg))
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()
        self.env.stop()

    def req(self, path, body=None, ctype="application/json"):
        data = None if body is None else json.dumps(body).encode()
        r = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", data=data, headers={"Content-Type": ctype} if data else {})
        try:
            with urllib.request.urlopen(r) as resp:
                return resp.status, resp.read().decode()
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode()

    def test_redirect_until_done(self):
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *a, **k):
                return None
        opener = urllib.request.build_opener(NoRedirect)
        with self.assertRaises(urllib.error.HTTPError) as cm:
            opener.open(f"http://127.0.0.1:{self.port}/")
        self.assertEqual((cm.exception.code, cm.exception.headers["Location"]), (302, "/setup"))
        self.assertIn("setup.html" if False else "/api/setup", self.req("/setup")[1])
        self.req("/api/setup", {"done": True})
        st, html = self.req("/")
        self.assertEqual(st, 200)
        self.assertIn("NAVT", html)

    def test_name_language_roundtrip(self):
        st, body = self.req("/api/setup", {"name": "Ada Example", "language": "tr"})
        self.assertEqual(st, 200, body)
        self.assertTrue(json.loads(body)["reload"])
        d = json.loads(self.req("/api/setup")[1])
        self.assertEqual((d["name"], d["language"], d["done"]), ("Ada Example", "tr", False))
        self.assertEqual({x["code"] for x in d["languages"]}, {"en", "tr"})
        raw = tomllib.loads((self.tmp / "kokpit.toml").read_text())
        self.assertEqual(raw["user"]["name"], "Ada Example")

    def test_key_goes_to_env_only(self):
        st, body = self.req("/api/setup", {"model": {"provider": "openai_compatible", "base_url": "https://provider.example/v1",
                                                     "model": "m1", "api_key": KEY, "summaries": True}})
        self.assertEqual(st, 200, body)
        self.assertNotIn(KEY, body)
        st, got = self.req("/api/setup")
        self.assertNotIn(KEY, got)
        d = json.loads(got)["model"]
        self.assertTrue(d["key_set"] and d["summaries"])
        self.assertNotIn(KEY, (self.tmp / "kokpit.toml").read_text())
        self.assertEqual(secrets.read(self.tmp / ".env"), {"KOKPIT_MODEL_API_KEY": KEY})
        self.assertNotIn(KEY, self.req("/api/connections")[1])
        raw = tomllib.loads((self.tmp / "kokpit.toml").read_text())
        self.assertTrue(raw["model"]["enabled"] and raw["model"]["uses"]["digest"] and raw["model"]["uses"]["career_weekly"])
        self.req("/api/setup", {"model": {"provider": "none"}})
        self.assertEqual(secrets.names(self.tmp / ".env"), [])
        self.assertFalse(json.loads(self.req("/api/setup")[1])["model"]["key_set"])

    def test_topics_write_and_clear(self):
        topics = [{"name": "open source releases", "keywords": ["release"], "feeds": ["https://example.org/releases.atom"]}]
        self.assertEqual(self.req("/api/setup", {"topics": topics})[0], 200)
        self.assertEqual(json.loads(self.req("/api/setup")[1])["topics"], topics)
        self.assertTrue(self.cfg.modules["briefing"])
        self.req("/api/setup", {"topics": []})
        self.assertEqual(json.loads(self.req("/api/setup")[1])["topics"], [])
        self.assertFalse(self.cfg.modules["briefing"])
        st, body = self.req("/api/setup", {"topics": [{"name": "x", "feeds": ["file:///etc/passwd"]}]})
        self.assertEqual(st, 400)

    def test_limits_and_content_type(self):
        self.assertEqual(self.req("/api/setup", {"name": "x" * 81})[0], 400)
        self.assertEqual(self.req("/api/setup", {"name": "x"}, ctype="text/plain")[0], 415)
        self.assertEqual(self.req("/api/setup", {"language": "xx"})[0], 400)

    def test_career_and_place(self):
        st, body = self.req("/api/setup", {"career": {"locations": "Example City, Remote", "remote_pref": "hybrid", "target_roles": ["Data analyst"]}})
        self.assertEqual(st, 200, body)
        c = json.loads(body)["career"]
        self.assertEqual((c["locations"], c["remote_pref"], c["target_roles"]), (["Example City", "Remote"], "hybrid", ["Data analyst"]))
        fake = {"place": {"city": "Example City", "country": "XX", "lat": 10.5, "lon": 20.25, "display": "Example City"},
                "slugs": {"allevents_slug": "example-city", "eventbrite_slug": "xx--example-city"}, "counts": {"allevents": 0}}
        with mock.patch.object(wizard, "find_place", return_value=fake):
            st, body = self.req("/api/setup/place", {"city": "Example City"})
        self.assertEqual(json.loads(body)["counts"], {"allevents": 0})
        with mock.patch.object(wizard, "find_place", side_effect=OSError("offline")):
            st, body = self.req("/api/setup/place", {"city": "Example City"})
        self.assertEqual(st, 502)
        self.assertTrue(json.loads(body)["offline"])
        p = {**{k: fake["place"][k] for k in ("city", "country", "lat", "lon")}, **fake["slugs"]}
        self.assertEqual(self.req("/api/setup", {"place": p})[0], 200)
        self.assertTrue(self.cfg.has_place and self.cfg.modules["events"])

    def test_locale(self):
        en = json.loads(self.req("/api/locale")[1])
        tr = json.loads(self.req("/api/locale?lang=tr")[1])
        self.assertEqual((en["language"], tr["language"]), ("en", "tr"))
        self.assertEqual(tr["strings"]["greet.morning"], "Günaydın")
        self.assertEqual(set(en["strings"]), set(json.loads((ROOT / "kokpit/locales/tr.json").read_text())))

    def test_connections_setup_row(self):
        secrets.set_env_value(self.tmp / ".env", "KOKPIT_MODEL_API_KEY", KEY)
        body = self.req("/api/connections")[1]
        self.assertNotIn(KEY, body)
        row = next(c for c in json.loads(body)["connections"] if c["id"] == "setup")
        self.assertEqual(row["status"], "not done")
        self.assertIn("KOKPIT_MODEL_API_KEY", row["detail"])


class CliTests(unittest.TestCase):
    def test_init_yes_without_tty(self):
        d = Path(tempfile.mkdtemp())
        env = {**os.environ, "PYTHONPATH": str(ROOT)}
        out = subprocess.run([sys.executable, "-m", "kokpit", "init", "--yes", "--name", "Ada Example", "--language", "tr"],
                             cwd=d, env=env, capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=60)
        self.assertEqual(out.returncode, 0, out.stderr)
        cfg = cfgmod.load(d / "kokpit.toml")
        self.assertEqual((cfg.user_name, cfg.ui_language, cfg.setup_done), ("Ada Example", "tr", False))

    def test_ask_cli(self):
        d = Path(tempfile.mkdtemp())
        p = cfgmod.write_default(d / "kokpit.toml", vault=str(d / "v"), db=str(d / "k.db"))
        cfgmod.load(p)
        answers = iter(["tr", "Ada Example", "", "", "", "open source releases", "release", "https://example.org/a.atom", "key",
                        "m1", "https://provider.example/v1", "n"])
        with mock.patch.dict(os.environ, {}, clear=False):
            wizard.ask_cli(p, ask=lambda _q: next(answers), ask_secret=lambda _q: KEY, say=lambda *_: None)
            os.environ.pop("KOKPIT_MODEL_API_KEY", None)
        cfg = cfgmod.load(p)
        self.assertEqual((cfg.ui_language, cfg.user_name, cfg.setup_done), ("tr", "Ada Example", True))
        self.assertEqual(cfg.briefing["topics"][0]["name"], "open source releases")
        self.assertNotIn(KEY, p.read_text())
        self.assertEqual(secrets.read(d / ".env"), {"KOKPIT_MODEL_API_KEY": KEY})


if __name__ == "__main__":
    unittest.main()
