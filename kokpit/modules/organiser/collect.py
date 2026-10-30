"""Night organiser: scan the vault, store proposals, write one report and one plan file. Never edits or deletes a note.

Job contract: ``collect(cfg, conn, today=None) -> (rows, info, warnings)``; rows is the number of NEW proposals.
Detectors are rules (see rules.py). The model is optional (semantic.py): with ``[model.uses] night`` off, nothing
is sent anywhere. Each run also writes ``<data>/organiser/plan-YYYY-MM-DD.json`` (inputs with hashes, proposals,
rejected model items); the plan never goes into the vault.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path

from ...config import Config
from ..brain import reader
from ..model import client
from . import rules, semantic

SCHEMA = (Path(__file__).parent / "schema.sql").read_text(encoding="utf-8")
MARKER = "<!-- kokpit-organiser: generated report, safe to delete -->"
REPORT_NAME = "organiser-report.md"
SKIP_FILES = {"threads.md", "last-session.md", "to-do.md"}
KINDS = ("categorise", "link", "conditions", "enrich", "sync", "todo")
STATUSES = ("open", "gone", "dismissed", "done")
DECISIONS = ("open", "dismissed", "done")
NEW_COLUMNS = {"source": "TEXT NOT NULL DEFAULT 'rule'", "reason": "TEXT NOT NULL DEFAULT ''",
               "evidence": "TEXT NOT NULL DEFAULT ''"}
PLAN_FIELDS = ("id", "kind", "path", "target", "detail", "text", "source", "reason", "evidence", "status", "first_seen", "last_seen")
SYSTEM = ("You write a short plain summary (two sentences at most) of a list of housekeeping suggestions "
          "for a personal notes vault. Do not invent items. No other text.")
FM = re.compile(r"\A---\r?\n(.*?)\r?\n---\r?\n?", re.S)
ISO = re.compile(r"(20\d\d-\d\d-\d\d)")
PLAN_NAME = re.compile(r"^plan-(20\d\d-\d\d-\d\d)\.json$")


def ensure_schema(conn: sqlite3.Connection) -> None:
    """Create the tables; add the columns a table from an older version lacks. Status needs no change (text column)."""
    try:
        conn.executescript(SCHEMA)
        have = {r[1] for r in conn.execute("PRAGMA table_info(organiser_proposals)")}
        for col, decl in NEW_COLUMNS.items():
            if col not in have:
                conn.execute(f"ALTER TABLE organiser_proposals ADD COLUMN {col} {decl}")
        conn.commit()
    except sqlite3.OperationalError as e:
        if "readonly" not in str(e):
            raise


def report_path(cfg: Config) -> Path:
    return cfg.db.parent / REPORT_NAME


def plan_dir(cfg: Config) -> Path:
    return cfg.db.parent / "organiser"


def plan_path(cfg: Config, day: str) -> Path:
    return plan_dir(cfg) / f"plan-{day}.json"


def vault_report_path(cfg: Config) -> Path | None:
    folder = cfg.organiser["report_folder"]
    parts = Path(folder).parts
    if not folder or Path(folder).is_absolute() or ".." in parts:
        return None
    return Path(cfg.resolve(cfg.vault_path)) / folder / REPORT_NAME


def _list(v: str) -> list[str]:
    v = v.strip().strip("[]")
    return [x.strip().strip("'\"") for x in v.split(",") if x.strip().strip("'\"")]


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def parse_note(rel: str, text: str, mtime: float) -> dict:
    m = FM.match(text)
    fm: dict[str, str] = {}
    if m:
        for line in m.group(1).splitlines():
            k, sep, v = line.partition(":")
            if sep and k.strip() and not k.startswith((" ", "\t", "-")):
                fm[k.strip().lower()] = v.strip().strip("'\"")
    body = text[m.end():] if m else text
    stem = Path(rel).stem
    h1 = re.search(r"^#\s+(.+?)\s*$", body, re.M)
    title = fm.get("title") or (h1.group(1) if h1 else stem)
    d = ISO.search(fm.get("date", "")) or ISO.search(stem)
    day = d.group(1) if d else datetime.fromtimestamp(mtime).date().isoformat()
    links = {x.group(1).strip().lower().removesuffix(".md") for x in reader.WIKILINK.finditer(body)}
    key = {title.lower(), stem.lower(), rel.lower().removesuffix(".md")}
    return {"path": rel, "title": title.strip(), "aliases": _list(fm.get("aliases", "")), "fm": fm,
            "has_fm": bool(m), "body": body, "date": day, "links": links, "links_key": key,
            "mtime": mtime, "sha256": _sha(text), "chars": len(text)}


def scan(cfg: Config) -> list[dict]:
    vault = Path(cfg.resolve(cfg.vault_path))
    skip = Path(cfg.organiser["report_folder"]).parts[:1]
    notes = []
    if not vault.exists():
        return notes
    for p in sorted(vault.rglob("*.md")):
        rel = p.relative_to(vault)
        if any(part.startswith(".") for part in rel.parts) or p.name.lower() in SKIP_FILES and len(rel.parts) == 1:
            continue
        if cfg.organiser["apply_report_to_vault"] and skip and rel.parts[0] == skip[0]:
            continue
        notes.append(parse_note(rel.as_posix(), reader._read(p), p.stat().st_mtime))
    return notes


def _rule_proposals(cfg: Config, notes: list[dict], threads_text: str, todo_text: str, today: date) -> list[dict]:
    o = cfg.organiser
    threads = reader.parse_threads(threads_text)
    found = (rules.categorise(notes, o["types"], o["folders"]) + rules.link(notes)
             + rules.conditions(notes) + rules.enrich(notes, o["required_fields"], o["short_summary"])
             + rules.sync(notes, threads, today, o["stale_days"])
             + rules.todo(todo_text, notes, today, o["todo_undated_days"]))
    for p in found:
        p.update(source="rule", reason="", evidence="")
    return found


def detect(cfg: Config, today: date) -> list[dict]:
    """Rule proposals only (no model, no writes)."""
    vault = Path(cfg.resolve(cfg.vault_path))
    return _rule_proposals(cfg, scan(cfg), reader._read(vault / "Threads.md"), reader._read(vault / "To-Do.md"), today)


def _read_plan(cfg: Config, day: str) -> dict | None:
    try:
        return json.loads(plan_path(cfg, day).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def plan_run(cfg: Config, today: date) -> dict:
    """Detect everything for one run without writing anything. Returns the plan parts and warnings.

    Same day, same inputs and a plan that already holds model results: the stored model proposals are reused
    instead of asking the model again, so a re-run is stable and sends nothing.
    """
    day = today.isoformat()
    vault = Path(cfg.resolve(cfg.vault_path))
    notes = scan(cfg)
    threads_text, todo_text = reader._read(vault / "Threads.md"), reader._read(vault / "To-Do.md")
    inputs = [{"path": n["path"], "sha256": n["sha256"], "chars": n["chars"]} for n in notes]
    for name, text in (("Threads.md", threads_text), ("To-Do.md", todo_text)):
        if (vault / name).exists():
            inputs.append({"path": name, "sha256": _sha(text), "chars": len(text)})
    inputs.sort(key=lambda x: x["path"])
    found = _rule_proposals(cfg, notes, threads_text, todo_text, today)
    warnings: list[str] = []
    model_found: list[dict] = []
    rejected: list[dict] = []
    model_used = False
    if client.use_enabled(cfg, "night"):
        old = _read_plan(cfg, day)
        if old and old.get("model_used") and old.get("inputs") == inputs:
            model_found = [{k: p.get(k, "") for k in ("kind", "path", "target", "detail", "text", "source", "reason", "evidence")}
                           for p in old.get("proposals", []) if p.get("source") == "model"]
            rejected = list(old.get("rejected", []))
            model_used = True
        else:
            try:
                model_found, rejected, _sent = semantic.propose(cfg, notes, today, client.complete)
                model_used = True
            except Exception as exc:  # the rule-based report stands without it
                warnings.append(f"model proposals skipped: {exc}")
        if rejected:
            why = sorted({r["reason"] for r in rejected})
            warnings.append(f"model proposals rejected: {len(rejected)} ({', '.join(why)})")
    rule_keys = {(p["kind"], p["path"], p["target"], p["detail"]) for p in found}
    dup = [p for p in model_found if (p["kind"], p["path"], p["target"], p["detail"]) in rule_keys]
    found += [p for p in model_found if p not in dup]  # same UNIQUE key as a rule proposal: the rule one stays
    return {"day": day, "inputs": inputs, "found": found, "rejected": rejected, "model_used": model_used,
            "duplicates": len(dup), "warnings": warnings}


def store(conn: sqlite3.Connection, found: list[dict], day: str) -> int:
    """Insert or refresh proposals. A user decision (dismissed, done) is never reopened; only last_seen moves.
    When a rule and a model proposal share a key, the rule one is kept."""
    new = 0
    seen = set()
    for p in found:
        key = (p["kind"], p["path"], p["target"], p["detail"])
        if key in seen:
            continue
        seen.add(key)
        src, reason, evidence = p.get("source", "rule"), p.get("reason", ""), p.get("evidence", "")
        cur = conn.execute("INSERT OR IGNORE INTO organiser_proposals(kind,path,target,detail,text,first_seen,last_seen,source,reason,evidence)"
                           " VALUES(?,?,?,?,?,?,?,?,?,?)", (*key, p["text"], day, day, src, reason, evidence))
        if cur.rowcount:
            new += 1
            continue
        old = conn.execute("SELECT source FROM organiser_proposals WHERE kind=? AND path=? AND target=? AND detail=?", key).fetchone()
        keep_status = "CASE WHEN status IN ('dismissed','done') THEN status ELSE 'open' END"
        if old and old[0] == "rule" and src == "model":
            conn.execute(f"UPDATE organiser_proposals SET status={keep_status}, last_seen=? WHERE kind=? AND path=? AND target=? AND detail=?",
                         (day, *key))
        else:
            conn.execute(f"UPDATE organiser_proposals SET status={keep_status}, text=?, source=?, reason=?, evidence=?, last_seen=?"
                         " WHERE kind=? AND path=? AND target=? AND detail=?", (p["text"], src, reason, evidence, day, *key))
    conn.execute("UPDATE organiser_proposals SET status='gone' WHERE status='open' AND last_seen<>?", (day,))
    conn.commit()
    return new


def _sort(rows: list[dict]) -> list[dict]:
    order = {k: i for i, k in enumerate(KINDS)}
    rows.sort(key=lambda r: (order.get(r["kind"], 9), r["path"], r["target"], r["detail"]))
    return rows


def open_proposals(conn: sqlite3.Connection, statuses: tuple[str, ...] = ("open",)) -> list[dict]:
    marks = ",".join("?" * len(statuses))
    rows = [dict(r) for r in conn.execute(f"SELECT * FROM organiser_proposals WHERE status IN ({marks})", statuses)]
    for r in rows:
        r.setdefault("source", "rule")
        r.setdefault("reason", "")
        r.setdefault("evidence", "")
    return _sort(rows)


def decide(conn: sqlite3.Connection, pid, status: str) -> dict:
    """Record the user's decision on one proposal. Touches only this table; never a note."""
    if status not in DECISIONS:
        raise ValueError(f"status must be one of: {', '.join(DECISIONS)}")
    if isinstance(pid, bool) or not isinstance(pid, (int, str)) or not str(pid).isdigit():
        raise ValueError("id must be a whole number")
    cur = conn.execute("UPDATE organiser_proposals SET status=? WHERE id=?", (status, int(pid)))
    if not cur.rowcount:
        raise LookupError("proposal not found")
    conn.commit()
    return dict(conn.execute("SELECT * FROM organiser_proposals WHERE id=?", (int(pid),)).fetchone())


