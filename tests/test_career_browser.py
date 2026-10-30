"""Career browser sources (Indeed, Jooble): pure parsing, site table, block policy, collect() wiring.

No Playwright and no network: a fake launcher returns a fake page. Companies and places are fictional.
"""
from __future__ import annotations

import contextlib
import io
import re
import sqlite3
import sys
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from kokpit import __main__ as cli, config as cfgmod  # noqa: E402
from kokpit.modules.career import browser, collect, profile  # noqa: E402
from kokpit.modules.connections import api as conn_api  # noqa: E402

TODAY = date(2026, 10, 30)
NOW = datetime(2026, 10, 30, 9, 0)
IT = browser.SITES["IT"]

INDEED_CARDS = [
    {"jk": "a1b2", "title": "Data Analyst", "company": "Example Corp", "location": "Example City", "salary": "", "age": "3 days ago", "snippet": "SQL and dashboards"},
    {"jk": "c3d4", "title": "BI Developer", "company": "Riverside Analytics", "location": "Remote", "salary": "40.000 \u20ac", "age": "Just posted", "snippet": ""},
]
JOOBLE_TEXT = "Data Analyst (m/f)\n38.000 \u20ac\nWork with data teams\nTempo pieno\nRemoto\nSegnala\nExample Corp S.p.A.\nExample City\n2 giorni fa"


class FakePage:
    """Answers evaluate() by the script constant and the last url; counts page loads."""

    def __init__(self, indeed=None, jooble=None, block=(), detail_block=(), detail_text="Full posting text " * 30):
        self.indeed = INDEED_CARDS if indeed is None else indeed
        self.jooble = [{"id": "j77", "title": "Data Analyst (m/f)", "href": "https://example.invalid/jb/j77", "text": JOOBLE_TEXT}] if jooble is None else jooble
        self.block, self.detail_block, self.detail_text = set(block), set(detail_block), detail_text
        self.urls: list[str] = []
        self.url = ""

    def goto(self, url, timeout=None, wait_until=None):
        self.urls.append(url)
        self.url = url

    def _kind(self):
        if "indeed" in self.url:
            return "indeed", "viewjob" in self.url
        return "jooble", "/jb/" in self.url

    def title(self):
        name, detail = self._kind()
        return "Just a moment..." if (name in (self.detail_block if detail else self.block)) else "Results"

    def evaluate(self, js):
        name, detail = self._kind()
        blocked = name in (self.detail_block if detail else self.block)
        if js == browser.INDEED_CARDS_JS:
            return [] if blocked else [dict(c) for c in self.indeed]
        if js == browser.JOOBLE_CARDS_JS:
            return [] if blocked else [dict(c) for c in self.jooble]
        if js == browser.INDEED_DESCRIPTION_JS:
            return "" if blocked else self.detail_text
        if js == browser.BODY_JS:
            return "Checking your browser. Just a moment" if blocked else "Menu\nData Analyst (m/f)\n" + self.detail_text
        raise AssertionError("unexpected script")


def launcher_for(page, calls=None):
    def launch(cfg, locale):
        if calls is not None:
            calls.append(locale)
        return page, lambda: None
    return launch


def _cfg(tmp: Path, sources='["indeed", "jooble"]', country="IT", city="Example City", extra: dict | None = None) -> cfgmod.Config:
    p = cfgmod.write_default(tmp / "kokpit.toml", vault=str(tmp / "vault"), db=str(tmp / "k.db"))
    t = p.read_text(encoding="utf-8").replace("jobs = false", "jobs = true", 1)
    t = t.replace('city = ""', f'city = "{city}"', 1).replace('country = ""', f'country = "{country}"', 1)
    t = t.replace("browser_sources = []", f"browser_sources = {sources}", 1)
    for k, v in (extra or {}).items():
        t, n = re.subn(rf"^{k} = [^#\n]*", f"{k} = {v} ", t, count=1, flags=re.M)
        assert n == 1, k
    p.write_text(t, encoding="utf-8")
    return cfgmod.load(p)


