"""Career profile, CV extraction, scoring with reasons, CLI and web endpoints. Synthetic data only."""
from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from kokpit import __main__ as cli, config as cfgmod  # noqa: E402
from kokpit.modules.career import api, cv, profile, scoring  # noqa: E402
from kokpit.web import ThreadingHTTPServer, make_handler  # noqa: E402

PROFILE = {
    "target_roles": ["Data Analyst", "BI Developer"], "skills": ["Python", "SQL"], "must_have": ["SQL"],
    "nice_to_have": ["Power BI", "dbt"], "exclude": ["unpaid"], "locations": ["Example City"], "remote_pref": "hybrid",
    "seniority": "junior", "experience_years": 2, "languages": [{"language": "English", "level": "C1"}, {"language": "German", "level": "A2"}],
}


def _cfg(tmp: Path, jobs_on=True) -> cfgmod.Config:
    p = cfgmod.write_default(tmp / "kokpit.toml", vault=str(tmp / "v"), db=str(tmp / "k.db"))
    if jobs_on:
        p.write_text(p.read_text(encoding="utf-8").replace("jobs = false", "jobs = true"), encoding="utf-8")
    return cfgmod.load(p)


class ProfileTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.cfg = _cfg(self.tmp)

    def test_jobs_module_off_by_default(self):
        cfg = _cfg(self.tmp / "other", jobs_on=False)
        self.assertFalse(cfg.modules["jobs"])
        self.assertEqual(api.view(cfg), {"enabled": False, "profile": None})
        with self.assertRaises(PermissionError):
            api.save_profile(cfg, {})

    def test_roundtrip_and_location(self):
        saved = profile.save(self.cfg, {**PROFILE, "target_roles": "Data Analyst,  data analyst, BI Developer"})
        self.assertEqual(saved["target_roles"], ["Data Analyst", "BI Developer"])
        self.assertEqual(profile.load(self.cfg), saved)
        self.assertEqual(profile.path(self.cfg).parent.name, "data")
        self.assertNotIn(str(self.cfg.vault), str(profile.path(self.cfg)))

    def test_languages_from_text_and_validation(self):
        p = profile.normalise({"languages": "english:c1, German:A2, Klingon:zz"})
        self.assertEqual(p["languages"], [{"language": "English", "level": "C1"}, {"language": "German", "level": "A2"}, {"language": "Klingon", "level": ""}])
        for bad in ({"remote_pref": "moon"}, {"seniority": "god"}, {"experience_years": "x"}, {"experience_years": 99}):
            with self.assertRaises(ValueError):
                profile.normalise(bad)

    def test_summary_is_scrubbed(self):
        p = profile.normalise({"experience_summary": "Analyst. Mail jane@example.com or +1 555 010 0199, see https://example.com/me"})
        self.assertNotIn("@", p["experience_summary"])
        self.assertNotIn("0199", p["experience_summary"])
        self.assertNotIn("http", p["experience_summary"])

    def test_corrupt_file_gives_empty_profile(self):
        p = profile.path(self.cfg)
        p.parent.mkdir(parents=True)
        p.write_text("{not json", encoding="utf-8")
        self.assertTrue(profile.is_empty(profile.load(self.cfg)))


