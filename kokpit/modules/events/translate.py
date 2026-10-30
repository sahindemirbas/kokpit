"""Optional translation of event titles and descriptions through the model connection.

Does nothing unless ``[events] translate_to`` is set AND the model use ``events_translate`` is on with a key.
Only title and description leave the machine. Translations already stored for the same text and target are reused,
so a collect run asks the model only about new or changed events. A failure is a warning; the original text stays.
"""
from __future__ import annotations

import json
import sqlite3

from ...config import Config
from ..model import client
from . import store

BATCH = 10
SYSTEM = ("You translate event listings. Input is a JSON list of objects with id, title and description. "
          "Translate title and description into the language with code '{lang}'. Keep names of people, venues and places. "
          "Answer with only a JSON list of objects with id, title, description, same ids, nothing else.")


def _cached(conn: sqlite3.Connection, target: str) -> dict:
    store.ensure_schema(conn)
    if not store.has_table(conn):
        return {}
    rows = conn.execute("SELECT source, source_id, title, description, title_tr, description_tr FROM events WHERE translated_to=? AND title_tr IS NOT NULL", (target,)).fetchall()
    return {(r["source"], r["source_id"]): r for r in rows}


def apply(cfg: Config, conn: sqlite3.Connection, records: list[dict], target: str) -> tuple[str, list[str]]:
    """Fills title_tr, description_tr, translated_to on ``records`` in place. Returns (outcome, warnings)."""
    try:
        client.check(cfg, "events_translate")
    except client.ModelDisabled as exc:
        return "skipped", [f"translate_to is '{target}' but {exc}"]
    cache = _cached(conn, target)
    todo = []
    for r in records:
        c = cache.get((r["source"], r["source_id"]))
        if c and c["title"] == r.get("title") and (c["description"] or "") == (r.get("description") or ""):
            r["title_tr"], r["description_tr"], r["translated_to"] = c["title_tr"], c["description_tr"], target
        else:
            todo.append(r)
    done, warnings = 0, []
    for i in range(0, len(todo), BATCH):
        chunk = todo[i:i + BATCH]
        payload = [{"id": n, "title": r.get("title") or "", "description": (r.get("description") or "")[:600]} for n, r in enumerate(chunk)]
        try:
            text = client.complete(cfg, "events_translate", SYSTEM.format(lang=target), json.dumps(payload, ensure_ascii=False), max_tokens=2000)
            out = {int(x["id"]): x for x in json.loads(text)}
        except client.ModelError as exc:
            warnings.append(f"translation failed: {exc}")
            break
        except (ValueError, KeyError, TypeError):
            warnings.append("translation answer was not the expected JSON; originals kept")
            continue
        for n, r in enumerate(chunk):
            x = out.get(n)
            if x and isinstance(x.get("title"), str) and x["title"].strip():
                r["title_tr"] = x["title"].strip()
                r["description_tr"] = x["description"].strip() if isinstance(x.get("description"), str) and r.get("description") else None
                r["translated_to"] = target
                done += 1
    return f"{done} translated, {len(records) - len(todo)} cached", warnings
