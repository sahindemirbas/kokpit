"""AI usage: readers (synthetic fixtures built at runtime), pricing, three numbers, limits, advice, API, Health warnings. No network."""
from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from kokpit import config as cfgmod, jobs  # noqa: E402
from kokpit.db import connect  # noqa: E402
from kokpit.modules.ai_usage import advice, api, collect, pricing, readers, store  # noqa: E402
from kokpit.modules.ai_usage.normalize import normalize_model, session_hash  # noqa: E402
from kokpit.modules.connections import api as conn_api  # noqa: E402

NOW = datetime.now(timezone.utc).replace(microsecond=0)


def ts(delta_hours=0.0) -> str:
    return (NOW - timedelta(hours=delta_hours)).strftime("%Y-%m-%dT%H:%M:%SZ")


def epoch(delta_hours=0.0) -> float:
    return (NOW - timedelta(hours=delta_hours)).timestamp()


def make_hermes(path: Path, usage: list[tuple]) -> None:
    c = sqlite3.connect(path)
    c.executescript("""CREATE TABLE sessions (id TEXT PRIMARY KEY, source TEXT, started_at REAL, last_activity_at REAL);
    CREATE TABLE session_model_usage (session_id TEXT, model TEXT, input_tokens INT, output_tokens INT, cache_read_tokens INT,
      cache_write_tokens INT, first_seen REAL, last_seen REAL);""")
    for sid, source, model, i, o, cr, cw, h in usage:
        if not c.execute("SELECT 1 FROM sessions WHERE id=?", (sid,)).fetchone():
            c.execute("INSERT INTO sessions VALUES (?,?,?,?)", (sid, source, epoch(h + 1), epoch(h)))
        c.execute("INSERT INTO session_model_usage VALUES (?,?,?,?,?,?,?,?)", (sid, model, i, o, cr, cw, epoch(h + 1), epoch(h)))
    c.commit()
    c.close()


def write_jsonl(path: Path, objs: list) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(o if isinstance(o, str) else json.dumps(o) for o in objs) + "\n", encoding="utf-8")


def claude_line(mid, model, sess, i, o, cr, cw, hours=1.0, req=None, entry="cli"):
    return {"type": "assistant", "sessionId": sess, "timestamp": ts(hours), "requestId": req or "req-" + mid, "entrypoint": entry,
            "message": {"id": mid, "model": model, "usage": {"input_tokens": i, "output_tokens": o, "cache_read_input_tokens": cr, "cache_creation_input_tokens": cw}}}


def codex_events(sid, model, totals, hours=1.0, reasoning=0):
    """totals: (input incl. cached, cached subset, output incl. reasoning). Mirrors the real structure: total_tokens = input + output."""
    ev = [{"type": "session_meta", "payload": {"id": sid, "source": "exec"}}, {"type": "turn_context", "payload": {"model": model}}]
    for k, (i, c, o) in enumerate(totals):
        ev.append({"type": "event_msg", "timestamp": ts(hours - k * 0.01), "payload": {"type": "token_count", "info": {
            "total_token_usage": {"input_tokens": i, "cached_input_tokens": c, "cache_write_input_tokens": 0, "output_tokens": o,
                                                                                                    "reasoning_output_tokens": min(reasoning, o), "total_tokens": i + o}}}})
    ev.insert(2, {"type": "event_msg", "payload": {"type": "token_count", "info": None}})
    return ev


class Base(unittest.TestCase):
    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self.tmp = Path(self._t.name)
        self.addCleanup(self._t.cleanup)

    def cfg(self, readers_=("hermes", "claude_code", "codex"), extra="", on=True):
        p = cfgmod.write_default(self.tmp / "kokpit.toml", vault=str(self.tmp / "vault"), db=str(self.tmp / "k.db"))
        text = p.read_text(encoding="utf-8")
        if on:
            text = text.replace("ai_usage = false", "ai_usage = true", 1)
        text = text.replace("readers = []", "readers = [" + ", ".join(f'"{r}"' for r in readers_) + "]", 1)
        for key, val in (("hermes_db", self.tmp / "state.db"), ("claude_dir", self.tmp / "claude"), ("codex_dir", self.tmp / "codex"),
                         ("prices_path", self.tmp / "prices.json")):
            text = text.replace(next(l for l in text.splitlines() if l.startswith(key + " =")), f'{key} = "{val}"', 1)
        p.write_text(text + extra, encoding="utf-8")
        return cfgmod.load(p)

    def run_job(self, cfg):
        conn = connect(cfg.db)
        try:
            return collect.collect(cfg, conn)
        finally:
            conn.close()


