"""Night organiser, proposals mode: model proposals (validated in code), plan file, To-Do pass, decisions.

Synthetic notes only. The model is never called for real: ``client.complete`` is replaced in every test that
turns the night use on, and a test with the model off fails if anything tries to call it.
"""
from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from contextlib import redirect_stdout
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from kokpit import __main__ as cli
from kokpit.db import connect
from kokpit.modules.model import client
from kokpit.modules.organiser import api, collect, rules, semantic
from kokpit.web import ThreadingHTTPServer, make_handler

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_organiser import TODAY, hashes, make  # noqa: E402

TODO = """# To-Do

## Open
- [ ] Order the relay board (due: 2026-10-20)
- [ ] Book the Italian exam (due: 2026-11-20)
- [ ] Water the tomatoes 2026-10-01
- [ ] Check the valve wiring
- [x] Old done task (due: 2026-09-01)
- [ ] Buy a soil sensor

## Waiting 2026-09-10
- [ ] Reply about the course fee
"""
PLAN_NOTE = "notes/plan.md"


def settle_times(cfg):
    """Deterministic file dates: notes/ last changed long ago, inbox/ changed on TODAY (independent of the real clock)."""
    old = datetime(2026, 9, 1, 12).timestamp()
    new = datetime(TODAY.year, TODAY.month, TODAY.day, 12).timestamp()
    for p in cfg.vault.rglob("*.md"):
        t = new if p.relative_to(cfg.vault).parts[0] == "inbox" else old
        os.utime(p, (t, t))


def model_cfg(tmp: Path):
    cfg = make(tmp)
    settle_times(cfg)
    cfg.model["enabled"] = True
    cfg.model["uses"]["night"] = True
    return cfg


def fake(answer):
    """A stand-in for client.complete that records calls; ``answer`` may be a string or an exception."""
    calls = []

    def complete(cfg, purpose, system, user, max_tokens=0):
        calls.append({"purpose": purpose, "system": system, "user": user})
        if isinstance(answer, Exception):
            raise answer
        if "suggestions" in system:  # the short summary call
            return "Short summary."
        return answer if isinstance(answer, str) else json.dumps(answer)
    complete.calls = calls
    return complete


GOOD = [
    {"kind": "categorise", "path": "inbox/loose.md", "target": "projects", "detail": "loose-idea.md",
     "text": "Move this idea next to the other projects.", "reason": "It describes a small build."},
    {"kind": "link", "path": "inbox/short.md", "target": "notes/italian.md", "detail": "",
     "text": "Link the short note to the Italian note.", "reason": "Continuation", "evidence": "Text."},
    {"kind": "conditions", "path": "notes/irrigation.md", "target": "inbox/odd.md", "detail": "relay board arrives",
     "text": "The waiting condition looks fulfilled.", "reason": "", "evidence": "Text."},
    {"kind": "enrich", "path": "inbox/odd.md", "target": "", "detail": "split-idea",
     "text": "This could be its own idea note.", "reason": "Separate topic.", "evidence": "Text."},
]


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def run_job(self, cfg, day=TODAY):
        conn = connect(cfg.db)
        try:
            return collect.collect(cfg, conn, day)
        finally:
            conn.close()

    def all_rows(self, cfg):
        conn = connect(cfg.db)
        try:
            return collect.open_proposals(conn, collect.STATUSES)
        finally:
            conn.close()

    def with_model(self, cfg, answer):
        f = fake(answer)
        p1 = mock.patch.object(client, "use_enabled", lambda c, purpose: purpose == "night" and c.model["enabled"])
        p2 = mock.patch.object(client, "complete", f)
        p1.start(); p2.start()
        self.addCleanup(p1.stop); self.addCleanup(p2.stop)
        return f