class CVTests(unittest.TestCase):
    TEXT = ("Jane Example, jane.example@example.com, +1 555 010 0199, https://www.linkedin.com/in/jane-example\n"
            "Experience 2019-2023: Data analyst. Built dashboards with Power BI and SQL, Python (pandas), dbt.\n"
            "Languages: English (C1), German - basic, Turkish native. Used R and Go.")

    def test_scrub_keeps_dates(self):
        out = cv.scrub(self.TEXT)
        for leak in ("@", "351", "linkedin", "http"):
            self.assertNotIn(leak, out)
        self.assertIn("2019-2023", out)

    def test_propose(self):
        prop = cv.propose(self.TEXT)
        for s in ("python", "sql", "power bi", "dbt", "pandas", "r", "go"):
            self.assertIn(s, prop["skills"])
        self.assertNotIn("java", prop["skills"])
        langs = {x["language"]: x["level"] for x in prop["languages"]}
        self.assertEqual(langs["English"], "C1")
        self.assertEqual(langs["German"], "A2")
        self.assertEqual(langs["Turkish"], "native")

    def test_extract_text_file_and_errors(self):
        tmp = Path(tempfile.mkdtemp())
        (tmp / "cv.txt").write_text(self.TEXT, encoding="utf-8")
        self.assertNotIn("@", cv.extract_text(tmp / "cv.txt"))
        (tmp / "cv.docx").write_text("x", encoding="utf-8")
        with self.assertRaises(cv.CVError):
            cv.extract_text(tmp / "cv.docx")
        with self.assertRaises(cv.CVError):
            cv.extract_text(tmp / "missing.txt")

    def test_pdf_without_pdftotext_is_a_clear_error(self):
        tmp = Path(tempfile.mkdtemp())
        (tmp / "cv.pdf").write_bytes(b"%PDF-1.4")
        with mock.patch("kokpit.modules.career.cv.shutil.which", return_value=None):
            with self.assertRaises(cv.CVError) as ctx:
                cv.extract_text(tmp / "cv.pdf")
        self.assertIn("pdftotext", str(ctx.exception))

    def test_pdf_uses_subprocess_without_shell(self):
        tmp = Path(tempfile.mkdtemp())
        (tmp / "cv.pdf").write_bytes(b"%PDF-1.4")
        done = mock.Mock(returncode=0, stdout=b"Python and SQL. mail a@b.example")
        with mock.patch("kokpit.modules.career.cv.shutil.which", return_value="/usr/bin/pdftotext"), \
                mock.patch("kokpit.modules.career.cv.subprocess.run", return_value=done) as run:
            text = cv.extract_text(tmp / "cv.pdf")
        args, kwargs = run.call_args
        self.assertIsInstance(args[0], list)
        self.assertNotIn("shell", kwargs)
        self.assertEqual(kwargs["timeout"], cv.TIMEOUT_S)
        self.assertNotIn("a@b", text)


def L(**kw):
    return {"title": "Data Analyst", "description": "", "location": "", "remote": None, **kw}