def counts(rows: list[dict]) -> dict[str, int]:
    return {k: sum(1 for r in rows if r["kind"] == k) for k in KINDS}


def morning_text(rows: list[dict], summary: str = "", top: int = 5) -> str:
    """Deterministic: counts plus the first ``top`` open proposals. Same rows give the same text."""
    rows = [r for r in rows if r.get("status", "open") == "open"]
    if not rows:
        return "Organiser: no open proposals."
    c = counts(rows)
    head = f"Organiser: {len(rows)} open proposal(s) ({', '.join(f'{n} {k}' for k, n in c.items() if n)})."
    lines = [head] + [f"{i}. [{r['kind']}] {r['text']}" for i, r in enumerate(rows[:top], 1)]
    if summary:
        lines.append("Summary: " + summary)
    return "\n".join(lines)


def get_summary(conn: sqlite3.Connection) -> str:
    r = conn.execute("SELECT value FROM organiser_meta WHERE key='summary'").fetchone()
    return r[0] if r else ""


def report_text(rows: list[dict], day: str, summary: str = "") -> str:
    rows = [r for r in rows if r.get("status", "open") == "open"]
    lines = [MARKER, f"# Organiser report {day}", "", "Proposals only. Nothing in your notes was changed.", ""]
    if summary:
        lines += [summary, ""]
    lines.append(f"Open proposals: {len(rows)}")
    for k in KINDS:
        sel = [r for r in rows if r["kind"] == k]
        if sel:
            lines += ["", f"## {k} ({len(sel)})"] + [
                f"- {r['text']} ({r['path'] or 'vault'})" + (" [model]" if r.get("source") == "model" else "") for r in sel]
    return "\n".join(lines) + "\n"