class SemanticTests(Base):
    def test_model_off_never_called(self):
        cfg = make(self.tmp)
        boom = mock.Mock(side_effect=AssertionError("model must not be called"))
        with mock.patch.object(client, "complete", boom):
            _, info, warns = self.run_job(cfg)
        boom.assert_not_called()
        self.assertEqual(warns, [])
        self.assertFalse(info["model"])
        self.assertTrue(all(r["source"] == "rule" for r in self.all_rows(cfg)))

    def test_valid_items_stored_as_model(self):
        cfg = model_cfg(self.tmp)
        f = self.with_model(cfg, GOOD)
        _, info, warns = self.run_job(cfg)
        self.assertTrue(info["model"])
        self.assertEqual(info["rejected"], 0, warns)
        model = {(r["kind"], r["path"]): r for r in self.all_rows(cfg) if r["source"] == "model"}
        self.assertEqual(set(model), {("categorise", "inbox/loose.md"), ("link", "inbox/short.md"),
                                      ("conditions", "notes/irrigation.md"), ("enrich", "inbox/odd.md")})
        self.assertEqual(model[("link", "inbox/short.md")]["reason"], "Continuation")
        self.assertEqual(model[("enrich", "inbox/odd.md")]["evidence"], "Text.")
        self.assertEqual(f.calls[0]["purpose"], "night")

    def test_input_limits_and_never_sent_files(self):
        cfg = model_cfg(self.tmp)
        (cfg.vault / "To-Do.md").write_text("# To-Do\n- [ ] SECRET-TODO-BODY\n")
        (cfg.vault / "Last-Session.md").write_text("SECRET-SESSION-BODY\n")
        (cfg.vault / "notes/long.md").write_text("# Long\n" + "x" * 9000 + "TAIL-MARKER\n")
        now = time.time()
        os.utime(cfg.vault / "notes/long.md", (now, now))
        cfg.organiser["max_chars"] = 500
        f = self.with_model(cfg, [])
        self.run_job(cfg, datetime.fromtimestamp(now).date())
        user = f.calls[0]["user"]
        for secret in ("SECRET-TODO-BODY", "SECRET-SESSION-BODY", "Where we left off", "TAIL-MARKER"):
            self.assertNotIn(secret, user)
        self.assertIn("=== notes/long.md", user)
        self.assertIn("=== inbox/loose.md", user)
        # an older note that did not change today is only in the catalogue (path | title | aliases | start)
        self.assertNotIn("=== notes/italian.md", user)
        self.assertIn("notes/italian.md | Italian | Italian lessons | # Italian Steady progress.", user)
        cfg.organiser["max_notes"] = 1
        notes = collect.scan(cfg)
        self.assertEqual([n["path"] for n in semantic.candidates(notes, datetime.fromtimestamp(now).date(), 1)], ["inbox/loose.md"])

    def test_rejections(self):
        cfg = model_cfg(self.tmp)
        bad = [
            {"kind": "move", "path": "inbox/loose.md", "text": "x"},                               # unknown kind
            {"kind": "link", "path": "inbox/nope.md", "target": "notes/italian.md", "text": "x",
             "reason": "Continuation", "evidence": "Text."},                                        # unknown path
            {"kind": "link", "path": "inbox/short.md", "target": "notes/italian.md", "text": "x",
             "reason": "Because", "evidence": "Text."},                                             # bad reason
            {"kind": "link", "path": "inbox/short.md", "target": "notes/plan.md", "text": "x",
             "reason": "Preparation", "evidence": "Not in the note at all."},                       # evidence missing
            {"kind": "categorise", "path": "inbox/loose.md", "target": "nowhere", "detail": "a.md",
             "text": "x", "reason": "r"},                                                           # unknown folder
            {"kind": "enrich", "path": "inbox/odd.md", "target": "", "detail": "split-idea", "text": "y" * 301,
             "evidence": "Text."},                                                                   # text too long
            {"kind": "conditions", "path": "inbox/short.md", "target": "inbox/odd.md", "detail": "c",
             "text": "x", "evidence": "Text."},                                                     # reverse direction (odd is older)
            {"kind": "enrich", "path": "inbox/odd.md", "detail": "split-idea", "text": "x",
             "evidence": "Text.", "extra": "no"},                                                    # unknown field
            "not an object",
        ]
        self.with_model(cfg, bad)
        odd = cfg.vault / "inbox/odd.md"
        odd.write_text("---\ntype: gadget\ndate: 2026-10-01\nsummary: Something with an unusual type.\n---\n# Odd\nText.\n")
        settle_times(cfg)
        _, info, warns = self.run_job(cfg)
        self.assertEqual(info["rejected"], len(bad))
        self.assertFalse([r for r in self.all_rows(cfg) if r["source"] == "model"])
        self.assertTrue(any(w.startswith(f"model proposals rejected: {len(bad)} (") for w in warns), warns)
        plan = json.loads(Path(info["plan"]).read_text())
        reasons = sorted(r["reason"] for r in plan["rejected"])
        self.assertEqual(reasons, sorted(["unknown-kind", "unknown-path", "bad-reason", "evidence-not-found",
                                          "unknown-folder", "text-length", "reverse-direction", "unknown-field",
                                          "not-an-object"]))

    def test_broken_json_and_errors_keep_rule_report(self):
        for answer in ("this is not json", TimeoutError("timed out")):
            tmp = Path(tempfile.mkdtemp())
            cfg = model_cfg(tmp)
            with mock.patch.object(client, "use_enabled", lambda c, p: True), \
                 mock.patch.object(client, "complete", fake(answer)):
                new, info, warns = self.run_job(cfg)
            self.assertGreater(new, 0)
            self.assertEqual(len([w for w in warns if w.startswith("model proposals skipped")]), 1, warns)
            self.assertTrue(Path(info["report"]).exists())
            self.assertTrue(all(r["source"] == "rule" for r in self.all_rows(cfg)))

    def test_store_rule_kept_over_model_with_same_unique_key(self):
        cfg = make(self.tmp)
        conn = connect(cfg.db)
        collect.ensure_schema(conn)
        base = dict(kind="link", path="a.md", target="b.md", detail="semantic")
        collect.store(conn, [dict(base, text="rule text", source="rule", reason="", evidence="")], "2026-10-30")
        collect.store(conn, [dict(base, text="model text", source="model", reason="Continuation", evidence="q")], "2026-10-30")
        row = dict(conn.execute("SELECT * FROM organiser_proposals").fetchone())
        conn.close()
        self.assertEqual((row["source"], row["text"]), ("rule", "rule text"))

    def test_connections_text_describes_night(self):
        t = client.USES["night"]
        for part in ("inbox/", "changed today", "max_notes", "first 200 characters", "Threads.md", "To-Do.md"):
            self.assertIn(part, t)