class ScoringTests(unittest.TestCase):
    def test_always_has_reasons(self):
        for prof in (PROFILE, profile.empty()):
            res = scoring.score(prof, L())
            self.assertTrue(res["reasons"])
            self.assertTrue(all(r["text"] and r["effect"] in ("plus", "minus", "neutral", "unknown") for r in res["reasons"]))

    def test_empty_profile_is_unknown_not_zero(self):
        res = scoring.score(profile.empty(), L())
        self.assertIsNone(res["score"])
        self.assertTrue(all(r["effect"] == "unknown" for r in res["reasons"]))

    def test_strong_match_scores_high_with_reasons(self):
        res = scoring.score(PROFILE, L(description="Junior friendly. SQL, Power BI and dbt. Fluent English. Hybrid.", location="Example City, EX", remote=False))
        self.assertGreaterEqual(res["score"], 85)
        kinds = {(r["kind"], r["effect"]) for r in res["reasons"]}
        for want in (("role", "plus"), ("must_have", "plus"), ("nice_to_have", "plus"), ("location", "plus"), ("language", "plus")):
            self.assertIn(want, kinds)

    def test_role_mismatch_and_missing_must_have(self):
        res = scoring.score(PROFILE, L(title="Warehouse Operator", description="Lifting boxes"))
        self.assertLess(res["score"], 30)
        texts = " ".join(r["text"] for r in res["reasons"])
        self.assertIn("must-have missing: SQL", texts)
        self.assertIn("matches none of your target roles", texts)

    def test_partial_role_match(self):
        res = scoring.score({"target_roles": ["Data Analyst"]}, L(title="Senior Analyst, Data Platform"))
        self.assertTrue(any("partly matches" in r["text"] for r in res["reasons"]))

    def test_unknown_location_is_not_zero(self):
        base = scoring.score(PROFILE, L(description="SQL"))
        loc = [r for r in base["reasons"] if r["kind"] == "location"][0]
        self.assertEqual(loc["effect"], "unknown")
        self.assertGreaterEqual(base["unknowns"], 2)
        wrong = scoring.score(PROFILE, L(description="SQL", location="Elsewhere"))
        self.assertLess(wrong["score"], base["score"])

    def test_language_requirement(self):
        res = scoring.score(PROFILE, L(description="SQL. Fluent German required (C1)."))
        lang = [r for r in res["reasons"] if r["kind"] == "language"]
        self.assertTrue(any(r["effect"] == "minus" and "German C1" in r["text"] for r in lang))
        ok = scoring.score(PROFILE, L(description="SQL. German A2 is enough."))
        self.assertTrue(any(r["effect"] == "plus" for r in ok["reasons"] if r["kind"] == "language"))
        self.assertGreater(ok["score"], res["score"])

    def test_language_missing_from_profile(self):
        res = scoring.score(PROFILE, L(description="SQL. Spanish B2 required."))
        self.assertTrue(any("Spanish is asked for but not in your profile" in r["text"] for r in res["reasons"]))

    def test_seniority_and_years(self):
        junior = scoring.score(PROFILE, L(title="Junior Data Analyst", description="SQL"))
        senior = scoring.score(PROFILE, L(title="Senior Data Analyst", description="SQL. 6+ years of experience."))
        self.assertGreater(junior["score"], senior["score"])
        texts = " ".join(r["text"] for r in senior["reasons"])
        self.assertIn("title says senior", texts)
        self.assertIn("6+ years asked for; your profile has 2", texts)
        self.assertEqual(scoring.years_required("3-5 years and at least 4 years"), 4)
        self.assertEqual(scoring.years_required("5 years of experience"), 5)
        self.assertIsNone(scoring.years_required("no numbers"))

    def test_years_unknown_in_profile(self):
        res = scoring.score({**PROFILE, "experience_years": None}, L(description="SQL. 4+ years"))
        self.assertTrue(any(r["effect"] == "unknown" and "years of experience not set" in r["text"] for r in res["reasons"]))

    def test_remote_preference(self):
        pref = {**PROFILE, "remote_pref": "remote"}
        yes = scoring.score(pref, L(description="SQL", remote=True))
        no = scoring.score(pref, L(description="SQL", remote=False))
        self.assertGreater(yes["score"], no["score"])
        self.assertTrue(any(r["kind"] == "remote" and r["effect"] == "unknown" for r in scoring.score(pref, L(description="SQL"))["reasons"]))

    def test_exclude_caps_score(self):
        res = scoring.score(PROFILE, L(description="SQL, Power BI, dbt. This is an unpaid internship."))
        self.assertLessEqual(res["score"], scoring.EXCLUDE_CAP)
        self.assertTrue(any(r["kind"] == "exclude" for r in res["reasons"]))

    def test_keyword_boundaries(self):
        res = scoring.score({"must_have": ["R"]}, L(title="Analyst", description="We use SQL and Rust"))
        self.assertTrue(any("must-have missing: R" in r["text"] for r in res["reasons"]))

    def test_score_is_deterministic(self):
        a = scoring.score(PROFILE, L(description="SQL"))
        self.assertEqual(a, scoring.score(PROFILE, L(description="SQL")))
        self.assertTrue(0 <= a["score"] <= 100)


