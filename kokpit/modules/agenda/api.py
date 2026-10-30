"""What the Agenda tab shows. Reads the digest table filled by the ``agenda`` job; never fetches."""
from __future__ import annotations

import json

from ...config import Config
from ...db import connect
from ..health import api as health_api
from . import collect, tts


def _items(rows) -> list[dict]:
    return [{**dict(r), "tags": json.loads(r["tags"] or "[]"), "headline": bool(r["headline"]), "by_model": bool(r["by_model"])} for r in rows]


def view(cfg: Config) -> dict:
    out = {"enabled": cfg.modules.get("agenda", False), "freshness": health_api.freshness(cfg, "agenda"),
           "day": None, "sections": [], "speech": "", "audio": None,
           "tts": {"provider": cfg.agenda["tts"]["provider"], "on": tts.enabled(cfg)}}
    if not out["enabled"]:
        return out
    if not cfg.agenda["topics"]:
        out["reason"] = "No topics yet: add [[agenda.topics]] with feeds and keywords to kokpit.toml."
        return out
    try:
        conn = connect(cfg.db, readonly=True)
    except FileNotFoundError:
        out["reason"] = "no database yet: run `kokpit run-job agenda`"
        return out
    try:
        try:
            day = conn.execute("SELECT MAX(day) FROM agenda_items").fetchone()[0]
            rows = conn.execute("SELECT * FROM agenda_items WHERE day=? ORDER BY project IS NULL, score DESC, title", (day,)).fetchall() if day else []
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
        out["audio"] = "/api/agenda/audio"
    out["count"] = len(items)
    return out


def audio_file(cfg: Config):
    """Path of the newest digest audio file, or None."""
    d = tts.audio_path(cfg, "x").parent
    files = sorted(d.glob("digest-*.mp3")) if d.is_dir() else []
    return files[-1] if files else None