class NormalizeTests(unittest.TestCase):
    def test_model_ids(self):
        for raw, want in (("anthropic/claude-sonnet-4-5-20250929", "claude-sonnet-4-5"), ("gpt-5-high", "gpt-5"), ("Model-X-thinking", "model-x"),
                          ("vendor/some-model-2025-01-02-low", "some-model"), ("", "unknown"), (None, "unknown"), ("plain", "plain")):
            self.assertEqual(normalize_model(raw), want, raw)

    def test_hash_is_short_and_stable(self):
        h = session_hash("abc")
        self.assertEqual(len(h), 12)
        self.assertEqual(h, session_hash("abc"))
        self.assertNotIn("abc", h)


class ReaderTests(Base):
    def test_hermes(self):
        make_hermes(self.tmp / "state.db", [("s1", "cron", "vendor/m-a", 100, 50, 1000, 20, 2), ("s1", "cron", "m-b", 10, 5, 0, 0, 1),
                                            ("s2", "cli", "m-a", 0, 0, 0, 0, 1)])
        rows, meta = readers.read_hermes(self.tmp / "state.db")
        self.assertEqual({r["model_raw"] for r in rows}, {"vendor/m-a", "m-b"})  # all-zero usage row is dropped
        a = [r for r in rows if r["model_raw"] == "vendor/m-a"]
        self.assertGreaterEqual(len(a), 2)  # session totals are spread over the hours it was active
        self.assertEqual((sum(r["input_tokens"] for r in a), sum(r["cache_read_tokens"] for r in a), sum(r["cache_write_tokens"] for r in a)), (100, 1000, 20))
        self.assertTrue(all(r["approx"] == 1 and r["place"] == "cron" and len(r["session_hash"]) == 12 for r in a))
        self.assertIsNotNone(meta["source_mtime"])

    def test_hermes_opened_read_only_and_missing(self):
        make_hermes(self.tmp / "state.db", [("s1", "cli", "m", 1, 1, 0, 0, 1)])
        before = (self.tmp / "state.db").read_bytes()
        readers.read_hermes(self.tmp / "state.db")
        self.assertEqual(before, (self.tmp / "state.db").read_bytes())
        with self.assertRaises(readers.ReaderError):
            readers.read_hermes(self.tmp / "nope.db")
        (self.tmp / "bad.db").write_text("not a database")
        with self.assertRaises(readers.ReaderError):
            readers.read_hermes(self.tmp / "bad.db")

    def test_claude_dedupe(self):
        d = self.tmp / "claude" / "proj"
        write_jsonl(d / "a.jsonl", [claude_line("m1", "claude-x-20250101", "S1", 10, 4, 100, 50), claude_line("m1", "claude-x-20250101", "S1", 10, 40, 100, 50),
                                    claude_line("m2", "claude-x-20250101", "S1", 5, 5, 0, 0), "not json {", {"type": "user"},
                                    {"type": "assistant", "message": {"id": "m3", "model": "<synthetic>", "usage": {"input_tokens": 9}}}])
        write_jsonl(d / "b.jsonl", [claude_line("m1", "claude-x-20250101", "S1", 10, 40, 100, 50)])  # same message in a resumed file
        rows, meta = readers.read_claude_code(self.tmp / "claude")
        self.assertEqual(len(rows), 1)
        r = rows[0]
        self.assertEqual((r["input_tokens"], r["output_tokens"], r["cache_read_tokens"], r["cache_write_tokens"]), (15, 45, 100, 50))
        self.assertEqual(meta["messages"], 2)
        with self.assertRaises(readers.ReaderError):
            readers.read_claude_code(self.tmp / "missing")

    def test_codex_cumulative_totals(self):
        write_jsonl(self.tmp / "codex" / "2026" / "a.jsonl", codex_events("C1", "gpt-x", [(1000, 800, 10), (1000, 800, 10), (2500, 2000, 30), (3000, 2000, 35)]))
        rows, _ = readers.read_codex(self.tmp / "codex")
        self.assertEqual(len(rows), 1)
        r = rows[0]
        # final totals: input 3000 of which 2000 cached, output 35; repeated events add nothing
        self.assertEqual((r["input_tokens"], r["cache_read_tokens"], r["output_tokens"]), (1000, 2000, 35))

    def test_codex_cache_and_reasoning_split(self):
        write_jsonl(self.tmp / "codex" / "a.jsonl", codex_events("C1", "gpt-x", [(14139, 12288, 5), (20000, 15000, 405)], reasoning=300))
        r = readers.read_codex(self.tmp / "codex")[0][0]
        # input excludes the cached subset, reasoning stays inside output (not added again), no cache write
        self.assertEqual((r["input_tokens"], r["cache_read_tokens"], r["output_tokens"], r["cache_write_tokens"]), (5000, 15000, 405, 0))
        # stored tokens add up to the log's own total_tokens (20405 = input 20000 + output 405), so reasoning is not counted twice
        self.assertEqual(r["input_tokens"] + r["cache_read_tokens"] + r["output_tokens"], 20000 + 405)

    def test_codex_counter_reset_counts_new_usage(self):
        write_jsonl(self.tmp / "codex" / "a.jsonl", codex_events("C1", "gpt-x", [(1000, 0, 10), (200, 0, 5)]))
        rows, _ = readers.read_codex(self.tmp / "codex")
        self.assertEqual((rows[0]["input_tokens"], rows[0]["output_tokens"]), (1200, 15))