PROF = {"target_roles": ["Data Analyst"], "locations": ["Example City"], "remote_pref": "hybrid"}


class ParseTests(unittest.TestCase):
    def test_parse_age_en_it(self):
        cases = {"today": "2026-10-30", "Just posted": "2026-10-30", "3 days ago": "2026-10-27", "Posted 30+ days ago": "2026-09-30",
                 "1 day ago": "2026-10-29", "oggi": "2026-10-30", "Pubblicato 5 giorni fa": "2026-10-25", "2 settimane fa": "2026-10-16",
                 "1 settimana fa": "2026-10-23", "yesterday": "2026-10-29"}
        for text, want in cases.items():
            self.assertEqual(browser.parse_age(text, TODAY, "it"), want, text)
        for text in ("", None, "Employer active", "Nuovo", "recently"):
            self.assertIsNone(browser.parse_age(text, TODAY, "en"), text)

    def test_parse_indeed_cards(self):
        cards = INDEED_CARDS + [{"jk": "", "title": "No key"}, {"jk": "zz", "title": "  "}]
        recs = browser.parse_indeed_cards(cards, IT, TODAY)
        self.assertEqual([r["external_id"] for r in recs], ["a1b2", "c3d4"])
        r = recs[0]
        self.assertEqual((r["source"], r["company"], r["posted_at"], r["salary"]), ("indeed", "Example Corp", "2026-10-27", None))
        self.assertEqual(r["url"], "https://it.indeed.com/viewjob?jk=a1b2")
        self.assertEqual(recs[1]["remote"], "remote")
        self.assertEqual(recs[1]["salary"], "40.000 \u20ac")
        self.assertEqual(recs[1]["posted_at"], "2026-10-30")

    def test_parse_jooble_card(self):
        f = browser.parse_jooble_card(JOOBLE_TEXT, "Data Analyst (m/f)", TODAY, "it")
        self.assertEqual((f["company"], f["location"], f["posted_at"]), ("Example Corp S.p.A.", "Example City", "2026-10-28"))
        self.assertEqual(f["salary"], "38.000 \u20ac")
        self.assertEqual(f["tags"], ["full time", "remote"])
        self.assertEqual(f["remote"], "remote")
        self.assertIn("Work with data teams", f["snippet"])
        self.assertNotIn("Tempo pieno", f["snippet"].split("\n")[0])
        g = browser.parse_jooble_card("Data Analyst\nShort summary\nPart-time", "Data Analyst", TODAY)
        self.assertEqual((g["company"], g["location"], g["posted_at"], g["salary"]), (None, None, None, None))
        self.assertEqual(g["tags"], ["part time"])

    def test_trim_and_blocked(self):
        page = "Sign in\nRegister\nMenu stuff\nData Analyst\nWe look for a person.\n" + "x" * 300 + "\nSimilar jobs\nOther job"
        out = browser.trim_description(page, "Data Analyst")
        self.assertTrue(out.startswith("Data Analyst"))
        self.assertNotIn("Similar jobs", out)
        self.assertNotIn("Register", out)
        self.assertTrue(browser.looks_blocked("Just a moment...", ""))
        self.assertTrue(browser.looks_blocked("Indeed", "Please complete the CAPTCHA"))
        self.assertTrue(browser.looks_blocked("", "Verifica di sicurezza in corso"))
        self.assertTrue(browser.looks_blocked("Ci siamo quasi\u2026", "Ulteriore verifica richiesta\nIl tuo Ray ID per questa richiesta"))
        self.assertFalse(browser.looks_blocked("Data Analyst jobs", "120 results"))


class SiteAndConfigTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_sites(self):
        for cc in ("IT", "DE", "FR", "ES", "NL", "GB", "US", "TR"):
            self.assertIn(cc, browser.SITES)
        site, why = browser.site_for(_cfg(self.tmp, country="IT"))
        assert site is not None
        self.assertEqual((site["indeed"], site["jooble"], why), ("it.indeed.com", "it.jooble.org", None))
        self.assertEqual(browser.SITES["US"]["indeed"], "www.indeed.com")

    def test_unknown_and_empty_country(self):
        for cc, label in (("ZZ", "ZZ"), ("", "(not set)")):
            cfg = _cfg(Path(tempfile.mkdtemp(dir=self.tmp)), country=cc)
            page = FakePage()
            recs, warns, per = browser.search(cfg, PROF, NOW, launcher=launcher_for(page), sleep=lambda s: None)
            self.assertEqual(recs, [])
            self.assertEqual(warns, [f"browser: no Indeed/Jooble site known for country {label}; add it to SITES"])
            self.assertEqual(page.urls, [])

    def test_no_target_roles(self):
        page = FakePage()
        recs, warns, per = browser.search(_cfg(self.tmp), {"target_roles": []}, NOW, launcher=launcher_for(page), sleep=lambda s: None)
        self.assertEqual((recs, page.urls), ([], []))
        self.assertTrue(any("profile has no target roles" in w for w in warns))

    def test_config_bounds_and_unknown_source(self):
        cfg = _cfg(self.tmp, sources='["Indeed", "monster", "jooble"]', extra={"browser_pages": 9, "browser_delay_s": 0.1, "browser_max_fails": 0})
        c = cfg.career
        self.assertEqual((c["browser_sources"], c["browser_ignored"]), (["indeed", "jooble"], ["monster"]))
        self.assertEqual((c["browser_pages"], c["browser_delay_s"], c["browser_max_fails"]), (5, 1.0, 1))
        self.assertEqual(_cfg(Path(tempfile.mkdtemp(dir=self.tmp)), extra={"browser_pages": 0}).career["browser_pages"], 1)
        fresh = cfgmod.load(cfgmod.write_default(self.tmp / "fresh.toml"))
        self.assertEqual(fresh.career["browser_sources"], [])
        self.assertTrue(fresh.career["browser_detail"])
        self.assertEqual((fresh.career["browser_pages"], fresh.career["browser_delay_s"]), (2, 2.5))


class SearchFlowTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_full_flow_records_and_details(self):
        cfg = _cfg(self.tmp, extra={"browser_pages": 1})
        page, naps, calls = FakePage(), [], []
        recs, warns, per = browser.search(cfg, PROF, NOW, launcher=launcher_for(page, calls), sleep=naps.append)
        self.assertEqual(calls, ["it-IT"])
        self.assertEqual(warns, [])
        self.assertEqual(per, {"indeed": 2, "jooble": 1})
        self.assertEqual(len(recs), 3)
        self.assertEqual(sum("viewjob" in u for u in page.urls), 2)
        self.assertEqual(sum("/jb/" in u for u in page.urls), 1)
        self.assertTrue(all(r["description"].startswith(("Full posting", "Data Analyst")) for r in recs))
        self.assertTrue(naps and all(n == 2.5 for n in naps))
        self.assertIn("q=Data+Analyst", page.urls[0])
        self.assertIn("l=Example+City", page.urls[0])

    def test_remote_pref_adds_indeed_remote_query(self):
        cfg = _cfg(self.tmp, sources='["indeed"]', extra={"browser_pages": 1, "browser_detail": "false"})
        page = FakePage()
        browser.search(cfg, {**PROF, "remote_pref": "remote"}, NOW, launcher=launcher_for(page), sleep=lambda s: None)
        self.assertEqual(len(page.urls), 2)
        self.assertIn("q=Data+Analyst+remote", page.urls[1])
        self.assertIn("l=Italia", page.urls[1])

    def test_detail_off_never_opens_posting_pages(self):
        cfg = _cfg(self.tmp, extra={"browser_pages": 1, "browser_detail": "false"})
        page = FakePage()
        recs, warns, per = browser.search(cfg, PROF, NOW, launcher=launcher_for(page), sleep=lambda s: None)
        self.assertEqual(len(page.urls), 2)
        self.assertEqual(len(recs), 3)
        self.assertEqual(next(r for r in recs if r["external_id"] == "a1b2")["description"], "SQL and dashboards")

    def test_block_chain_stops_after_max_fails(self):
        cfg = _cfg(self.tmp, sources='["indeed"]', extra={"browser_pages": 3, "browser_max_fails": 2})
        page = FakePage(block={"indeed"})
        recs, warns, per = browser.search(cfg, PROF, NOW, launcher=launcher_for(page), sleep=lambda s: None)
        self.assertEqual(len(page.urls), 2)
        self.assertEqual((recs, per), ([], {"indeed": 0}))
        self.assertEqual(warns, ["indeed: stopped after 2 consecutive errors (Just a moment...)"])

    def test_indeed_detail_block_stops_source_jooble_falls_back(self):
        cfg = _cfg(self.tmp, extra={"browser_pages": 1})
        jobs = [{"id": f"j{i}", "title": f"Data Analyst {i}", "href": f"https://example.invalid/jb/j{i}", "text": f"Data Analyst {i}\nSegnala\nCo {i}\nExample City\noggi"} for i in range(5)]
        page = FakePage(jooble=jobs, detail_block={"indeed", "jooble"})
        recs, warns, per = browser.search(cfg, PROF, NOW, launcher=launcher_for(page), sleep=lambda s: None)
        self.assertEqual(per, {"indeed": 2, "jooble": 5})
        self.assertEqual(sum("viewjob" in u for u in page.urls), 1)
        self.assertIn("indeed: stopped, posting page blocked (Just a moment...)", warns)
        self.assertEqual(sum("/jb/" in u for u in page.urls), browser.JOOBLE_DETAIL_BLOCKS)
        self.assertTrue(any("card summaries only" in w for w in warns))

    def test_playwright_missing_is_one_warning(self):
        cfg = _cfg(self.tmp)

        def launch(cfg, locale):
            raise ImportError("No module named 'playwright'")
        recs, warns, per = browser.search(cfg, PROF, NOW, launcher=launch, sleep=lambda s: None)
        self.assertEqual(recs, [])
        self.assertEqual(warns, ["browser: playwright is not installed: pip install playwright; indeed, jooble skipped"])
        self.assertEqual(per, {"indeed": 0, "jooble": 0})

    def test_real_launch_without_playwright(self):
        with mock.patch.dict(sys.modules, {"playwright": None, "playwright.sync_api": None}):
            with self.assertRaises(browser.BrowserUnavailable) as cm:
                browser._launch(_cfg(self.tmp), "it-IT")
        self.assertEqual(str(cm.exception), "playwright is not installed: pip install playwright")

    def test_status_and_executable(self):
        cfg = _cfg(self.tmp)
        with mock.patch.object(browser, "playwright_installed", return_value=False):
            self.assertEqual(browser.status(cfg), "playwright missing")
        with mock.patch.object(browser, "playwright_installed", return_value=True), mock.patch.object(browser.shutil, "which", return_value=None):
            self.assertEqual(browser.status(cfg), "browser missing")
        with mock.patch.object(browser, "playwright_installed", return_value=True), \
                mock.patch.object(browser.shutil, "which", side_effect=lambda n: "/opt/bin/chromium-browser" if n == "chromium-browser" else None):
            self.assertEqual(browser.executable(cfg), "/opt/bin/chromium-browser")
            self.assertEqual(browser.status(cfg), "on")
        self.assertEqual(browser.status(_cfg(Path(tempfile.mkdtemp(dir=self.tmp)), sources="[]")), "off")


class CollectTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def _conn(self):
        conn = sqlite3.connect(self.tmp / "k.db")
        conn.row_factory = sqlite3.Row
        return conn

    def test_collect_end_to_end_merges_twins(self):
        cfg = _cfg(self.tmp, extra={"browser_pages": 1})
        profile.save(cfg, PROF)
        conn = self._conn()
        rows, info, warns = collect.collect(cfg, conn, sleep=lambda s: None, now=NOW, browser_launcher=launcher_for(FakePage()))
        self.assertEqual(rows, 3)
        self.assertEqual(info["sources"], {"indeed": 2, "jooble": 1})
        self.assertEqual((info["new"], info["duplicates"]), (2, 1))
        self.assertFalse(any("no source enabled" in w for w in warns))
        got = conn.execute("SELECT source, sources, posted_at FROM job_listings WHERE company LIKE 'Example Corp%'").fetchall()
        self.assertEqual(len(got), 1)
        self.assertEqual((got[0]["source"], got[0]["sources"], got[0]["posted_at"]), ("indeed", "indeed,jooble", "2026-10-27"))
        conn.close()

    def test_browser_not_called_when_off(self):
        cfg = _cfg(self.tmp, sources="[]")
        profile.save(cfg, PROF)
        conn = self._conn()
        with mock.patch.object(browser, "search", side_effect=AssertionError("called")) as m:
            rows, info, warns = collect.collect(cfg, conn, now=NOW)
        self.assertEqual(m.call_count, 0)
        self.assertEqual(rows, 0)
        self.assertTrue(any("browser_sources" in w and "no source enabled" in w for w in warns))
        conn.close()

    def test_unknown_source_warns_in_run(self):
        cfg = _cfg(self.tmp, sources='["monster"]')
        conn = self._conn()
        with mock.patch.object(browser, "search", side_effect=AssertionError("called")):
            _, _, warns = collect.collect(cfg, conn, now=NOW)
        self.assertTrue(any("unknown source 'monster'" in w for w in warns))
        conn.close()

    def test_missing_playwright_does_not_touch_other_sources(self):
        cfg = _cfg(self.tmp, sources='["indeed"]')
        profile.save(cfg, PROF)
        conn = self._conn()

        def launch(cfg, locale):
            raise browser.BrowserUnavailable("playwright is not installed: pip install playwright")
        rows, info, warns = collect.collect(cfg, conn, now=NOW, browser_launcher=launch)
        self.assertEqual(info["sources"], {"indeed": 0})
        self.assertEqual(sum("playwright is not installed" in w for w in warns), 1)
        conn.close()


class SurfaceTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_connections_rows(self):
        cfg = _cfg(self.tmp, sources='["indeed"]')
        with mock.patch.object(browser, "playwright_installed", return_value=False):
            items = {c["id"]: c for c in conn_api.view(cfg)["connections"]}
        ind, joo = items["browser-indeed"], items["browser-jooble"]
        self.assertEqual((ind["name"], ind["status"], ind["writes"], ind["target"]), ("Indeed (browser)", "playwright missing", "none", "it.indeed.com"))
        self.assertEqual((joo["name"], joo["status"], joo["enabled"]), ("Jooble (browser)", "off", False))
        self.assertIn("public search and posting pages", ind["reads"])
        self.assertEqual(ind["permission"], "none (no account)")

    def test_cli_sources_check(self):
        cfg = _cfg(self.tmp)
        profile.save(cfg, PROF)
        out = io.StringIO()
        with mock.patch.object(browser, "playwright_installed", return_value=False), contextlib.redirect_stdout(out):
            code = cli.main(["--config", str(cfg.path), "career", "sources", "--check"])
        self.assertEqual(code, 1)
        text = out.getvalue()
        self.assertIn("playwright:       missing", text)
        self.assertIn("browser sources:  indeed, jooble", text)
        self.assertIn("no network used", text)
