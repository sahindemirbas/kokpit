"""Briefing digest: fetch the feeds of your topics, score, tag with projects, summarise, store, optionally speak.

Job contract: ``collect(cfg, conn) -> (rows, info, warnings)``. Each feed that fails is a warning.
The model is optional: with ``[model.uses] digest`` off, the feed's own summary is cut to a sentence-sized length.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import date
from pathlib import Path

from ...config import Config
from ..brain import reader
from ..model import client
from . import feeds, match, sources, tts, visual

SCHEMA = (Path(__file__).parent / "schema.sql").read_text(encoding="utf-8")
SYSTEM = ("You summarise one item for a personal daily digest and sketch one small diagram of it. Answer with a JSON object "
          '{"summary": two plain sentences, "why": one short sentence on why it may matter, '
          '"visual": {"title": str, "diagram_type": "concept"|"process"|"comparison"|"timeline"|"layers", '
          '"nodes": [{"id": str, "label": str}] (max 4), "edges": [{"from": id, "to": id}] (max 4), '
          '"metrics": [{"label": str, "value": number, "unit": str}] (max 4, only numbers given in the input), '
          '"takeaway": one sentence} or null}. Use no number that is not in the input. No other text.')
NEW_COLUMNS = (("extra", "TEXT NOT NULL DEFAULT '{}'"), ("visual", "TEXT"))


LEGACY_TABLE, LEGACY_INDEX = "agenda_items", "idx_agenda_day"  # names before the rename


def _tables(conn: sqlite3.Connection) -> set[str]:
    return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def migrate_legacy(conn: sqlite3.Connection) -> None:
    """Rename the pre-rename table once; does nothing when already done or never created."""
    names = _tables(conn)
    if LEGACY_TABLE in names and "briefing_items" not in names:
        conn.execute(f"ALTER TABLE {LEGACY_TABLE} RENAME TO briefing_items")
        conn.execute(f"DROP INDEX IF EXISTS {LEGACY_INDEX}")
        conn.commit()


def table_name(conn: sqlite3.Connection) -> str:
    """Table to read: the new one, or the old one on a read-only database not migrated yet."""
    names = _tables(conn)
    return LEGACY_TABLE if LEGACY_TABLE in names and "briefing_items" not in names else "briefing_items"


def ensure_schema(conn: sqlite3.Connection) -> None:
    try:
        migrate_legacy(conn)
        conn.executescript(SCHEMA)
        have = {r[1] for r in conn.execute("PRAGMA table_info(briefing_items)")}
        for name, decl in NEW_COLUMNS:  # databases made before these columns existed
            if name not in have:
                conn.execute(f"ALTER TABLE briefing_items ADD COLUMN {name} {decl}")
    except sqlite3.OperationalError as e:
        if "readonly" not in str(e):
            raise


def _facts(extra: dict) -> dict:
    return {k: v for k, v in (extra or {}).items() if k != "personal_context"}


def _model_lines(cfg: Config, item: dict) -> tuple[str, str, dict | None] | None:
    """Fields sent (declared in model USES['digest']): title, tags, short source summary, measured facts.
    Raises on provider failure. The visual is validated strictly; an invalid one becomes None."""
    src = match.cut(item["summary"], 600)
    payload = json.dumps({"title": item["title"], "tags": item["tags"][:6], "summary": src,
                          "facts": _facts(item.get("extra", {}))}, ensure_ascii=False)
    raw = client.complete(cfg, "digest", SYSTEM, payload, max_tokens=500).strip()
    raw = raw[raw.find("{"): raw.rfind("}") + 1] if "{" in raw else raw
    d = json.loads(raw)
    s, w = str(d.get("summary", "")).strip(), str(d.get("why", "")).strip()
    if not s:
        return None
    source_text = " ".join([item["title"], " ".join(item["tags"]), item["summary"]])
    v = visual.validate(d.get("visual"), source_text, _facts(item.get("extra", {})))
    return match.cut(s, 360), match.cut(w, 280), v


def build(cfg: Config, fetcher=None, now=None, threads: list[dict] | None = None, get=None) -> tuple[list[dict], dict, list[str]]:
    """Pure-ish selection step: returns (items, info, warnings). Network only through ``fetcher`` / ``get`` / the model client."""
    fetcher = fetcher or feeds.fetch_feed
    now = now or match.now_utc()
    a = cfg.briefing
    warnings: list[str] = []
    if threads is None:
        threads = []
        if cfg.modules.get("brain", True) and cfg.vault.exists():
            threads = match.brain_threads(reader.read_vault(cfg.vault))
    seen: set[str] = set()
    items: list[dict] = []
    per_topic: dict[str, int] = {}
    for topic in a["topics"]:
        pool = []
        if not topic["feeds"]:
            warnings.append(f"topic {topic['name']}: no feed listed")
        for url in topic["feeds"]:
            host = url.split("/")[2] if url.count("/") >= 2 else url
            try:
                got = fetcher(url)
            except Exception as exc:
                warnings.append(f"{topic['name']} / {host}: {type(exc).__name__}: {str(exc)[:100]}")
                continue
            if not got:
                warnings.append(f"{topic['name']} / {host}: 0 items (check the feed address)")
            pool.extend(got)
        scored = []
        for it in pool:
            if it["url"] in seen or match.too_old(it["published"], now, a["max_age_days"]):
                continue
            score, hits = match.topic_score(it, topic["keywords"], now, a["max_age_days"])
            if topic["keywords"] and not hits:
                continue  # a topic with keywords keeps only items that mention one
            scored.append({**it, "topic": topic["name"], "score": score, "hits": hits})
        scored.sort(key=lambda x: (-x["score"], x["title"]))
        keep = scored[: a["max_per_topic"]]
        per_topic[topic["name"]] = len(keep)
        for it in keep:
            seen.add(it["url"])
            items.append(it)
    for name, (got, warns) in ((sources.GITHUB, sources.github_items(a["github"], now, get)),
                               (sources.HUGGINGFACE, sources.huggingface_items(a["huggingface"], now, get))):
        warnings.extend(warns)
        kept = 0
        for it in got:
            if it["url"] in seen:
                continue
            seen.add(it["url"])
            # measured popularity orders these sections; freshness keeps the same scale as feed topics
            pop = it["extra"].get("stars", it["extra"].get("likes", it["extra"].get("upvotes", 0)))
            items.append({**it, "topic": name, "hits": [],
                          "score": match.freshness(it["published"], now, max(a["max_age_days"], 30)) + min(5.0, pop / 200)})
            kept += 1
        if kept:
            per_topic[name] = kept
    for it in items:
        it.setdefault("extra", {})
        found = match.matching_threads(it, threads)
        it["project"], it["reason"] = (found[0][0]["project"], found[0][1]) if found else (None, None)
        if found:
            it["score"] += 5
            it["extra"]["personal_context"] = [th["title"] for th, _ in found]
    use_model = client.use_enabled(cfg, "digest")
    model_fail = 0
    for it in items:
        lines = None
        if use_model and model_fail < 3:
            try:
                lines = _model_lines(cfg, it)
            except client.ModelDisabled:
                use_model = False
            except Exception:  # ModelError, bad JSON: fall back for this item, give up after three in a row
                model_fail += 1
                if model_fail == 3:
                    warnings.append("model digest: three failures, plain summaries used for the rest")
        it["by_model"] = bool(lines)
        it["visual"] = None
        if lines:
            it["summary"], it["why"], it["visual"] = lines
            model_fail = 0
        else:
            it["summary"] = match.first_sentence(it["summary"], 180)
            why = it["reason"] or (f"keyword {', '.join(it['hits'][:3])} in topic {it['topic']}" if it["hits"] else f"in topic {it['topic']}")
            it["why"] = why
    items.sort(key=lambda x: (x["project"] is None, -x["score"], x["title"]))
    top: dict[str, str] = {}
    for it in items:
        sec = it["project"] and "__projects__" or it["topic"]
        it["section"] = sec
        it["headline"] = sec not in top
        top.setdefault(sec, it["url"])
    return items, {"topics": per_topic, "tagged": sum(1 for i in items if i["project"]),
                   "model": sum(1 for i in items if i["by_model"]), "brain_threads": len(threads)}, warnings


def speech_text(day: str, items: list[dict]) -> str:
    """Tagged items first (in their order), then the sections; headlines are read first inside each section."""
    parts = [f"Your digest for {day}."]
    for it in items:
        lead = f"For project {it['project']}: " if it["project"] else f"{it['topic']}: "
        parts.append(f"{lead}{it['title']}. {it['summary']}".strip())
    return " ".join(parts) if len(parts) > 1 else parts[0] + " Nothing new today."


def store(conn: sqlite3.Connection, day: str, items: list[dict]) -> int:
    ensure_schema(conn)
    conn.execute("DELETE FROM briefing_items WHERE day=?", (day,))
    for it in items:
        conn.execute(
            "INSERT OR REPLACE INTO briefing_items(day,topic,url,title,tags,published,score,headline,summary,why,by_model,project,reason,extra,visual) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (day, it["topic"], it["url"], it["title"], json.dumps(it["tags"], ensure_ascii=False),
             it["published"].isoformat() if it["published"] else None, round(it["score"], 2), int(it["headline"]),
             it["summary"], it["why"], int(it["by_model"]), it["project"], it["reason"],
             json.dumps(it.get("extra") or {}, ensure_ascii=False),
             json.dumps(it["visual"], ensure_ascii=False) if it.get("visual") else None))
    conn.commit()
    return len(items)


def has_sources(cfg: Config) -> bool:
    a = cfg.briefing
    return bool(a["topics"] or a["github"]["queries"] or a["huggingface"]["models"] or a["huggingface"]["papers"])


def collect(cfg: Config, conn: sqlite3.Connection, fetcher=None, now=None, threads=None, get=None) -> tuple[int, dict, list[str]]:
    if not has_sources(cfg):
        return 0, {}, ["no topic configured: add [[briefing.topics]] to kokpit.toml"]
    items, info, warnings = build(cfg, fetcher, now, threads, get)
    day = date.today().isoformat()
    n = store(conn, day, items)
    if not n:
        warnings.append("digest is empty: no feed item matched the topics")
    if cfg.briefing["tts"]["provider"] != "none":
        try:
            info["audio"] = tts.speak(cfg, day, speech_text(day, items))
        except Exception as exc:
            warnings.append(f"tts: {exc}")
    return n, info, warnings