class PlanTests(Base):
    def test_plan_written_outside_vault_and_stable(self):
        cfg = model_cfg(self.tmp)
        f = self.with_model(cfg, GOOD)
        before = hashes(cfg.vault)
        _, info, _ = self.run_job(cfg)
        p = Path(info["plan"])
        self.assertEqual(p, cfg.db.parent / "organiser" / "plan-2026-10-30.json")
        self.assertFalse(str(p).startswith(str(cfg.vault)))
        first = json.loads(p.read_text())
        self.assertEqual(set(first), {"day", "generated_at", "inputs", "proposals", "rejected", "counts", "model_used"})
        self.assertTrue(first["model_used"])
        self.assertTrue(all({"path", "sha256", "chars"} == set(i) for i in first["inputs"]))
        self.assertTrue({"source", "evidence", "reason"} <= set(first["proposals"][0]))
        self.assertEqual(first["counts"]["model"], 4)
        calls = len(f.calls)
        time.sleep(1.1)
        self.run_job(cfg)
        second = json.loads(p.read_text())
        self.assertEqual(second["proposals"], first["proposals"])
        self.assertEqual(second["inputs"], first["inputs"])
        self.assertNotEqual(second["generated_at"], first["generated_at"])
        self.assertEqual(len(f.calls) - calls, 1)  # proposals reused; only the short summary is asked again
        self.assertEqual(hashes(cfg.vault), before)

    def test_old_plans_removed(self):
        cfg = make(self.tmp)
        d = cfg.db.parent / "organiser"
        d.mkdir(parents=True)
        (d / "plan-2026-09-01.json").write_text("{}")
        (d / "plan-2026-10-15.json").write_text("{}")
        (d / "notes.txt").write_text("keep")
        self.run_job(cfg)
        self.assertEqual(sorted(x.name for x in d.iterdir()), ["notes.txt", "plan-2026-10-15.json", "plan-2026-10-30.json"])

    def test_dry_run_writes_nothing(self):
        cfg = make(self.tmp)
        (cfg.vault / "To-Do.md").write_text(TODO)
        before = hashes(cfg.vault)
        out = collect.dry_run(cfg, TODAY)
        self.assertFalse(cfg.db.exists())
        self.assertFalse((cfg.db.parent / "organiser").exists())
        self.assertFalse(collect.report_path(cfg).exists())
        self.assertEqual(hashes(cfg.vault), before)
        lines = out.splitlines()
        self.assertTrue(lines[0].startswith("organiser dry run 2026-10-30: "))
        self.assertIn("nothing written", lines[0])
        self.assertEqual(len([x for x in lines if x[:1].isdigit()]), 10)

    def test_cli_dry_run_flag(self):
        cfg = make(self.tmp)
        buf = io.StringIO()
        with mock.patch.object(cli, "_load", lambda a: cfg), redirect_stdout(buf):
            rc = cli.cmd_run_job(SimpleNamespace(name="organiser", dry_run=True, config=None))
        self.assertEqual(rc, 0)
        self.assertIn("organiser dry run", buf.getvalue())
        self.assertFalse(cfg.db.exists())
        with mock.patch.object(cli, "_load", lambda a: cfg), self.assertRaises(SystemExit):
            cli.cmd_run_job(SimpleNamespace(name="health", dry_run=True, config=None))