class CLITests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.cfg = _cfg(self.tmp)

    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(["--config", str(self.cfg.path), *argv])
        return code, out.getvalue(), err.getvalue()

    def test_set_show_and_score(self):
        self.assertEqual(self.run_cli("career", "profile", "set", "target_roles", "Data Analyst, BI Developer")[0], 0)
        self.assertEqual(self.run_cli("career", "profile", "set", "languages", "English:C1")[0], 0)
        code, out, _ = self.run_cli("career", "profile", "show")
        self.assertEqual(code, 0)
        self.assertIn("BI Developer", out)
        self.assertEqual(self.run_cli("career", "profile", "set", "remote_pref", "moon")[0], 2)
        self.assertEqual(self.run_cli("career", "profile", "set", "nonsense", "x")[0], 2)
        code, out, _ = self.run_cli("career", "score", "--title", "Data Analyst", "--remote", "yes")
        self.assertEqual(code, 0)
        self.assertIn("score:", out)
        self.assertIn("role: title matches target role", out)

    def test_import_cv_proposes_then_applies(self):
        f = self.tmp / "cv.md"
        f.write_text("Python and SQL. English (C1). jane@example.com", encoding="utf-8")
        code, out, _ = self.run_cli("career", "profile", "import-cv", str(f))
        self.assertIn("nothing saved", out)
        self.assertFalse(profile.exists(self.cfg))
        self.assertNotIn("jane@", out)
        self.run_cli("career", "profile", "import-cv", str(f), "--apply")
        saved = profile.load(self.cfg)
        self.assertIn("python", saved["skills"])
        self.assertEqual(saved["languages"][0]["language"], "English")
        self.assertNotIn("jane@", profile.path(self.cfg).read_text(encoding="utf-8"))

    def test_import_cv_error(self):
        code, _, err = self.run_cli("career", "profile", "import-cv", str(self.tmp / "none.txt"))
        self.assertEqual(code, 3)
        self.assertIn("file not found", err)


class WebTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.cfg = _cfg(self.tmp)
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(self.cfg))
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()

    def call(self, path, body=None):
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", data=None if body is None else json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def test_profile_cv_score_flow(self):
        code, d = self.call("/api/career")
        self.assertEqual((code, d["enabled"], d["profile_empty"], d["profile_saved"]), (200, True, True, False))
        code, d = self.call("/api/career/cv", {"text": "Python, SQL, English C1. a@b.example"})
        self.assertIn("python", d["skills"])
        self.assertFalse(profile.exists(self.cfg), "a CV proposal must not be stored")
        code, d = self.call("/api/career/profile", PROFILE)
        self.assertEqual(code, 200)
        self.assertTrue(profile.exists(self.cfg))
        code, d = self.call("/api/career/score", {"title": "Data Analyst", "description": "SQL", "remote": "no"})
        self.assertEqual(code, 200)
        self.assertIsInstance(d["score"], int)
        self.assertTrue(d["reasons"])

    def test_bad_requests(self):
        self.assertEqual(self.call("/api/career/profile", {"remote_pref": "moon"})[0], 400)
        self.assertEqual(self.call("/api/career/cv", {"text": " "})[0], 400)
        self.assertEqual(self.call("/api/career/score", {"description": "x"})[0], 400)

    def test_module_off_blocks_writes(self):
        cfg = _cfg(self.tmp / "off", jobs_on=False)
        srv = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(cfg))
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            req = urllib.request.Request(f"http://127.0.0.1:{srv.server_address[1]}/api/career/profile", data=b"{}", headers={"Content-Type": "application/json"})
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                urllib.request.urlopen(req)
            self.assertEqual(ctx.exception.code, 403)
        finally:
            srv.shutdown()
            srv.server_close()

    def test_page_has_career_tab(self):
        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/") as r:
            html = r.read().decode()
        self.assertIn("career:{l:'Career',k:[['jb','Jobs'],['jb/market','Market'],['jb/skills','Skills']]}", html)  # Career nav group, three leaves


if __name__ == "__main__":
    unittest.main()
