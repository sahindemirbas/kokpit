"""What the Briefing tab shows. Reads the digest table filled by the ``briefing`` job; never fetches."""
from __future__ import annotations

import json

from ...config import Config
from ...db import connect
from ..health import api as health_api
from . import collect, podcast, tts


def _items(rows) -> list[dict]:
    out = []
    for r in rows:
        d = dict(r)
        extra = json.loads(d.get("extra") or "{}")
        ctx = extra.pop("personal_context", [])
        vis = json.loads(d["visual"]) if d.get("visual") else None
        out.append({**d, "tags": json.loads(d["tags"] or "[]"), "headline": bool(d["headline"]), "by_model": bool(d["by_model"]),
                    "extra": extra, "visual": vis, "personal_context": ctx if isinstance(ctx, list) else []})
    return out


def view(cfg: Config) -> dict:
    out = {"enabled": cfg.modules.get("briefing", False), "freshness": health_api.freshness(cfg, "briefing"),
           "day": None, "sections": [], "speech": "", "audio": None, "podcast": None,
           "tts": {"provider": cfg.briefing["tts"]["provider"], "on": tts.enabled(cfg)}}
    if not out["enabled"]:
        return out
    if not collect.has_sources(cfg):
        out["reason"] = "No topics yet: add [[briefing.topics]] with feeds and keywords to kokpit.toml."
        return out
    try:
        conn = connect(cfg.db, readonly=True)
    except FileNotFoundError:
        out["reason"] = "no database yet: run `kokpit run-job briefing`"
        return out
    try:
        try:
            t = collect.table_name(conn)
            day = conn.execute(f"SELECT MAX(day) FROM {t}").fetchone()[0]
            rows = conn.execute(f"SELECT * FROM {t} WHERE day=? ORDER BY project IS NULL, score DESC, title", (day,)).fetchall() if day else []
        except Exception:  # table not created yet
            day, rows = None, []
    finally:
        conn.close()
    items = _items(rows)
    out["day"] = day
    sections: dict[str, dict] = {}
    for it in items:
        key = "__projects__" if it["project"] else it["topic"]
        sec = sections.setdefault(key, {"name": "Matches your projects" if it["project"] else it["topic"], "tagged": bool(it["project"]), "items": []})
        sec["items"].append(it)
    out["sections"] = list(sections.values())
    if items:
        out["speech"] = collect.speech_text(day, [{**i, "project": i["project"]} for i in items])
    if day and tts.audio_path(cfg, day).is_file():
        out["audio"] = "/api/briefing/audio"
    if day:
        out["podcast"] = podcast.info(cfg, day)
    out["count"] = len(items)
    return out


def audio_file(cfg: Config):
    """Path of the newest digest audio file, or None."""
    d = tts.audio_path(cfg, "x").parent
    files = sorted(d.glob("digest-*.mp3")) if d.is_dir() else []
    return files[-1] if files else None