class TodoTests(Base):
    def test_three_cases_and_done_excluded(self):
        notes = [{"path": "notes/garden.md", "body": "- [ ] Buy a soil sensor\n- [x] Water the tomatoes 2026-10-01\n"}]
        out = rules.todo(TODO, notes, TODAY, 14)
        det = sorted(p["detail"] for p in out)
        self.assertEqual(det, sorted([
            "overdue:order the relay board",
            "undated-stale:water the tomatoes 2026 10 01",
            "undated-stale:reply about the course fee",
            "duplicate-task:buy a soil sensor",
        ]))
        self.assertFalse([p for p in out if "old done task" in p["detail"]])
        self.assertFalse([p for p in out if "check the valve wiring" in p["detail"]])  # no date anywhere: skipped
        self.assertFalse([p for p in out if "italian exam" in p["detail"]])            # due in the future
        dup = [p for p in out if p["detail"].startswith("duplicate-task")][0]
        self.assertEqual((dup["path"], dup["target"]), ("To-Do.md", "notes/garden.md"))
        for p in out:
            self.assertEqual(p["kind"], "todo")
            self.assertNotIn("\u2014", p["text"])

    def test_due_today_not_overdue_and_threshold(self):
        t = "- [ ] Pay the bill (due: 2026-10-30)\n## 2026-10-17\n- [ ] Thirteen days\n## 2026-10-16\n- [ ] Fourteen days\n"
        self.assertEqual([p["detail"] for p in rules.todo(t, [], TODAY, 14)], ["undated-stale:fourteen days"])

    def test_todo_in_job_morning_and_report(self):
        cfg = make(self.tmp)
        (cfg.vault / "To-Do.md").write_text(TODO)
        _, info, _ = self.run_job(cfg)
        self.assertGreater(info["counts"]["todo"], 0)
        rows = [r for r in self.all_rows(cfg) if r["kind"] == "todo"]
        self.assertIn("todo", collect.morning_text(rows))
        rep = collect.report_text(rows, "2026-10-30")
        self.assertIn(f"## todo ({len(rows)})", rep)
        self.assertIn("todo", collect.KINDS)


