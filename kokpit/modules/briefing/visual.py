"""Strict validation of a model-made visual spec for one digest item.

Schema (same shape as the reference design): ``title`` (str), ``diagram_type`` (concept, process,
comparison, timeline, layers), ``nodes`` [{id, label}], ``edges`` [{from, to}], ``metrics``
[{label, value (number), unit}], ``takeaway`` (str), optional ``caveat`` (str). At most four nodes,
edges and metrics. Every number anywhere in the spec must appear in the source text or in the
measured facts (``extra``); otherwise the whole visual is dropped and the card falls back to text.
A spec without metrics is a schematic, not a measurement, and is marked ``schematic: true``.
"""
from __future__ import annotations

import re

TYPES = ("concept", "process", "comparison", "timeline", "layers")
NUM = re.compile(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?")
LIMIT = 4


def numbers(text: str) -> set[float]:
    out = set()
    for m in NUM.findall(text or ""):
        try:
            out.add(float(m.replace(",", "")))
        except ValueError:
            pass
    return out


def fact_numbers(extra: dict) -> set[float]:
    out = set()
    for k, v in (extra or {}).items():
        if k == "personal_context":
            continue
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            out.add(float(v))
        elif isinstance(v, str):
            out |= numbers(v)
    return out


def _s(v, n: int) -> str | None:
    return v.strip()[:n] if isinstance(v, str) and v.strip() else None


def validate(spec, source_text: str, extra: dict | None = None) -> dict | None:
    """Clean spec or None. ``source_text`` is what the item itself says (title, summary, tags)."""
    if not isinstance(spec, dict) or spec.get("diagram_type") not in TYPES:
        return None
    title, take = _s(spec.get("title"), 90), _s(spec.get("takeaway"), 220)
    nodes, edges, metrics = spec.get("nodes"), spec.get("edges", []), spec.get("metrics", [])
    if not title or not take or not all(isinstance(x, list) for x in (nodes, edges, metrics)):
        return None
    if any(len(x) > LIMIT for x in (nodes, edges, metrics)) or not (nodes or metrics):
        return None
    clean_nodes, ids = [], set()
    for n in nodes:
        if not isinstance(n, dict):
            return None
        nid, label = _s(str(n.get("id", "")) if n.get("id") is not None else None, 30), _s(n.get("label"), 60)
        if not nid or not label or nid in ids:
            return None
        ids.add(nid)
        clean_nodes.append({"id": nid, "label": label})
    clean_edges = []
    for e in edges:
        if not isinstance(e, dict) or e.get("from") not in ids or e.get("to") not in ids or e.get("from") == e.get("to"):
            return None
        clean_edges.append({"from": e["from"], "to": e["to"]})
    clean_metrics = []
    for m in metrics:
        if not isinstance(m, dict):
            return None
        label, unit, value = _s(m.get("label"), 50), m.get("unit", ""), m.get("value")
        if not label or not isinstance(unit, str) or isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        if value != value or value < 0:  # NaN or negative
            return None
        clean_metrics.append({"label": label, "value": value, "unit": unit.strip()[:16]})
    caveat = _s(spec.get("caveat"), 160)
    allowed = numbers(source_text) | fact_numbers(extra or {})
    used = set()
    for t in [title, take, caveat or ""] + [n["label"] for n in clean_nodes] + [m["label"] + " " + m["unit"] for m in clean_metrics]:
        used |= numbers(t)
    used |= {float(m["value"]) for m in clean_metrics}
    if not used <= allowed:
        return None
    out = {"title": title, "diagram_type": spec["diagram_type"], "nodes": clean_nodes, "edges": clean_edges,
           "metrics": clean_metrics, "takeaway": take, "schematic": not clean_metrics}
    if caveat:
        out["caveat"] = caveat
    return out
