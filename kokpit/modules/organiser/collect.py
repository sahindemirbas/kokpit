"""Night organiser: scan the vault, store proposals, write one report. Never edits or deletes a note.

Job contract: ``collect(cfg, conn, today=None) -> (rows, info, warnings)``; rows is the number of NEW proposals.
Detectors are rules (see rules.py). The model is optional: with ``[model.uses] night`` off, nothing is sent anywhere.
"""
from __future__ import annotations

import re
import sqlite3
from datetime import date, datetime
from pathlib import Path

from ...config import Config
from ..brain import reader
from ..model import client
from . import rules

SCHEMA = (Path(__file__).parent / "schema.sql").read_text(encoding="utf-8")
MARKER = "<!-- kokpit-organiser: generated report, safe to delete -->"
REPORT_NAME = "organiser-report.md"
SKIP_FILES = {"threads.md", "last-session.md", "to-do.md"}
KINDS = ("categorise", "link", "conditions", "enrich", "sync")
SYSTEM = ("You write a short plain summary (two sentences at most) of a list of housekeeping suggestions "
          "for a personal notes vault. Do not invent items. No other text.")
FM = re.compile(r"\A---\r?\n(.*?)\r?\n---\r?\n?", re.S)
ISO = re.compile(r"(20\d\d-\d\d-\d\d)")


def ensure_schema(conn: sqlite3.Connection) -> None:
    try:
        conn.executescript(SCHEMA)
    except sqlite3.OperationalError as e:
        if "readonly" not in str(e):
            raise


def report_path(cfg: Config) -> Path:
    return cfg.db.parent / REPORT_NAME


def vault_report_path(cfg: Config) -> Path | None:
    folder = cfg.organiser["report_folder"]
    parts = Path(folder).parts
    if not folder or Path(folder).is_absolute() or ".." in parts:
        return None
    return Path(cfg.resolve(cfg.vault_path)) / folder / REPORT_NAME


def _list(v: str) -> list[str]:
    v = v.strip().strip("[]")
    return [x.strip().strip("'\"") for x in v.split(",") if x.strip().strip("'\"")]


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
            "has_fm": bool(m), "body": body, "date": day, "links": links, "links_key": key}


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


def detect(cfg: Config, today: date) -> list[dict]:
    o = cfg.organiser
    notes = scan(cfg)
    threads = reader.parse_threads(reader._read(Path(cfg.resolve(cfg.vault_path)) / "Threads.md"))
    return (rules.categorise(notes, o["types"], o["folders"]) + rules.link(notes)
            + rules.conditions(notes) + rules.enrich(notes, o["required_fields"], o["short_summary"])
            + rules.sync(notes, threads, today, o["stale_days"]))


def store(conn: sqlite3.Connection, found: list[dict], day: str) -> int:
    new = 0
    seen = set()
    for p in found:
        key = (p["kind"], p["path"], p["target"], p["detail"])
        if key in seen:
            continue
        seen.add(key)
        cur = conn.execute("INSERT OR IGNORE INTO organiser_proposals(kind,path,target,detail,text,first_seen,last_seen) VALUES(?,?,?,?,?,?,?)",
                           (*key, p["text"], day, day))
        if cur.rowcount:
            new += 1
        else:
            conn.execute("UPDATE organiser_proposals SET status='open', text=?, last_seen=? WHERE kind=? AND path=? AND target=? AND detail=?",
                         (p["text"], day, *key))
    conn.execute("UPDATE organiser_proposals SET status='gone' WHERE status='open' AND last_seen<>?", (day,))
    conn.commit()
    return new


def open_proposals(conn: sqlite3.Connection) -> list[dict]:
    order = {k: i for i, k in enumerate(KINDS)}
    rows = [dict(r) for r in conn.execute("SELECT * FROM organiser_proposals WHERE status='open'")]
    rows.sort(key=lambda r: (order.get(r["kind"], 9), r["path"], r["target"], r["detail"]))
    return rows


def counts(rows: list[dict]) -> dict[str, int]:
    return {k: sum(1 for r in rows if r["kind"] == k) for k in KINDS}


def morning_text(rows: list[dict], summary: str = "", top: int = 5) -> str:
    """Deterministic: counts plus the first ``top`` proposals. Same rows give the same text."""
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
    lines = [MARKER, f"# Organiser report {day}", "", "Proposals only. Nothing in your notes was changed.", ""]
    if summary:
        lines += [summary, ""]
    lines.append(f"Open proposals: {len(rows)}")
    for k in KINDS:
        sel = [r for r in rows if r["kind"] == k]
        if sel:
            lines += ["", f"## {k} ({len(sel)})"] + [f"- {r['text']} ({r['path'] or 'vault'})" for r in sel]
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


def collect(cfg: Config, conn: sqlite3.Connection, today: date | None = None) -> tuple[int, dict, list[str]]:
    ensure_schema(conn)
    today = today or date.today()
    day = today.isoformat()
    if not Path(cfg.resolve(cfg.vault_path)).exists():
        return 0, {}, ["vault not found: set vault_path in kokpit.toml"]
    warnings: list[str] = []
    new = store(conn, detect(cfg, today), day)
    rows = open_proposals(conn)
    summary = ""
    if rows and client.use_enabled(cfg, "night"):
        try:
            summary = client.complete(cfg, "night", SYSTEM, "\n".join(r["text"] for r in rows[:20]), max_tokens=160).strip()[:400]
        except Exception as exc:  # the rule-based report stands without it
            warnings.append(f"model summary skipped: {exc}")
    conn.execute("INSERT INTO organiser_meta(key,value) VALUES('summary',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (summary,))
    conn.commit()
    where, warn = _write_report(cfg, report_text(rows, day, summary))
    if warn:
        warnings.append(warn)
    return new, {"open": len(rows), "counts": counts(rows), "report": where}, warnings