def _write_report(cfg: Config, text: str) -> tuple[str, str | None]:
    """Returns (where, warning). Inside the vault only a file that carries our marker is ever replaced."""
    if cfg.organiser["apply_report_to_vault"]:
        p = vault_report_path(cfg)
        if p is None:
            return "", "report_folder must be a relative folder inside the vault"
        if p.exists() and MARKER not in p.read_text(encoding="utf-8", errors="replace")[:200]:
            return "", f"{p.name} exists in the vault and was not created by the organiser; left alone"
    else:
        p = report_path(cfg)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return str(p), None


def _plan_counts(plan: dict, open_rows: int) -> dict:
    found = plan["found"]
    return {"rule": sum(1 for p in found if p["source"] == "rule"), "model": sum(1 for p in found if p["source"] == "model"),
            "rejected": len(plan["rejected"]), "duplicates": plan["duplicates"], "open": open_rows,
            "by_kind": counts(found)}


def write_plan(cfg: Config, plan: dict, rows: list[dict], open_rows: int, today: date) -> Path:
    """Write the plan file to the data folder (never the vault) and drop plan files older than the keep window."""
    d = plan_dir(cfg)
    d.mkdir(parents=True, exist_ok=True)
    out = {"day": plan["day"], "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
           "inputs": plan["inputs"], "proposals": [{k: r.get(k, "") for k in PLAN_FIELDS} for r in rows],
           "rejected": plan["rejected"], "counts": _plan_counts(plan, open_rows), "model_used": plan["model_used"]}
    p = plan_path(cfg, plan["day"])
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(out, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(p)
    cutoff = today - timedelta(days=cfg.organiser["plan_keep_days"])
    for f in d.iterdir():
        m = PLAN_NAME.match(f.name)
        if m and date.fromisoformat(m.group(1)) < cutoff:
            f.unlink()
    return p


def dry_run(cfg: Config, today: date | None = None, top: int = 10) -> str:
    """Detect and describe; writes neither the plan file nor the database nor the report."""
    today = today or date.today()
    if not Path(cfg.resolve(cfg.vault_path)).exists():
        return "organiser dry run: vault not found: set vault_path in kokpit.toml"
    plan = plan_run(cfg, today)
    found = _sort(list(plan["found"]))
    c = _plan_counts(plan, 0)
    lines = [f"organiser dry run {plan['day']}: {len(found)} proposal(s), {c['rule']} rule, {c['model']} model, "
             f"{c['rejected']} rejected; model {'used' if plan['model_used'] else 'off'}; nothing written",
             "by kind: " + ", ".join(f"{n} {k}" for k, n in c["by_kind"].items() if n)]
    lines += [f"{i}. [{p['kind']}/{p['source']}] {p['text']}" for i, p in enumerate(found[:top], 1)]
    lines += [f"warning: {w}" for w in plan["warnings"]]
    return "\n".join(lines)


def collect(cfg: Config, conn: sqlite3.Connection, today: date | None = None) -> tuple[int, dict, list[str]]:
    ensure_schema(conn)
    today = today or date.today()
    day = today.isoformat()
    if not Path(cfg.resolve(cfg.vault_path)).exists():
        return 0, {}, ["vault not found: set vault_path in kokpit.toml"]
    plan = plan_run(cfg, today)
    warnings: list[str] = list(plan["warnings"])
    new = store(conn, plan["found"], day)
    keys = {(p["kind"], p["path"], p["target"], p["detail"]) for p in plan["found"]}
    seen_rows = [r for r in open_proposals(conn, STATUSES) if (r["kind"], r["path"], r["target"], r["detail"]) in keys]
    rows = open_proposals(conn)
    summary = ""
    if rows and client.use_enabled(cfg, "night"):
        try:
            summary = client.complete(cfg, "night", SYSTEM, "\n".join(r["text"] for r in rows[:20]), max_tokens=160).strip()[:400]
        except Exception as exc:  # the rule-based report stands without it
            warnings.append(f"model summary skipped: {exc}")
    conn.execute("INSERT INTO organiser_meta(key,value) VALUES('summary',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (summary,))
    conn.commit()
    plan_file = write_plan(cfg, plan, seen_rows, len(rows), today)
    where, warn = _write_report(cfg, report_text(rows, day, summary))
    if warn:
        warnings.append(warn)
    return new, {"open": len(rows), "counts": counts(rows), "report": where, "plan": str(plan_file),
                 "model": plan["model_used"], "rejected": len(plan["rejected"])}, warnings
