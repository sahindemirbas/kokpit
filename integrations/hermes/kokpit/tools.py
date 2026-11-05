"""Tool handlers for the kokpit toolset. Every handler returns a JSON string.

Shape: {"ok": true, "mode": "live"|"demo", ...} or {"ok": false, "error": ..., "fix": ...}.
Missing data is said out loud ("never run", "unknown", "no database yet"), never filled in.
Only kokpit_capture writes, and only inside the vault.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from . import contract as c

NO_DB = "no database yet: run a Kokpit job first (python -m kokpit run-job <name>)"
VAULT_FIX = "create the vault ([brain] vault_path in kokpit.toml) or run `python -m kokpit init`"


def _dump(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, default=str)


def _int(v, default: int, lo: int, hi: int) -> int:
    try:
        n = int(v)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, n))


def _need(state: dict, module: str) -> None:
    if not state["cfg"]["modules"].get(module, False):
        raise c.disabled(module)


def _vault(state: dict) -> Path:
    v = state["vault"]
    if v is None or not Path(v).is_dir():
        raise c.NotReady("vault not found", f"{v} does not exist; " + VAULT_FIX)
    return Path(v)


def _layout(state: dict) -> dict:
    """The resolved vault layout (settings, root, discovery); resolved here when a caller built state by hand."""
    lay = state.get("layout")
    if lay is None:
        lay = state["layout"] = c.resolve_layout(_vault(state))
    return lay


def _db(state: dict):
    """Read-only connection or None (no database yet)."""
    return c.open_db(state["db"])


# ---------------------------------------------------------------- shared readers

def freshness(conn, job: str) -> dict:
    """Same shape as kokpit/modules/health/store.py freshness()."""
    if conn is None or not c.has_table(conn, "runs"):
        return {"job": job, "last_status": "never run", "last_success": None, "records": None}
    last = conn.execute("SELECT ok, finished, info FROM runs WHERE job=? ORDER BY id DESC LIMIT 1", (job,)).fetchone()
    if not last:
        return {"job": job, "last_status": "never run", "last_success": None, "records": None}
    good = conn.execute("SELECT finished, rows FROM runs WHERE job=? AND ok=1 ORDER BY id DESC LIMIT 1", (job,)).fetchone()
    out = {"job": job, "last_status": "ok" if last["ok"] else "error", "last_run": last["finished"],
           "last_success": good["finished"] if good else None, "records": good["rows"] if good else None}
    if not last["ok"]:
        out["error"] = str((_json(last["info"], {}) or {}).get("error", ""))[:160]
    return out


def _json(text, default):
    try:
        v = json.loads(text) if text else default
    except (TypeError, ValueError):
        return default
    return v if v is not None else default


def _latest_payload(conn, table: str, where: str = "") -> dict | None:
    if conn is None or not c.has_table(conn, table):
        return None
    r = conn.execute(f"SELECT fetched, payload FROM {table} {where} ORDER BY id DESC LIMIT 1").fetchone()
    if not r:
        return None
    out = _json(r["payload"], {})
    out["fetched"] = r["fetched"]
    try:
        age = (datetime.now() - datetime.fromisoformat(r["fetched"])).total_seconds() / 3600
        out["age_hours"], out["stale"] = round(age, 1), age > 24
    except (TypeError, ValueError):
        out["age_hours"], out["stale"] = None, None
    return out


# ---------------------------------------------------------------- tools

def today(state: dict, args: dict) -> dict:
    _need(state, "today")
    cfg, conn = state["cfg"], _db(state)
    out: dict[str, Any] = {"date": date.today().isoformat()}
    try:
        cal = _latest_payload(conn, "calendar", "WHERE ok=1")
        if cal is None:
            out["calendar"] = {"status": "never run", "events": []}
        else:
            start = date.today().isoformat()
            out["calendar"] = {"status": "stale" if cal["stale"] else "ok", "fetched": cal["fetched"],
                               "events": [e for e in cal.get("events", []) if str(e.get("date", "")) >= start][:12]}
        out["calendar"]["freshness"] = freshness(conn, "calendar")
        w = _latest_payload(conn, "weather")
        out["weather"] = ({"status": "never run"} if w is None else
                          {"status": "stale" if w["stale"] else "ok", "fetched": w["fetched"],
                           "current": w.get("current"), "days": w.get("days", [])[:3]})
        out["weather"]["freshness"] = freshness(conn, "weather")
        if cfg["modules"].get("brain", True) and state["vault"] is not None and Path(state["vault"]).is_dir():
            v = c.read_vault(state["vault"], layout=_layout(state))
            out["tasks"] = {"open": len(v["todo"]), "overdue": sum(1 for t in v["todo"] if t["overdue"]),
                            "next": v["todo"][:6]}
            out["upcoming"] = v["upcoming"][:8]
            out["threads"] = len(v["threads"])
        else:
            out["tasks"] = {"status": "unknown", "reason": "brain module off or vault not found"}
            out["upcoming"] = []
        counts: dict = {}
        if cfg["modules"].get("events"):
            counts["events_next_7_days"] = _count_events(conn, 7)
        if cfg["modules"].get("jobs"):
            counts["open_job_listings"] = _count(conn, "job_listings", "status='open'")
        if cfg["modules"].get("health", True):
            h = health(state, {}, conn=conn)
            counts["jobs_with_problems"] = len(h["problems"]) if h.get("db") != "missing" else "unknown"
        out["counts"] = counts
    finally:
        if conn is not None:
            conn.close()
    return out


def _count(conn, table: str, where: str = "1=1"):
    if conn is None or not c.has_table(conn, table):
        return "unknown"
    return conn.execute(f"SELECT COUNT(*) FROM {table} WHERE {where}").fetchone()[0]


def _count_events(conn, days: int):
    if conn is None or not c.has_table(conn, "events"):
        return "unknown"
    lo, hi = date.today().isoformat(), (date.today() + timedelta(days=days)).isoformat()
    return conn.execute("SELECT COUNT(*) FROM events WHERE substr(start,1,10) >= ? AND substr(start,1,10) <= ?",
                        (lo, hi)).fetchone()[0]


def brain_search(state: dict, args: dict) -> dict:
    _need(state, "brain")
    query = str(args.get("query") or "").strip()
    if not query:
        raise c.NotReady("query is empty", "pass query: the words to look for")
    vault = _vault(state)
    hits = c.search(vault, query, _int(args.get("limit"), 5, 1, 20), [_layout(state)["inbox"]["path"]])
    return {"query": query, "results": hits, "count": len(hits),
            **({} if hits else {"note": "no note matched every word"})}


def threads(state: dict, args: dict) -> dict:
    _need(state, "brain")
    vault, lay = _vault(state), _layout(state)
    err = c.ambiguous(lay, "threads")
    if err:
        raise err
    v = c.read_vault(vault, layout=lay)
    src = c.rel(vault, lay["threads"]["path"])
    note = {} if v["threads"] else {"note": f"{src} has no active threads" if lay["threads"]["found"]
                                    else f"{src} not found; set plugins.entries.kokpit.settings.threads_file"}
    return {"threads": v["threads"], "on_you": [{"thread": t["title"], **o} for t in v["threads"] for o in t["open"] if o["on_you"]],
            "last_session": v["sessions"][0] if v["sessions"] else None, "source": src, **note}


def capture(state: dict, args: dict) -> dict:
    _need(state, "brain")
    if not state["cfg"].get("quick_capture", True):
        raise c.NotReady("quick capture is off", "enable with [brain] quick_capture = true in kokpit.toml")
    vault, lay = _vault(state), _layout(state)
    kind = str(args.get("kind") or "note").strip().lower()
    text = str(args.get("text") or "")
    try:
        if kind == "task":
            err = c.ambiguous(lay, "todo")
            if err:
                raise err
            line = c.capture_task(vault, text, str(args.get("due") or "").strip(), path=lay["todo"]["path"])
            return {"written": c.rel(vault, lay["todo"]["path"]), "kind": "task", "line": line}
        if kind != "note":
            raise ValueError("kind must be note or task")
        p = c.capture_note(vault, text, inbox=lay["inbox"]["path"])
    except ValueError as e:
        raise c.NotReady(str(e), "shorten or fix the text and try again")
    return {"written": c.rel(vault, p), "kind": "note"}


def events(state: dict, args: dict) -> dict:
    _need(state, "events")
    days, limit = _int(args.get("days"), 7, 1, 60), _int(args.get("limit"), 10, 1, 50)
    conn = _db(state)
    try:
        if conn is None:
            return {"status": "never run", "reason": NO_DB, "events": [], "freshness": freshness(None, "events-collect")}
        fr = freshness(conn, "events-collect")
        if not c.has_table(conn, "events"):
            return {"status": "never run", "events": [], "freshness": fr}
        lo, hi = date.today().isoformat(), (date.today() + timedelta(days=days)).isoformat()
        rs = c.rows(conn, "SELECT title, start, end, venue, city, price, category, distance_km, url FROM events "
                          "WHERE substr(start,1,10) >= ? AND substr(start,1,10) <= ? ORDER BY start LIMIT ?", (lo, hi, limit))
        for r in rs:
            for k in ("price", "distance_km", "venue"):
                if r.get(k) in (None, ""):
                    r[k] = "unknown"
        return {"days": days, "events": rs, "count": len(rs), "freshness": fr,
                **({} if rs else {"note": f"no stored events in the next {days} days"})}
    finally:
        if conn is not None:
            conn.close()


def jobs(state: dict, args: dict) -> dict:
    _need(state, "jobs")
    limit = _int(args.get("limit"), 10, 1, 50)
    min_score = args.get("min_score")
    conn = _db(state)
    try:
        if conn is None:
            return {"status": "never run", "reason": NO_DB, "listings": [], "freshness": freshness(None, "career")}
        fr = freshness(conn, "career")
        if not c.has_table(conn, "job_listings"):
            return {"status": "never run", "listings": [], "freshness": fr}
        sql = ("SELECT title, company, location, remote, url, posted_at, score, reasons FROM job_listings "
               "WHERE status='open'")
        params: list = []
        if min_score is not None:
            sql += " AND score >= ?"
            params.append(_int(min_score, 0, 0, 100))
        sql += " ORDER BY score IS NULL, score DESC, last_seen DESC LIMIT ?"
        params.append(limit)
        out = []
        for r in c.rows(conn, sql, params):
            rs = _json(r.pop("reasons"), {})
            rs = rs if isinstance(rs, dict) else {"reasons": rs if isinstance(rs, list) else []}
            r["score"] = "unknown" if r["score"] is None else r["score"]
            r["reasons"] = [f"{x.get('effect', '')} {x.get('kind', '')}: {x.get('text', '')}".strip()
                            for x in rs.get("reasons") or [] if isinstance(x, dict)]
            r["unknowns"] = rs.get("unknowns") or []
            if r["score"] == "unknown" and not r["reasons"]:
                r["reasons"] = ["not enough information to score"]
            out.append(r)
        return {"listings": out, "count": len(out), "open_total": _count(conn, "job_listings", "status='open'"),
                "freshness": fr}
    finally:
        if conn is not None:
            conn.close()


def _norm(model: str) -> str:
    return str(model or "").strip().lower().replace(".", "-")


def load_prices(path) -> dict:
    """{model: {input, output, cache_read, cache_write}} from [ai_usage] prices_path; missing file -> {}."""
    if not path:
        return {}
    data = _json(c.read(Path(path)), {})
    models = data.get("models") if isinstance(data, dict) else None
    out = {}
    for k, v in (models or {}).items():
        if isinstance(v, dict):
            out[_norm(k)] = {key: (float(v[key]) if isinstance(v.get(key), (int, float)) and not isinstance(v.get(key), bool)
                                   and v[key] >= 0 else None) for key in ("input", "output", "cache_read", "cache_write")}
    return out


def cost(row: dict, price: dict | None):
    """USD list-price equivalent, or None when any needed price is unknown (never 0 for unknown)."""
    if price is None:
        return None
    total = 0.0
    for tok, key in (("input_tokens", "input"), ("output_tokens", "output"),
                     ("cache_read_tokens", "cache_read"), ("cache_write_tokens", "cache_write")):
        n = row.get(tok) or 0
        if not n:
            continue
        if price.get(key) is None:
            return None
        total += n * price[key] / 1_000_000
    return total


def ai_usage(state: dict, args: dict) -> dict:
    _need(state, "ai_usage")
    days = _int(args.get("days"), 7, 1, 90)
    conn = _db(state)
    try:
        if conn is None:
            return {"status": "never run", "reason": NO_DB, "rows": [], "freshness": freshness(None, "ai_usage")}
        fr = freshness(conn, "ai_usage")
        if not c.has_table(conn, "ai_usage_hours"):
            return {"status": "never run", "rows": [], "freshness": fr}
        since = (date.today() - timedelta(days=days - 1)).isoformat()
        rs = c.rows(conn, "SELECT tool, model, SUM(input_tokens) AS input_tokens, SUM(output_tokens) AS output_tokens, "
                          "SUM(cache_read_tokens) AS cache_read_tokens, SUM(cache_write_tokens) AS cache_write_tokens, "
                          "COUNT(DISTINCT session_hash) AS sessions FROM ai_usage_hours "
                          "WHERE substr(COALESCE(NULLIF(bucket,''), last_seen),1,10) >= ? GROUP BY tool, model "
                          "ORDER BY tool, model", (since,))
        prices = load_prices(state["cfg"].get("prices"))
        known, unpriced = 0.0, []
        for r in rs:
            v = cost(r, prices.get(_norm(r["model"])))
            r["cost_usd"] = "unknown" if v is None else round(v, 4)
            if v is None:
                unpriced.append(r["model"])
            else:
                known += v
        readers = c.rows(conn, "SELECT tool, status, last_run, rows, error FROM ai_usage_readers ORDER BY tool") \
            if c.has_table(conn, "ai_usage_readers") else []
        return {"days": days, "since": since, "rows": rs,
                "total_cost_usd": round(known, 4) if not unpriced else "unknown",
                "known_cost_usd": round(known, 4), "unpriced_models": sorted(set(unpriced)),
                "readers": readers, "freshness": fr,
                **({} if rs else {"note": f"no usage rows in the last {days} days"})}
    finally:
        if conn is not None:
            conn.close()


def _vault_files(state: dict) -> dict:
    """Which vault files the plugin uses: path, found, and how (setting, root, discovered, ambiguous, default)."""
    v = state.get("vault")
    if v is None or not Path(v).is_dir():
        return {"status": "vault not found", "vault": str(v) if v else ""}
    return c.layout_report(Path(v), _layout(state))


def health(state: dict, args: dict, conn=None) -> dict:
    _need(state, "health")
    own = conn is None
    conn = _db(state) if own else conn
    expected = state["cfg"].get("expected") or []
    vault_files = _vault_files(state)
    vault_problems = [f"vault: {r['fix']}" for r in vault_files.values() if isinstance(r, dict) and r.get("fix")]
    try:
        if conn is None or not c.has_table(conn, "runs"):
            return {"db": "missing" if conn is None else "empty", "reason": NO_DB, "jobs": [],
                    "problems": [f"{j}: never run" for j in expected] + vault_problems, "expected": expected,
                    "vault_files": vault_files}
        jobs_ = []
        for r in c.rows(conn, "SELECT r.job, r.day, r.finished, r.ok, r.rows, r.info, r.warnings FROM runs r JOIN "
                              "(SELECT job, MAX(id) AS id FROM runs GROUP BY job) m ON r.id = m.id ORDER BY r.job"):
            j = {"job": r["job"], "last_run": r["finished"], "status": "ok" if r["ok"] else "failed",
                 "records": r["rows"], "warnings": _json(r["warnings"], [])}
            if not r["ok"]:
                j["error"] = str((_json(r["info"], {}) or {}).get("error", ""))[:200]
            streak = 0
            for (ok,) in conn.execute("SELECT ok FROM runs WHERE job=? ORDER BY id DESC", (r["job"],)):
                if ok:
                    break
                streak += 1
            j["failed_in_a_row"] = streak
            jobs_.append(j)
        seen = {j["job"] for j in jobs_}
        problems = [f"{j}: never run" for j in expected if j not in seen]
        for j in jobs_:
            if j["status"] == "failed":
                problems.append(f"{j['job']}: failed" + (f" {j['failed_in_a_row']} times in a row" if j["failed_in_a_row"] > 1 else "")
                                + (f" ({j['error']})" if j.get("error") else ""))
            elif j["records"] == 0:
                problems.append(f"{j['job']}: last run produced 0 records")
            problems += [f"{j['job']}: {w}" for w in j["warnings"]]
        problems += vault_problems
        return {"db": "ok", "jobs": jobs_, "problems": problems, "expected": expected,
                "never_run": [j for j in expected if j not in seen], "vault_files": vault_files}
    finally:
        if own and conn is not None:
            conn.close()


TOOLS = {"kokpit_today": today, "kokpit_brain_search": brain_search, "kokpit_threads": threads,
         "kokpit_capture": capture, "kokpit_events": events, "kokpit_jobs": jobs,
         "kokpit_ai_usage": ai_usage, "kokpit_health": health}


def run(env: c.Env, name: str, args) -> dict:
    args = args if isinstance(args, dict) else {}
    try:
        state = env.state()
        body = TOOLS[name](state, args)
    except c.NotReady as e:
        return {"ok": False, "error": e.error, "fix": e.fix}
    except sqlite3.Error as e:
        return {"ok": False, "error": f"database could not be read: {type(e).__name__}",
                "fix": "check [health] db_path in kokpit.toml; run `python -m kokpit hermes status`"}
    except OSError as e:
        return {"ok": False, "error": f"file could not be read or written: {type(e).__name__}", "fix": "check the vault path and permissions"}
    return {"ok": True, "mode": state["mode"], **body}


def handler(env: c.Env, name: str):
    def handle(args, **_kw) -> str:
        return _dump(run(env, name, args))
    handle.__name__ = f"handle_{name}"
    return handle