class DecisionTests(Base):
    def setUp(self):
        super().setUp()
        self.cfg = make(self.tmp)
        self.run_job(self.cfg)
        self.ids = [r["id"] for r in self.all_rows(self.cfg)]

    def test_dismissed_and_done_stay_closed(self):
        cfg = self.cfg
        a, b = self.ids[:2]
        self.assertEqual(api.decide(cfg, {"id": a, "status": "dismissed"})["status"], "dismissed")
        self.assertEqual(api.decide(cfg, {"id": str(b), "status": "done"})["status"], "done")
        before = hashes(cfg.vault)
        self.run_job(cfg, date(2026, 10, 31))
        st = {r["id"]: r for r in self.all_rows(cfg)}
        self.assertEqual(st[a]["status"], "dismissed")
        self.assertEqual(st[b]["status"], "done")
        self.assertEqual(st[a]["last_seen"], "2026-10-31")
        self.assertEqual(hashes(cfg.vault), before)
        v = api.view(cfg)
        self.assertEqual(v["total"], len(self.ids) - 2)
        self.assertNotIn(a, [p["id"] for p in v["proposals"]])
        self.assertIn(f"Organiser: {len(self.ids) - 2} open", api.morning(cfg))
        shown = api.view(cfg, show_dismissed=True)
        self.assertIn(a, [p["id"] for p in shown["proposals"]])
        self.assertNotIn(b, [p["id"] for p in shown["proposals"]])
        self.assertEqual(shown["dismissed"], 1)
        self.assertEqual(api.decide(cfg, {"id": a, "status": "open"})["status"], "open")

    def test_gone_reopens(self):
        cfg = self.cfg
        loose = [r for r in self.all_rows(cfg) if r["path"] == "inbox/loose.md"]
        text = (cfg.vault / "inbox/loose.md").read_text()
        (cfg.vault / "inbox/loose.md").unlink()
        self.run_job(cfg, date(2026, 10, 31))
        self.assertTrue(all(r["status"] == "gone" for r in self.all_rows(cfg) if r["path"] == "inbox/loose.md"))
        (cfg.vault / "inbox/loose.md").write_text(text)
        self.run_job(cfg, date(2026, 11, 1))
        self.assertTrue(loose and all(r["status"] == "open" for r in self.all_rows(cfg) if r["path"] == "inbox/loose.md"))

    def test_bad_input(self):
        for data in ({"id": self.ids[0], "status": "gone"}, {"id": self.ids[0], "status": "deleted"},
                     {"id": "x", "status": "done"}, {"id": None, "status": "done"}, {"id": True, "status": "done"}):
            with self.assertRaises(ValueError):
                api.decide(self.cfg, data)
        with self.assertRaises(LookupError):
            api.decide(self.cfg, {"id": 99999, "status": "done"})


class DecisionHttpTests(DecisionTests):
    def setUp(self):
        super().setUp()
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(self.cfg))
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def tearDown(self):
        self.srv.shutdown(); self.srv.server_close()

    def post(self, data, headers=None):
        h = {"Content-Type": "application/json", "X-Kokpit": "1"} if headers is None else headers
        r = urllib.request.Request(f"http://127.0.0.1:{self.port}/api/organiser/decide", data=json.dumps(data).encode(), headers=h)
        try:
            with urllib.request.urlopen(r) as resp:
                return resp.status, json.loads(resp.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def test_http_200_400_404_and_guard(self):
        i = self.ids[0]
        st, j = self.post({"id": i, "status": "dismissed"})
        self.assertEqual((st, j["id"], j["status"]), (200, i, "dismissed"))
        self.assertEqual(self.post({"id": i, "status": "gone"})[0], 400)
        self.assertEqual(self.post({"id": 99999, "status": "done"})[0], 404)
        self.assertEqual(self.post({"id": i, "status": "done"}, {"Content-Type": "application/json"})[0], 403)
        self.assertEqual(self.post({"id": i, "status": "done"}, {"Content-Type": "text/plain", "X-Kokpit": "1"})[0], 403)
        self.assertEqual(self.post({"id": i, "status": "done"}, {"Content-Type": "application/x-www-form-urlencoded"})[0], 403)
        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/api/organiser?dismissed=1") as r:
            d = json.loads(r.read())
        self.assertIn(i, [p["id"] for p in d["proposals"] if p["status"] == "dismissed"])
        self.cfg.modules["organiser"] = False
        self.assertEqual(self.post({"id": i, "status": "done"})[0], 403)


class SchemaTests(Base):
    def test_old_table_gains_columns(self):
        cfg = make(self.tmp)
        cfg.db.parent.mkdir(parents=True)
        conn = connect(cfg.db)
        conn.executescript("""CREATE TABLE organiser_proposals (id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL,
            path TEXT NOT NULL, target TEXT NOT NULL DEFAULT '', detail TEXT NOT NULL DEFAULT '', text TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'open', first_seen TEXT NOT NULL, last_seen TEXT NOT NULL, UNIQUE(kind, path, target, detail));
            INSERT INTO organiser_proposals(kind,path,text,first_seen,last_seen) VALUES('enrich','inbox/loose.md','old','2026-10-01','2026-10-01');""")
        conn.commit()
        collect.ensure_schema(conn)
        cols = {r[1] for r in conn.execute("PRAGMA table_info(organiser_proposals)")}
        conn.close()
        self.assertTrue({"source", "reason", "evidence"} <= cols)
        self.run_job(cfg)
        self.assertTrue(all(r["source"] == "rule" for r in self.all_rows(cfg)))


if __name__ == "__main__":
    unittest.main()
