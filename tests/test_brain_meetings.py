"""Brain > Meetings: question extraction, marked-section writes, conflicts, limits, config, lock, HTTP."""
import hashlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from kokpit import config as cfgmod
from kokpit.modules.brain import api as brain_api, meetings
from kokpit.web import ThreadingHTTPServer, make_handler

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 5, 4, 9, 30, tzinfo=timezone.utc)

NOTE = """# Garden timer: review with the supplier

Context paragraph that is not a question.

## Questions for the supplier

- Which valve model ships with the kit?
* Is the pump rated for outdoor use?
1. When does the warranty start?

A plain paragraph under the heading is not a question either.

## Open decisions

- Buy the extended warranty?

## Later

- Not part of the meeting.
"""

MEETING_TOML = """
[[brain.meetings]]
id = "garden-timer-review"
title = "Garden timer: review with the supplier"
source = "notes/garden-timer-review.md"
sections = ["## Questions for the supplier", "## Open decisions"]
"""


def _cfg(tmp: Path, extra: str = MEETING_TOML, write=True) -> cfgmod.Config:
    p = cfgmod.write_default(tmp / "kokpit.toml")
    text = p.read_text()
    if not write:
        text = text.replace("meetings_write = true", "meetings_write = false")
    p.write_text(text + "\n" + extra)
    cfg = cfgmod.load(p)
    brain_api.init_vault(cfg.vault)
    return cfg


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.cfg = _cfg(self.dir)
        self.src = self.cfg.vault / "notes" / "garden-timer-review.md"
        self.src.parent.mkdir(parents=True, exist_ok=True)
        self.src.write_text(NOTE, encoding="utf-8")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def body(self, answers=None, general="", rev=None):
        d = meetings.load(self.cfg, "garden-timer-review")
        return {"meeting": "garden-timer-review", "revision": rev or d["revision"],
                "answers": answers if answers is not None else [""] * d["count"], "general": general}


class Parsing(Base):
    def test_questions_two_groups_mixed_bullets(self):
        d = meetings.load(self.cfg, "garden-timer-review")
        self.assertEqual([g["title"] for g in d["groups"]], ["Questions for the supplier", "Open decisions"])
        self.assertEqual([q["q"] for q in d["groups"][0]["questions"]],
                         ["Which valve model ships with the kit?", "Is the pump rated for outdoor use?", "When does the warranty start?"])
        self.assertEqual([q["n"] for q in d["groups"][1]["questions"]], [4])
        self.assertEqual(d["count"], 4)
        self.assertEqual(d["warnings"], [])
        self.assertEqual(d["source"], "garden-timer-review.md")
        self.assertNotIn(str(self.cfg.vault), json.dumps(d))

    def test_missing_section_gives_empty_group_and_warning(self):
        self.src.write_text("# T\n\n## Questions for the supplier\n\n- One?\n", encoding="utf-8")
        d = meetings.load(self.cfg, "garden-timer-review")
        self.assertEqual(d["groups"][1]["questions"], [])
        self.assertEqual(d["warnings"], ["heading not found: ## Open decisions"])

    def test_list_meetings(self):
        lst = meetings.list_meetings(self.cfg)
        self.assertEqual(lst, [{"id": "garden-timer-review", "title": "Garden timer: review with the supplier",
                                "source": "garden-timer-review.md", "exists": True, "count": 4}])
        self.src.unlink()
        self.assertEqual(meetings.list_meetings(self.cfg)[0]["exists"], False)


