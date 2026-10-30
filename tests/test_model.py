"""Model connection: a fake local http.server stands in for the provider. No real network, no real key."""
from __future__ import annotations

import json
import os
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest import mock

from kokpit import config as cfgmod
from kokpit.db import connect
from kokpit.modules.connections import api as conn_api
from kokpit.modules.events import api as events_api, store as events_store, translate
from kokpit.modules.model import client

KEY = "sk-" + "test" + "-" + "x" * 6


class Fake(BaseHTTPRequestHandler):
    hits: list = []
    mode = "ok"

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        Fake.hits.append({"path": self.path, "auth": self.headers.get("Authorization"), "body": body})
        if Fake.mode == "fail":
            self.send_response(500); self.end_headers(); self.wfile.write(b"boom " + KEY.encode()); return
        user = body["messages"][1]["content"]
        try:
            items = json.loads(user)
            text = json.dumps([{"id": x["id"], "title": "T:" + x["title"], "description": "D:" + x["description"]} for x in items])
        except ValueError:
            text = "echo " + user
        out = json.dumps({"choices": [{"message": {"content": text}}]}).encode()
        self.send_response(200); self.send_header("Content-Type", "application/json"); self.end_headers(); self.wfile.write(out)

    def log_message(self, *a):
        pass


def make_cfg(tmp: Path, url: str, enabled=True, uses=("events_translate",), translate_to="en") -> cfgmod.Config:
    p = tmp / "kokpit.toml"
    u = "\n".join(f"{k} = true" for k in uses)
    p.write_text(f'''[modules]
events = true
[place]
city = "Testville"
country = "IT"
lat = 45.0
lon = 10.0
radius_km = 40
languages = ["it"]
[events]
translate_to = "{translate_to}"
[model]
enabled = {str(enabled).lower()}
base_url = "{url}"
model = "fake-1"
[model.uses]
{u}
[health]
db_path = "t.db"
''')
    return cfgmod.load(p)