class PricingTests(Base):
    def test_unknown_is_none_not_zero(self):
        row = {"input_tokens": 1_000_000, "output_tokens": 0, "cache_read_tokens": 0, "cache_write_tokens": 0}
        self.assertIsNone(pricing.cost(row, None))
        self.assertIsNone(pricing.cost(row, {"input": None, "output": 1, "cache_read": 1, "cache_write": 1}))
        self.assertAlmostEqual(pricing.cost(row, {"input": 3.0, "output": 15, "cache_read": 0.3, "cache_write": 3.75}), 3.0)

    def test_local_file_wins_over_cache_and_dots_match_dashes(self):
        pp = self.tmp / "prices.json"
        pp.write_text(json.dumps({"models": {"Vendor/Model-4.5-20250101": {"input": 1, "output": 2, "cache_read": 0.1, "cache_write": 1.2}}}))
        pricing.cache_path(pp).write_text(json.dumps({"fetched_at": ts(), "models": {"model-4-5": {"input": 9, "output": 9, "cache_read": 9, "cache_write": 9}}}))
        prices = pricing.load(pp)
        self.assertEqual(pricing.lookup(prices, "model-4-5")["input"], 1)
        self.assertIsNone(pricing.lookup(prices, "other"))

    def test_refresh_is_opt_in_cached_24h(self):
        pp = self.tmp / "prices.json"
        calls = []
        body = json.dumps({"anthropic": {"models": {"m-1": {"cost": {"input": 1, "output": 2}}}}})
        get = lambda url, timeout=0: (calls.append(url), body)[1]
        self.assertEqual(pricing.refresh(pp, get=get)[0], "updated")
        self.assertEqual(pricing.refresh(pp, get=get)[0], "fresh")
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0], pricing.URL)
        self.assertEqual(pricing.refresh(pp, get=get, now=NOW + timedelta(hours=25))[0], "updated")
        p = pricing.lookup(pricing.load(pp), "m-1")
        self.assertEqual((p["input"], p["output"], p["cache_read"]), (1, 2, None))  # missing cache price stays unknown

    def test_refresh_failure_is_a_warning(self):
        def boom(url, timeout=0):
            raise OSError("offline")
        state, warn = pricing.refresh(self.tmp / "prices.json", get=boom)
        self.assertEqual(state, "failed")
        self.assertIn("offline", warn)

    def test_job_does_not_fetch_unless_opted_in(self):
        cfg = self.cfg(readers_=())
        calls = []
        conn = connect(cfg.db)
        collect.collect(cfg, conn, get=lambda *a, **k: calls.append(1) or "{}")
        self.assertEqual(calls, [])
        cfg2 = self.cfg(readers_=(), extra="\n")
        cfg2.ai_usage["price_refresh"] = True
        collect.collect(cfg2, conn, get=lambda *a, **k: calls.append(1) or "{}")
        self.assertEqual(len(calls), 1)
        conn.close()


