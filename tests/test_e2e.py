"""End to end: init in a temp dir, run jobs, start the server, hit every route."""
import json
import os
from datetime import date, timedelta
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from kokpit import config as cfgmod, jobs
from kokpit.modules.brain import api as brain_api
from kokpit.web import ThreadingHTTPServer, make_handler

ROOT = Path(__file__).resolve().parents[1]


class EndToEnd(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.cfg_path = cfgmod.write_default(self.dir / "kokpit.toml")
        self.cfg = cfgmod.load(self.cfg_path)
        brain_api.init_vault(self.cfg.vault)
        jobs.run(self.cfg, "heartbeat")
        jobs.run(self.cfg, "brain-check")
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(self.cfg))
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def tearDown(self):
        self.srv.shutdown(); self.srv.server_close()
        shutil.rmtree(self.dir, ignore_errors=True)

    def get(self, path):
        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}{path}") as r:
            return r.status, r.read()

    def post(self, path, payload):
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", data=json.dumps(payload).encode(),
                                     headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req) as r:
            return r.status, json.loads(r.read())

    def test_routes(self):
        st, body = self.get("/")
        self.assertEqual(st, 200); self.assertIn(b"<title>Kokpit</title>", body)
        today = json.loads(self.get("/api/today")[1])
        self.assertEqual(today["sections"]["brain"]["threads"], 1)
        self.assertEqual(today["sections"]["brain"]["open_tasks"], 2)
        self.assertTrue(today["sections"]["health"]["available"])
        self.assertEqual(today["sections"]["health"]["warnings"], [])
        self.assertIn(today["sections"]["weather"]["status"], ("needs a place", "not fetched"))
        self.assertEqual(today["sections"]["calendar"]["status"], "not connected")
        health = json.loads(self.get("/api/health")[1])
        self.assertEqual({r["job"] for r in health["runs"]}, {"heartbeat", "brain-check"})
        brain = json.loads(self.get("/api/brain")[1])
        self.assertEqual(brain["lanes"]["on_you"][0]["text"], "replace this thread with a real one")
        st, res = self.post("/api/brain/capture", {"kind": "task", "text": "from the test", "due": (date.today() + timedelta(days=3)).isoformat()})
        self.assertTrue(res["ok"])
        self.assertIn("from the test", (self.cfg.vault / "To-Do.md").read_text())
        self.assertEqual(json.loads(self.get("/api/today")[1])["sections"]["brain"]["upcoming"][0]["text"], "from the test")

    def test_tick_task_done(self):
        todo = self.cfg.vault / "To-Do.md"
        before = json.loads(self.get("/api/today")[1])["sections"]["brain"]["open_tasks"]
        first = json.loads(self.get("/api/brain")[1])["todo"][0]["text"]
        st, res = self.post("/api/brain/done", {"text": first})
        self.assertTrue(res["ok"])
        self.assertEqual(json.loads(self.get("/api/today")[1])["sections"]["brain"]["open_tasks"], before - 1)
        self.assertIn("[x] " + first, todo.read_text())
        with self.assertRaises(urllib.error.HTTPError) as cm:
            self.post("/api/brain/done", {"text": first})
        self.assertEqual(cm.exception.code, 400)

    def test_cli_init_and_jobs(self):
        d = Path(tempfile.mkdtemp())
        env = {**os.environ, "PYTHONPATH": str(ROOT)}
        out = subprocess.run([sys.executable, "-m", "kokpit", "init"], cwd=d, env=env, capture_output=True, text=True)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertTrue((d / "kokpit.toml").exists() and (d / "data/vault/Threads.md").exists())
        out = subprocess.run([sys.executable, "-m", "kokpit", "run-job", "heartbeat"], cwd=d, env=env, capture_output=True, text=True)
        self.assertEqual(out.returncode, 0, out.stderr); self.assertIn("heartbeat: ok, 1 rows", out.stdout)
        out = subprocess.run([sys.executable, "-m", "kokpit", "run-job", "nope"], cwd=d, env=env, capture_output=True, text=True)
        self.assertNotEqual(out.returncode, 0)
        shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