class ModelTests(unittest.TestCase):
    def setUp(self):
        self.srv = HTTPServer(("127.0.0.1", 0), Fake)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.srv.server_port}/v1"
        Fake.hits, Fake.mode = [], "ok"
        self.tmp = Path(tempfile.mkdtemp())
        self.env = mock.patch.dict(os.environ, {"KOKPIT_MODEL_API_KEY": KEY})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.srv.shutdown(); self.srv.server_close()

    def test_defaults_off(self):
        cfg = cfgmod.Config(path=self.tmp / "x.toml")
        self.assertFalse(cfg.model["enabled"])
        self.assertFalse(any(cfg.model["uses"].values()))
        self.assertEqual(cfg.events.get("translate_to", ""), "")
        self.assertIn("enabled = false", cfgmod.DEFAULT_TOML.split("\n[model]")[1])

    def test_disabled_makes_no_network_call(self):
        for kw in ({"enabled": False}, {"uses": ()}):
            cfg = make_cfg(self.tmp, self.url, **kw)
            with self.assertRaises(client.ModelDisabled):
                client.complete(cfg, "events_translate", "s", "u", 10)
        cfg = make_cfg(self.tmp, self.url)
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("KOKPIT_MODEL_API_KEY")
            with self.assertRaises(client.ModelDisabled):
                client.complete(cfg, "events_translate", "s", "u", 10)
        with self.assertRaises(client.ModelDisabled):
            client.complete(make_cfg(self.tmp, self.url, uses=("digest",)), "events_translate", "s", "u", 10)
        self.assertEqual(Fake.hits, [])

    def test_complete_and_record(self):
        cfg = make_cfg(self.tmp, self.url)
        self.assertEqual(client.complete(cfg, "events_translate", "sys", "hello", 50), "echo hello")
        h = Fake.hits[0]
        self.assertEqual(h["path"], "/v1/chat/completions")
        self.assertEqual(h["auth"], "Bearer " + KEY)
        self.assertEqual(h["body"]["model"], "fake-1")
        item = conn_api._model_item(cfg)
        self.assertEqual(item["status"], "ok")
        self.assertIn("event title and description", item["detail"])
        self.assertNotIn(KEY, json.dumps(item))
        self.assertNotIn(KEY, (cfg.db).read_bytes().decode("latin-1"))

    def test_failure_is_recorded_and_scrubbed(self):
        cfg = make_cfg(self.tmp, self.url)
        Fake.mode = "fail"
        with self.assertRaises(client.ModelError) as cm:
            client.complete(cfg, "events_translate", "s", "u", 10)
        self.assertNotIn(KEY, str(cm.exception))
        item = conn_api._model_item(cfg)
        self.assertEqual(item["status"], "warning")
        self.assertNotIn(KEY, json.dumps(item))

    def test_connection_off_item(self):
        cfg = make_cfg(self.tmp, self.url, enabled=False)
        item = conn_api._model_item(cfg)
        self.assertEqual((item["status"], item["enabled"]), ("off", False))
        self.assertEqual(Fake.hits, [])

    # Hermes backend: subprocess.run and shutil.which are always faked, no real command ever runs.

    def _hermes_cfg(self, model="", extra=""):
        p = self.tmp / "hermes.toml"
        p.write_text(f'''[model]
enabled = true
provider = "hermes"
model = "{model}"
{extra}
[model.uses]
digest = true
[health]
db_path = "h.db"
''')
        return cfgmod.load(p)

    def test_provider_default_is_openai_compatible(self):
        self.assertEqual(cfgmod.Config(path=self.tmp / "x.toml").model["provider"], "openai_compatible")
        cfg = make_cfg(self.tmp, self.url)  # an older file without a provider line
        self.assertEqual(cfg.model["provider"], "openai_compatible")
        self.assertIn('provider = "openai_compatible"', cfgmod.DEFAULT_TOML.split("\n[model]")[1])
        with mock.patch.object(client.subprocess, "run") as run:
            self.assertEqual(client.complete(cfg, "events_translate", "sys", "hi", 20), "echo hi")
            run.assert_not_called()
        self.assertEqual(conn_api._model_item(cfg)["provider"], "openai_compatible")

    def test_hermes_missing_command_is_disabled(self):
        cfg = self._hermes_cfg()
        os.environ.pop("KOKPIT_MODEL_API_KEY")
        with mock.patch.object(client.shutil, "which", return_value=None), \
                mock.patch.object(client.subprocess, "run") as run:
            self.assertFalse(client.use_enabled(cfg, "digest"))
            with self.assertRaises(client.ModelDisabled) as cm:
                client.complete(cfg, "digest", "s", "u", 10)
            self.assertIn("hermes command not found", str(cm.exception))
            self.assertEqual(conn_api._model_item(cfg)["status"], "not configured")
            run.assert_not_called()
        self.assertEqual(Fake.hits, [])

    def test_hermes_success_is_recorded(self):
        cfg = self._hermes_cfg(model="example/model-a", extra='hermes_provider = "example"\nhermes_bin = "agent-cli"')
        os.environ.pop("KOKPIT_MODEL_API_KEY")
        done = client.subprocess.CompletedProcess([], 0, stdout="\x1b[1m  Short summary.  \x1b[0m\n", stderr="\nsession_id: s1\n")
        with mock.patch.object(client.shutil, "which", return_value="/usr/bin/agent-cli"), \
                mock.patch.object(client.subprocess, "run", return_value=done) as run:
            self.assertTrue(client.use_enabled(cfg, "digest"))
            self.assertEqual(client.complete(cfg, "digest", "SYS", "USER", 50), "Short summary.")
            item = conn_api._model_item(cfg)
        args, kw = run.call_args
        cmd = args[0]
        self.assertEqual(cmd[:4], ["agent-cli", "chat", "--oneshot", "-Q"])
        self.assertIn("--query-file", cmd)
        self.assertEqual(cmd[cmd.index("-m") + 1], "example/model-a")
        self.assertEqual(cmd[cmd.index("--provider") + 1], "example")
        self.assertEqual(kw["input"], "SYS\n\nUSER")
        self.assertNotIn("USER", " ".join(cmd))
        self.assertEqual(kw["timeout"], cfg.model["timeout"])
        conn = connect(cfg.db)
        r = conn.execute("SELECT ok, info FROM runs WHERE job = 'model:digest'").fetchone()
        conn.close()
        self.assertEqual(r[0], 1)
        info = json.loads(r[1])
        self.assertEqual((info["provider"], info["model"]), ("hermes", "example/model-a"))
        self.assertEqual((item["status"], item["provider"], item["target"]), ("ok", "hermes", "agent-cli"))
        self.assertIn("no API key", item["detail"])
        self.assertEqual(Fake.hits, [])

    def test_hermes_failure_is_recorded_and_short(self):
        cfg = self._hermes_cfg()
        long_err = "boom " + "x" * 500
        done = client.subprocess.CompletedProcess([], 2, stdout="", stderr=long_err)
        with mock.patch.object(client.shutil, "which", return_value="/usr/bin/hermes"), \
                mock.patch.object(client.subprocess, "run", return_value=done) as run:
            with self.assertRaises(client.ModelError) as cm:
                client.complete(cfg, "digest", "s", "u", 10)
            self.assertNotIn("-m", run.call_args[0][0])
            item = conn_api._model_item(cfg)
        msg = str(cm.exception)
        self.assertIn("hermes exited with 2", msg)
        self.assertLess(len(msg), 220)
        self.assertNotIn("x" * 200, msg)
        conn = connect(cfg.db)
        r = conn.execute("SELECT ok FROM runs WHERE job = 'model:digest'").fetchone()
        conn.close()
        self.assertEqual(r[0], 0)
        self.assertEqual(item["status"], "warning")
        empty = client.subprocess.CompletedProcess([], 0, stdout="\x1b[0m  \n", stderr="")
        with mock.patch.object(client.shutil, "which", return_value="/usr/bin/hermes"), \
                mock.patch.object(client.subprocess, "run", return_value=empty):
            with self.assertRaises(client.ModelError) as cm:
                client.complete(cfg, "digest", "s", "u", 10)
        self.assertEqual(str(cm.exception), "empty answer")

    def _records(self):
        return [{"source": "t", "source_id": "1", "title": "Sagra", "description": "Musica", "price": "", "category": "other", "start": "2026-11-01"},
                {"source": "t", "source_id": "2", "title": "Mostra", "description": "", "price": "", "category": "other", "start": "2026-11-02"}]

    def test_translate_events_and_cache(self):
        cfg = make_cfg(self.tmp, self.url)
        conn = connect(cfg.db)
        recs = self._records()
        out, warns = translate.apply(cfg, conn, recs, "en")
        self.assertEqual(warns, [])
        self.assertEqual((recs[0]["title_tr"], recs[0]["description_tr"], recs[0]["translated_to"]), ("T:Sagra", "D:Musica", "en"))
        self.assertIsNone(recs[1]["description_tr"])
        events_store.upsert(conn, recs)
        self.assertEqual(len(Fake.hits), 1)
        recs2 = self._records()
        translate.apply(cfg, conn, recs2, "en")
        self.assertEqual(len(Fake.hits), 1)  # cached, no second call
        self.assertEqual(recs2[0]["title_tr"], "T:Sagra")
        conn.close()

    def test_translate_target_set_but_model_off(self):
        cfg = make_cfg(self.tmp, self.url, uses=())
        conn = connect(cfg.db)
        recs = self._records()
        out, warns = translate.apply(cfg, conn, recs, "en")
        self.assertEqual(out, "skipped")
        self.assertTrue(warns)
        self.assertNotIn("title_tr", recs[0])
        self.assertEqual(Fake.hits, [])

    def test_translate_failure_keeps_originals(self):
        cfg = make_cfg(self.tmp, self.url)
        Fake.mode = "fail"
        conn = connect(cfg.db)
        recs = self._records()
        out, warns = translate.apply(cfg, conn, recs, "en")
        self.assertTrue(warns[0].startswith("translation failed"))
        self.assertNotIn("title_tr", recs[0])

    def test_collect_without_target_never_calls(self):
        cfg = make_cfg(self.tmp, self.url, translate_to="")
        conn = connect(cfg.db)
        fetchers = {}
        events_api.collect(cfg, conn, fetchers=fetchers)
        self.assertEqual(Fake.hits, [])

    def test_old_events_table_gets_columns(self):
        conn = connect(self.tmp / "old.db")
        conn.execute("CREATE TABLE events (id INTEGER PRIMARY KEY, source TEXT, source_id TEXT, title TEXT, start TEXT)")
        events_store.ensure_schema(conn)
        cols = {r[1] for r in conn.execute("PRAGMA table_info(events)")}
        self.assertTrue({"title_tr", "description_tr", "translated_to"} <= cols)


if __name__ == "__main__":
    unittest.main()