class Saving(Base):
    def test_first_save_appends_section_head_byte_equal(self):
        before = self.src.read_bytes()
        r = meetings.save(self.cfg, self.body(["v1", "yes", "", "no"], "General"), now=NOW)
        after = self.src.read_bytes()
        self.assertTrue(after.startswith(before))
        self.assertEqual(r["revision"], hashlib.sha256(after).hexdigest())
        text = after.decode()
        self.assertIn("<!-- kokpit-meeting:garden-timer-review -->", text)
        self.assertIn("## Meeting notes (Kokpit)", text)
        self.assertIn("```json", text)
        self.assertTrue(text.rstrip().endswith("<!-- /kokpit-meeting:garden-timer-review -->"))
        d = meetings.load(self.cfg, "garden-timer-review")
        self.assertEqual([q["a"] for g in d["groups"] for q in g["questions"]], ["v1", "yes", "", "no"])
        self.assertEqual(d["general"], "General")
        self.assertEqual(d["updated_at"], NOW.isoformat(timespec="seconds"))
        backups = list(self.cfg.resolve("data/backups/meetings").glob("garden-timer-review-*.md"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_bytes(), before)

    def test_second_save_same_data_is_idempotent(self):
        meetings.save(self.cfg, self.body(["v1", "yes", "", "no"], "General"), now=NOW)
        one = self.src.read_bytes()
        r = meetings.save(self.cfg, self.body(["v1", "yes", "", "no"], "General"), now=datetime(2027, 1, 1, tzinfo=timezone.utc))
        self.assertEqual(self.src.read_bytes(), one)
        self.assertEqual(r["updated_at"], NOW.isoformat(timespec="seconds"))
        self.assertEqual(len(list(self.cfg.resolve("data/backups/meetings").glob("*.md"))), 1)

    def test_third_save_changes_only_the_section_tail_byte_equal(self):
        meetings.save(self.cfg, self.body(["v1", "yes", "", "no"], "General"), now=NOW)
        text = self.src.read_text(encoding="utf-8")
        tail = "\n\nText a person added after the section.\n"
        self.src.write_text(text + tail, encoding="utf-8")
        head_before = self.src.read_bytes().split(b"<!-- kokpit-meeting:garden-timer-review -->")[0]
        meetings.save(self.cfg, self.body(["v2", "yes", "", "no"], "General 2"), now=NOW)
        after = self.src.read_bytes()
        self.assertTrue(after.startswith(head_before))
        self.assertTrue(after.endswith(tail.encode()))
        self.assertEqual(after.count(b"<!-- kokpit-meeting:garden-timer-review -->"), 1)
        self.assertEqual(meetings.load(self.cfg, "garden-timer-review")["groups"][0]["questions"][0]["a"], "v2")

    def test_changed_question_text_moves_answer_to_orphans(self):
        meetings.save(self.cfg, self.body(["v1", "yes", "", "no"], ""), now=NOW)
        # edit the question line in the source list only; the saved section keeps the old text
        text = self.src.read_text(encoding="utf-8").replace("Which valve model ships with the kit?", "Which valve model is included?", 1)
        self.src.write_text(text, encoding="utf-8")
        d = meetings.load(self.cfg, "garden-timer-review")
        self.assertEqual(d["groups"][0]["questions"][0]["a"], "")
        self.assertEqual(d["orphans"], [{"q": "Which valve model ships with the kit?", "a": "v1"}])
        meetings.save(self.cfg, self.body(["new", "yes", "", "no"], ""), now=NOW)
        self.assertEqual(meetings.load(self.cfg, "garden-timer-review")["orphans"],
                         [{"q": "Which valve model ships with the kit?", "a": "v1"}])

    def test_stale_revision_conflict_leaves_file_untouched(self):
        stale = self.body(["a", "b", "c", "d"], "")
        meetings.save(self.cfg, self.body(["x", "", "", ""], ""), now=NOW)
        cur = self.src.read_bytes()
        with self.assertRaises(meetings.Conflict) as ctx:
            meetings.save(self.cfg, stale, now=NOW)
        self.assertEqual(self.src.read_bytes(), cur)
        self.assertEqual(ctx.exception.current["revision"], hashlib.sha256(cur).hexdigest())
        self.assertEqual(ctx.exception.current["groups"][0]["questions"][0]["a"], "x")

    def test_limits(self):
        cur = self.src.read_bytes()
        with self.assertRaises(meetings.TooLarge):
            meetings.save(self.cfg, self.body(["a" * 5001, "", "", ""], ""), now=NOW)
        with self.assertRaises(meetings.Invalid):
            meetings.save(self.cfg, self.body([""] * 101, ""), now=NOW)
        with self.assertRaises(meetings.Invalid):
            meetings.save(self.cfg, self.body(["bad\x07", "", "", ""], ""), now=NOW)
        with self.assertRaises(meetings.Invalid):
            meetings.save(self.cfg, self.body(["", "", "", ""], "<!-- marker -->"), now=NOW)
        with self.assertRaises(meetings.Invalid):
            meetings.save(self.cfg, self.body(["", ""], ""), now=NOW)
        with self.assertRaises(meetings.Invalid):
            meetings.save(self.cfg, self.body(rev="zz"), now=NOW)
        with self.assertRaises(meetings.UnknownMeeting):
            meetings.load(self.cfg, "nope")
        self.assertEqual(self.src.read_bytes(), cur)

    def test_too_many_questions_in_source(self):
        self.src.write_text("## Questions for the supplier\n" + "".join(f"- q{i}?\n" for i in range(101)) + "## Open decisions\n", encoding="utf-8")
        with self.assertRaises(meetings.SourceError) as ctx:
            meetings.load(self.cfg, "garden-timer-review")
        self.assertEqual(ctx.exception.code, "too_many_questions")

    def test_backups_keep_last_20(self):
        for i in range(23):
            meetings.save(self.cfg, self.body([str(i), "", "", ""], ""), now=datetime(2026, 1, 1, 0, 0, i, tzinfo=timezone.utc))
        self.assertEqual(len(list(self.cfg.resolve("data/backups/meetings").glob("garden-timer-review-*.md"))), 20)

    def test_crlf_normalised_and_unicode_kept(self):
        meetings.save(self.cfg, self.body(["line1\r\nline2", "caf\u00e9", "", ""], "g\r\n"), now=NOW)
        d = meetings.load(self.cfg, "garden-timer-review")
        self.assertEqual(d["groups"][0]["questions"][0]["a"], "line1\nline2")
        self.assertEqual(d["groups"][0]["questions"][1]["a"], "caf\u00e9")


class ConfigLoading(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_outside_vault_and_duplicates_skipped(self):
        extra = MEETING_TOML + MEETING_TOML.replace('source = "notes/garden-timer-review.md"', 'source = "../x.md"').replace(
            'id = "garden-timer-review"', 'id = "escape"') + MEETING_TOML.replace(
            'source = "notes/garden-timer-review.md"', f'source = "{(self.dir / "abs.md").as_posix()}"').replace(
            'id = "garden-timer-review"', 'id = "absolute"') + MEETING_TOML + MEETING_TOML.replace(
            'id = "garden-timer-review"', 'id = "Bad_ID"') + MEETING_TOML.replace(
            'id = "garden-timer-review"', 'id = "empty"').replace(
            'sections = ["## Questions for the supplier", "## Open decisions"]', "sections = []")
        with self.assertLogs("kokpit.config", level="WARNING") as logs:
            cfg = _cfg(self.dir, extra)
        self.assertEqual([m["id"] for m in cfg.meetings], ["garden-timer-review"])
        joined = "\n".join(logs.output)
        self.assertIn("entry 2 skipped: source must stay inside the vault", joined)
        self.assertIn("entry 3 skipped", joined)
        self.assertIn("entry 4 skipped: duplicate id garden-timer-review", joined)
        self.assertIn("entry 5 skipped: id must match", joined)
        self.assertIn("entry 6 skipped: sections", joined)
        self.assertTrue(cfg.meetings_write)

    def test_no_meetings_by_default(self):
        cfg = _cfg(self.dir, "")
        self.assertEqual(cfg.meetings, [])
        self.assertTrue(cfg.meetings_write)
        self.assertIn("[[brain.meetings]]", (self.dir / "kokpit.toml").read_text())

    def test_write_flag_false_blocks_save(self):
        cfg = _cfg(self.dir, write=False)
        self.assertFalse(cfg.meetings_write)
        src = cfg.vault / "notes" / "garden-timer-review.md"
        src.parent.mkdir(parents=True, exist_ok=True)
        src.write_text(NOTE, encoding="utf-8")
        d = meetings.load(cfg, "garden-timer-review")
        self.assertEqual(d["count"], 4)
        with self.assertRaises(PermissionError):
            meetings.save(cfg, {"meeting": "garden-timer-review", "revision": d["revision"], "answers": [""] * 4, "general": ""})


class Locking(Base):
    def test_second_save_waits_for_the_lock(self):
        lock = self.src.with_name(self.src.name + ".lock")
        lock.write_text("1")
        slept = []

        def sleep(s):
            slept.append(s)
            if len(slept) == 3:
                lock.unlink()

        r = meetings.save(self.cfg, self.body(["a", "", "", ""], ""), now=NOW, sleep=sleep)
        self.assertTrue(r["ok"])
        self.assertEqual(len(slept), 3)
        self.assertFalse(lock.exists())

    def test_lock_timeout_is_a_conflict(self):
        lock = self.src.with_name(self.src.name + ".lock")
        lock.write_text("1")
        t = [1000.0]

        def clock():
            return t[0]

        def sleep(s):
            t[0] += s

        before = self.src.read_bytes()
        with self.assertRaises(meetings.Conflict):
            meetings.save(self.cfg, self.body(["a", "", "", ""], ""), now=NOW, clock=clock, sleep=sleep)
        self.assertEqual(self.src.read_bytes(), before)
        lock.unlink()

    def test_stale_lock_is_broken(self):
        lock = self.src.with_name(self.src.name + ".lock")
        lock.write_text("1")
        far = lock.stat().st_mtime + meetings.LOCK_STALE + 5
        slept = []
        r = meetings.save(self.cfg, self.body(["a", "", "", ""], ""), now=NOW, clock=lambda: far, sleep=slept.append)
        self.assertTrue(r["ok"])
        self.assertEqual(slept, [])
        self.assertFalse(lock.exists())


class Http(Base):
    def setUp(self):
        super().setUp()
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(self.cfg))
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def tearDown(self):
        self.srv.shutdown(); self.srv.server_close()
        super().tearDown()

    def req(self, path, data=None):
        body = None if data is None else (data if isinstance(data, bytes) else json.dumps(data).encode())
        r = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", data=body,
                                   headers={"Content-Type": "application/json"} if body is not None else {})
        try:
            with urllib.request.urlopen(r) as resp:
                return resp.status, json.loads(resp.read()), resp.headers
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read()), e.headers

    def test_get_list_single_and_404(self):
        st, j, h = self.req("/api/brain/meetings")
        self.assertEqual(st, 200)
        self.assertEqual(h["Cache-Control"], "no-store")
        self.assertEqual(j["write"], True)
        self.assertEqual(j["meetings"][0]["source"], "garden-timer-review.md")
        st, j, _ = self.req("/api/brain/meetings?id=garden-timer-review")
        self.assertEqual(st, 200)
        self.assertEqual(j["count"], 4)
        self.assertNotIn(str(self.cfg.vault), json.dumps(j))
        st, j, _ = self.req("/api/brain/meetings?id=nope")
        self.assertEqual((st, j), (404, {"error": "meeting not found"}))
        self.src.unlink()
        st, j, _ = self.req("/api/brain/meetings?id=garden-timer-review")
        self.assertEqual((st, j), (500, {"error": "meeting source could not be read"}))

    def test_post_success_conflict_invalid_too_large(self):
        st, d, _ = self.req("/api/brain/meetings?id=garden-timer-review")
        b = {"meeting": "garden-timer-review", "revision": d["revision"], "answers": ["a", "b", "c", "d"], "general": "g"}
        st, j, _ = self.req("/api/brain/meetings/save", b)
        self.assertEqual(st, 200)
        self.assertTrue(j["ok"])
        self.assertTrue(re.fullmatch(r"[0-9a-f]{64}", j["revision"]))
        self.assertIn("## Meeting notes (Kokpit)", self.src.read_text(encoding="utf-8"))
        cur = self.src.read_bytes()
        st, j, _ = self.req("/api/brain/meetings/save", b)  # stale revision
        self.assertEqual(st, 409)
        self.assertEqual(j["code"], "conflict")
        self.assertEqual(j["current"]["revision"], hashlib.sha256(cur).hexdigest())
        self.assertEqual(self.src.read_bytes(), cur)
        b["revision"] = j["current"]["revision"]
        st, j, _ = self.req("/api/brain/meetings/save", dict(b, answers=["x"]))
        self.assertEqual(st, 400)
        st, j, _ = self.req("/api/brain/meetings/save", dict(b, answers=["a" * 5001, "", "", ""]))
        self.assertEqual(st, 413)
        st, j, _ = self.req("/api/brain/meetings/save", dict(b, general="g" * 251 * 1024))
        self.assertEqual(st, 413)
        st, j, _ = self.req("/api/brain/meetings/save", b"{not json")
        self.assertEqual(st, 400)
        st, j, _ = self.req("/api/brain/meetings/save", dict(b, meeting="nope"))
        self.assertEqual(st, 404)
        self.assertEqual(self.src.read_bytes(), cur)

    def test_post_read_only_403(self):
        self.cfg.meetings_write = False
        st, d, _ = self.req("/api/brain/meetings?id=garden-timer-review")
        self.assertEqual(st, 200)
        st, j, _ = self.req("/api/brain/meetings/save", {"meeting": "garden-timer-review", "revision": d["revision"],
                                                          "answers": [""] * 4, "general": ""})
        self.assertEqual(st, 403)
        st, j, _ = self.req("/api/brain/meetings")
        self.assertEqual(j["write"], False)