class JobAndApiTests(Base):
    def populate(self):
        make_hermes(self.tmp / "state.db", [("h1", "cron", "prem-model", 1000, 2000, 5000, 100, 3), ("h2", "cli", "cheap", 100, 100, 0, 0, 30 * 24 + 5)])
        write_jsonl(self.tmp / "claude" / "p" / "a.jsonl", [claude_line("m1", "claude-x-20250101", "S1", 500, 500, 0, 0, hours=2)])
        write_jsonl(self.tmp / "codex" / "a.jsonl", codex_events("C1", "gpt-x", [(1000, 0, 100)], hours=4))
        (self.tmp / "prices.json").write_text(json.dumps({"models": {
            "prem-model": {"input": 10, "output": 50, "cache_read": 1, "cache_write": 12}, "cheap": {"input": 1, "output": 2, "cache_read": 0.1, "cache_write": 1},
            "claude-x": {"input": 3, "output": 15, "cache_read": 0.3, "cache_write": 3.75}}}))

    def test_module_off_by_default(self):
        cfg = cfgmod.load(cfgmod.write_default(self.tmp / "d.toml"))
        self.assertFalse(cfg.modules["ai_usage"])
        self.assertEqual(cfg.ai_usage["readers"], [])
        self.assertEqual(api.view(cfg), {"enabled": False})
        rows, info, warns = jobs.REGISTRY["ai_usage"](cfg)
        self.assertEqual(rows, 0)
        self.assertTrue(warns)

    def test_job_and_totals(self):
        self.populate()
        cfg = self.cfg()
        rows, info, warns = self.run_job(cfg)
        self.assertGreaterEqual(rows, 4)  # Hermes totals are spread over hour rows
        self.assertEqual(warns, [])
        self.assertEqual({k: v > 0 for k, v in info["readers"].items()}, {"hermes": True, "claude_code": True, "codex": True})
        self.run_job(cfg)  # idempotent: rows are replaced, not added
        v = api.view(cfg, NOW)
        self.assertEqual(v["windows"]["30d"]["sessions"], 3)  # h2 is older than 30 days
        w7 = v["windows"]["7d"]
        self.assertEqual(w7["input_tokens"], 1000 + 500 + 1000)
        self.assertEqual(w7["output_tokens"], 2000 + 500 + 100)
        # h1: 1000*10 + 2000*50 + 5000*1 + 100*12 = 116200 -> 0.1162 USD ; claude: 500*3+500*15 = 9000 -> 0.009
        self.assertAlmostEqual(w7["estimate_usd"], 0.1162 + 0.009, places=4)
        self.assertEqual(w7["unpriced_tokens"], 1100)  # gpt-x has no price: counted apart, not as zero
        self.assertEqual(w7["unpriced_models"], ["gpt-x"])
        self.assertEqual(v["by_tool"][0]["name"], "hermes")
        self.assertEqual(len(v["top_sessions"]), 3)
        self.assertTrue(all(len(s["session"]) == 12 for s in v["top_sessions"]))

    def test_unknown_partial_known_estimates(self):
        self.populate()
        self.run_job(self.cfg())
        v = api.view(self.cfg(), NOW)
        w = v["windows"]["7d"]
        self.assertEqual((w["estimate_state"], w["unpriced_models"]), ("partial", ["gpt-x"]))
        self.assertEqual(v["numbers"]["list_price_equivalent"]["state"]["7d"], "partial")
        self.assertIn("partial", api.money(w))
        codex = next(x for x in v["by_tool"] if x["name"] == "codex")
        self.assertEqual((codex["estimate_state"], codex["estimate_usd"]), ("unknown", None))
        self.assertEqual(api.money(codex), "unknown")
        claude = next(x for x in v["by_tool"] if x["name"] == "claude_code")
        self.assertEqual(claude["estimate_state"], "known")
        self.assertEqual(api.money(claude), "0.01 USD")
        self.assertTrue(all(s["estimate_state"] == "unknown" for s in v["top_sessions"] if s["tool"] == "codex"))
        # nothing priced at all: null, never 0
        (self.tmp / "prices.json").write_text(json.dumps({"models": {}}))
        v = api.view(self.cfg(), NOW)
        self.assertEqual((v["windows"]["30d"]["estimate_state"], v["windows"]["30d"]["estimate_usd"]), ("unknown", None))
        self.assertIsNone(v["numbers"]["list_price_equivalent"]["30d"])

    def test_three_numbers_stay_apart(self):
        self.populate()
        cfg = self.cfg()
        self.run_job(cfg)
        n = api.view(cfg, NOW)["numbers"]
        self.assertFalse(n["real_api_spend"]["configured"])
        self.assertIsNone(n["real_api_spend"]["total_monthly"])
        self.assertFalse(n["subscription_fee"]["configured"])
        self.assertEqual(n["list_price_equivalent"]["label"], "estimate, not a bill")
        cfg2 = self.cfg(extra='\n[ai_usage.subscriptions]\nclaude_code = 20\ncodex = 20.5\n[ai_usage.api_spend]\nhermes = 7\n')
        n2 = api.view(cfg2, NOW)["numbers"]
        self.assertEqual(n2["subscription_fee"]["total_monthly"], 40.5)
        self.assertEqual(n2["real_api_spend"]["total_monthly"], 7)
        self.assertIn("list_price_equivalent", n2)
        self.assertNotEqual(n2["list_price_equivalent"]["7d"], n2["subscription_fee"]["total_monthly"])

    def test_daily_series_and_places(self):
        self.populate()
        cfg = self.cfg()
        self.run_job(cfg)
        v = api.view(cfg, NOW)
        d = v["daily"]
        self.assertEqual(len(d), 30)
        self.assertEqual(d[-1]["date"], NOW.astimezone().strftime("%Y-%m-%d"))
        self.assertGreaterEqual(sum(x["tokens"] for x in d), v["windows"]["7d"]["tokens"])
        self.assertLessEqual(sum(x["tokens"] for x in d), sum(x["tokens"] for x in v["by_tool"]))
        empty = [x for x in d if not x["tokens"]]
        self.assertTrue(all(x["estimate_state"] == "known" and x["estimate_usd"] == 0 for x in empty))
        self.assertEqual({p["name"] for p in v["by_place"]} >= {"cron"}, True)
        # nothing priced: days with tokens report None, never 0
        (self.tmp / "prices.json").write_text(json.dumps({"models": {}}))
        d = api.view(cfg, NOW)["daily"]
        used = [x for x in d if x["tokens"]]
        self.assertTrue(used)
        self.assertTrue(all(x["estimate_usd"] is None and x["estimate_state"] == "unknown" for x in used))

    def test_daily_empty_and_missing_db(self):
        cfg = self.cfg()
        v = api.view(cfg, NOW)
        self.assertEqual(len(v["daily"]), 30)
        self.assertTrue(all(x["tokens"] == 0 for x in v["daily"]))
        self.assertEqual((v["by_place"], v["by_tool"], v["top_sessions"]), ([], [], []))

    def test_limits(self):
        self.populate()
        self.assertEqual(api.limits_view(self.cfg(), NOW)["configured"], False)
        self.assertIn("not configured", api.limits_view(self.cfg(), NOW)["message"])
        reset = (NOW + timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
        cfg = self.cfg(extra=f'\n[[ai_usage.limits]]\ntool = "codex"\nname = "5 hour"\nwindow_hours = 5\ntokens = 2000\nresets_at = "{reset}"\n'
                             '[[ai_usage.limits]]\ntool = "claude_code"\nwindow_hours = 1\ntokens = 10000\n')
        self.run_job(cfg)
        items = api.limits_view(cfg, NOW)["items"]
        codex = next(i for i in items if i["tool"] == "codex")
        self.assertEqual((codex["used_tokens"], codex["limit_tokens"], codex["percent"]), (1100, 2000, 55.0))
        self.assertEqual(codex["resets_at"], (NOW + timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%S"))
        claude = next(i for i in items if i["tool"] == "claude_code")
        self.assertEqual(claude["used_tokens"], 0)  # message is 2 h old, window 1 h
        self.assertIsNone(claude["resets_at"])

    def test_session_over_midnight_is_split(self):
        write_jsonl(self.tmp / "claude" / "p" / "a.jsonl", [
            {**claude_line("m1", "claude-x-20250101", "S1", 100, 10, 0, 0), "timestamp": "2026-03-10T23:30:00Z"},
            {**claude_line("m2", "claude-x-20250101", "S1", 200, 20, 0, 0), "timestamp": "2026-03-11T00:30:00Z"}])
        rows, _ = readers.read_claude_code(self.tmp / "claude")
        self.assertEqual(sorted(r["last_seen"][:13] for r in rows), ["2026-03-10T23", "2026-03-11T00"])
        self.assertTrue(all(r["approx"] == 0 for r in rows))
        enriched = api.enrich([{**r, "bucket": r["last_seen"], "model": normalize_model(r["model_raw"])} for r in rows], {})
        yesterday = api._in_window(enriched, "2026-03-10T00:00:00")
        today = api._in_window(enriched, "2026-03-11T00:00:00")
        self.assertEqual((api.totals(yesterday)["input_tokens"], api.totals(today)["input_tokens"]), (300, 200))
        self.assertEqual(api.totals(today)["sessions"], 1)
        self.assertFalse(api.totals(today)["approximate"])

    def test_limit_window_counts_only_events_inside(self):
        write_jsonl(self.tmp / "codex" / "a.jsonl", codex_events("C1", "gpt-x", [(1000, 0, 100)], hours=0.5))
        write_jsonl(self.tmp / "codex" / "b.jsonl", codex_events("C2", "gpt-x", [(5000, 0, 500)], hours=3.5))
        cfg = self.cfg(extra='\n[[ai_usage.limits]]\ntool = "codex"\nwindow_hours = 2\ntokens = 10000\n')
        self.run_job(cfg)
        item = api.limits_view(cfg, NOW)["items"][0]
        self.assertEqual(item["used_tokens"], 1100)  # the event 3.5 h ago is outside the 2 h window
        cfg3 = self.cfg(extra='\n[[ai_usage.limits]]\ntool = "codex"\nwindow_hours = 5\ntokens = 10000\n')
        self.assertEqual(api.limits_view(cfg3, NOW)["items"][0]["used_tokens"], 6600)

    def test_session_totals_source_is_prorated_and_approximate(self):
        make_hermes(self.tmp / "state.db", [("h1", "cli", "m", 300, 30, 0, 0, 0.5)])
        c = sqlite3.connect(self.tmp / "state.db")
        c.execute("UPDATE session_model_usage SET first_seen=?, last_seen=?", (datetime(2026, 3, 10, 22, 10, tzinfo=timezone.utc).timestamp(), datetime(2026, 3, 11, 0, 50, tzinfo=timezone.utc).timestamp()))
        c.commit()
        c.close()
        rows, _ = readers.read_hermes(self.tmp / "state.db")
        self.assertEqual(sorted(r["last_seen"][:13] for r in rows), ["2026-03-10T22", "2026-03-10T23", "2026-03-11T00"])
        self.assertEqual((sum(r["input_tokens"] for r in rows), sum(r["output_tokens"] for r in rows)), (300, 30))
        self.assertTrue(all(r["approx"] == 1 for r in rows))
        t = api.totals(api.enrich([{**r, "bucket": r["last_seen"], "model": "m"} for r in rows], {}))
        self.assertTrue(t["approximate"])

    def test_approximate_exposed_per_source_in_view(self):
        self.populate()
        cfg = self.cfg()
        self.run_job(cfg)
        v = api.view(cfg, NOW)
        self.assertEqual({r["tool"]: r["approximate"] for r in v["readers"]}, {"hermes": True, "claude_code": False, "codex": False})
        self.assertTrue(v["windows"]["7d"]["by_tool"]["hermes"]["approximate"])
        self.assertFalse(v["windows"]["7d"]["by_tool"]["codex"]["approximate"])

    def test_old_per_session_table_is_ignored_and_rebuilt(self):
        cfg = self.cfg()
        conn = connect(cfg.db)
        conn.execute("CREATE TABLE ai_usage_rows (tool TEXT, model_raw TEXT, session_hash TEXT, last_seen TEXT, input_tokens INT)")
        conn.execute("INSERT INTO ai_usage_rows VALUES ('codex', 'x', 'abc', ?, 5)", (ts(1),))
        conn.commit()
        conn.close()
        self.assertEqual(api.view(cfg, NOW)["windows"]["30d"]["sessions"], 0)  # old rows are not read
        write_jsonl(self.tmp / "codex" / "a.jsonl", codex_events("C1", "gpt-x", [(1000, 0, 100)], hours=1))
        self.run_job(cfg)
        self.assertEqual(api.view(cfg, NOW)["windows"]["30d"]["sessions"], 1)
        conn = connect(cfg.db)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM ai_usage_rows").fetchone()[0], 1)  # left alone
        conn.close()

    def test_missing_and_broken_sources_are_warnings_not_crashes(self):
        make_hermes(self.tmp / "state.db", [("h1", "cli", "cheap", 1, 1, 0, 0, 1)])
        (self.tmp / "codex").mkdir()
        write_jsonl(self.tmp / "codex" / "x.jsonl", ["garbage", "{broken"])
        cfg = self.cfg()  # claude dir does not exist
        rows, info, warns = jobs.REGISTRY["ai_usage"](cfg)
        self.assertEqual(rows, 1)
        self.assertTrue(any(w.startswith("claude_code:") for w in warns))
        self.assertTrue(any(w.startswith("codex: 0 usage rows") for w in warns))
        st = {r["tool"]: r for r in api.view(cfg, NOW)["readers"]}
        self.assertEqual(st["claude_code"]["status"], "missing")
        self.assertEqual(st["codex"]["status"], "empty")
        self.assertEqual(st["hermes"]["status"], "ok")
        # a failing reader keeps the previous rows
        (self.tmp / "state.db").unlink()
        jobs.REGISTRY["ai_usage"](cfg)
        self.assertEqual(api.view(cfg, NOW)["windows"]["30d"]["sessions"], 1)

    def test_health_run_records_warning(self):
        cfg = self.cfg(readers_=("hermes",))  # state.db does not exist
        self.assertTrue(jobs.run(cfg, "ai_usage"))
        c = sqlite3.connect(cfg.db)
        ok, warns = c.execute("SELECT ok, warnings FROM runs WHERE job='ai_usage'").fetchone()
        c.close()
        self.assertEqual(ok, 1)
        self.assertIn("hermes: database not found", warns)

    def test_http_endpoints_and_connections(self):
        self.populate()
        cfg = self.cfg()
        self.run_job(cfg)
        self.assertEqual(api.sessions_view(cfg, 30, 2, NOW)["sessions"].__len__(), 2)
        self.assertTrue(api.prices_view(cfg, NOW)["unknown"] == ["gpt-x"])
        self.assertIn("advice", api.advice_view(cfg, NOW))
        text = api.summary_text(cfg, NOW)
        self.assertIn("estimates, not bills", text)
        self.assertIn("real API spend: not configured", text)
        ids = {i["id"]: i for i in conn_api.view(cfg)["connections"]}
        self.assertTrue(ids["ai-usage-hermes"]["enabled"])
        self.assertEqual(ids["ai-usage-prices"]["status"], "off")
        self.assertNotIn(str(self.tmp), json.dumps(conn_api.view(cfg)))  # file names only, no folders
        import threading
        from kokpit import web
        from urllib.request import urlopen
        from http.server import ThreadingHTTPServer
        srv = ThreadingHTTPServer(("127.0.0.1", 0), web.make_handler(cfg))
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            base = f"http://127.0.0.1:{srv.server_address[1]}"
            for sub in ("", "/sessions?days=7&limit=2", "/advice", "/limits", "/prices"):
                self.assertTrue(json.loads(urlopen(base + "/api/ai_usage" + sub).read())["enabled"], sub)
        finally:
            srv.shutdown()
            srv.server_close()


def ctx(rows, **kw):
    base = {"fresh": 0, "cost": 0.0, "price": None, "place": "cli", "cache_read_tokens": 0, "cache_write_tokens": 0, "model": "m", "tool": "t", "session_hash": "aaaaaaaaaaaa"}
    settings = {"long_session_tokens": 1000, "cache_write_ratio": 0.5, "cache_write_min_tokens": 100, "background_places": ["cron"], "premium_output_price": 10.0, "stale_days": 3}
    return {"rows": [{**base, **r} for r in rows], "readers": kw.get("readers", []), "enabled": kw.get("enabled", []), "settings": settings, "now": NOW}


class AdviceTests(unittest.TestCase):
    def ids(self, c):
        return {a["id"]: a for a in advice.run(c)}

    def test_long_session(self):
        a = self.ids(ctx([{"session_hash": "bbbbbbbbbbbb", "fresh": 5000}, {"session_hash": "cccccccccccc", "fresh": 10}]))["long_sessions"]
        self.assertIn("bbbbbbbbbbbb", a["evidence"][0])
        self.assertEqual(len(a["evidence"]), 1)
        self.assertIn("estimate", a["estimate"])
        self.assertNotIn("long_sessions", self.ids(ctx([{"fresh": 10}])))

    def test_cache_write_heavy(self):
        self.assertIn("cache_write_heavy", self.ids(ctx([{"cache_write_tokens": 1000, "cache_read_tokens": 1000}])))
        self.assertIn("cache_write_heavy", self.ids(ctx([{"cache_write_tokens": 1000, "cache_read_tokens": 0}])))
        self.assertNotIn("cache_write_heavy", self.ids(ctx([{"cache_write_tokens": 1000, "cache_read_tokens": 100000}])))
        self.assertNotIn("cache_write_heavy", self.ids(ctx([{"cache_write_tokens": 10, "cache_read_tokens": 0}])))

    def test_background_premium(self):
        prem = {"output": 50.0}
        self.assertIn("background_premium", self.ids(ctx([{"place": "cron", "price": prem, "fresh": 10}])))
        self.assertNotIn("background_premium", self.ids(ctx([{"place": "cli", "price": prem}])))
        self.assertNotIn("background_premium", self.ids(ctx([{"place": "cron", "price": {"output": 2.0}}])))
        self.assertNotIn("background_premium", self.ids(ctx([{"place": "cron", "price": None}])))

    def test_unpriced(self):
        a = self.ids(ctx([{"cost": None, "model": "mystery", "fresh": 7}]))["unpriced_models"]
        self.assertIn("mystery", a["evidence"][0])
        self.assertNotIn("unpriced_models", self.ids(ctx([{"cost": 1.0}])))

    def test_stale_reader(self):
        old = (NOW - timedelta(days=10)).strftime("%Y-%m-%dT%H:%M:%S")
        new = (NOW - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%S")
        rd = lambda t, **k: {"tool": t, "status": "ok", "error": None, "last_data": new, **k}
        c = ctx([], enabled=["a", "b", "c", "d"], readers=[rd("a", last_data=old), rd("b"), rd("c", status="missing", error="gone")])
        ev = " ".join(self.ids(c)["stale_reader"]["evidence"])
        self.assertIn("a:", ev)
        self.assertIn("c: last run missing", ev)
        self.assertIn("d: enabled but never read", ev)
        self.assertNotIn("b:", ev)
        self.assertNotIn("stale_reader", self.ids(ctx([], enabled=["b"], readers=[rd("b")])))


if __name__ == "__main__":
    unittest.main()