class Frontend(unittest.TestCase):
    def test_meetings_view_present(self):
        html = (ROOT / "kokpit/static/index.html").read_text(encoding="utf-8")
        self.assertIn("No meetings configured. Add [[brain.meetings]] to kokpit.toml.", html)
        self.assertIn("Someone else saved this note. Your text is kept in the editor; reload to see the new version.", html)
        self.assertIn("beforeunload", html)
        self.assertIn("/api/brain/meetings/save", html)
        self.assertIn("data-sub=\"meetings\"", html.replace("data-sub=\"${k}\"", "data-sub=\"meetings\""))

    def test_scripts_pass_node_check(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("node not installed")
        html = (ROOT / "kokpit/static/index.html").read_text(encoding="utf-8")
        scripts = re.findall(r"<script>(.*?)</script>", html, re.S)
        self.assertTrue(scripts)
        with tempfile.TemporaryDirectory() as d:
            for i, s in enumerate(scripts):
                p = Path(d) / f"s{i}.js"
                p.write_text(s, encoding="utf-8")
                r = subprocess.run([node, "--check", str(p)], capture_output=True, text=True)
                self.assertEqual(r.returncode, 0, r.stderr)


class Privacy(unittest.TestCase):
    # Personal data that must never appear anywhere: the live vault name, home paths, the
    # company from the live note, the network name and the companion folder number.
    PERSONAL = re.compile(r"beynim|/home/|rextaura|tailscale|850(?!\d)|cremona", re.I)
    # The new module and its tests additionally may not mention the author or the upstream
    # project name; the README/docs carry deliberate author and upstream credit lines.
    STRICT = re.compile(r"sahin|beyin", re.I)
    SHARED = ["kokpit/web.py", "kokpit/config.py", "kokpit/static/index.html", "kokpit/modules/brain/capture.py",
              "config/kokpit.example.toml", "docs/brain-protocol.md", "docs/modules.md", "docs/privacy.md",
              "README.md", "README.tr.md"]
    NEW = ["kokpit/modules/brain/meetings.py", "tests/test_brain_meetings.py"]

    def _scan(self, files, pat):
        for f in files:
            for n, line in enumerate((ROOT / f).read_text(encoding="utf-8").splitlines(), 1):
                if f == "tests/test_brain_meetings.py" and "re.compile(" in line:
                    continue
                self.assertIsNone(pat.search(line), f"{f}:{n}: {line[:80]}")

    def test_no_personal_words(self):
        self._scan(self.SHARED + self.NEW, self.PERSONAL)
        self._scan(self.NEW, self.STRICT)


if __name__ == "__main__":
    unittest.main()
