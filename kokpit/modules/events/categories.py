"""Keyword categories from language packs. Order: source hint > title > description > venue name.

Category ids and their UI labels are English and fixed in ``CATEGORIES``/``LABELS``.
The keywords live in data files, one per language: ``category_packs/<code>.json``
ships with Kokpit (``en`` on by default, ``it`` available), and a user can add or
override packs from ``[events] category_pack_dirs``. Format (docs/category-packs.md)::

    {"language": "de", "label": "Deutsch",
     "rules": [{"category": "concert", "keywords": ["konzert", "live"]}]}

Inside a pack the first matching rule wins, so rule order matters (opera before
concert, kids before theatre). Several packs are tried in the order of
``[events] category_languages`` for each field. A missing or broken pack, or a
rule with an unknown category id, is a warning and is skipped; nothing crashes.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

CATEGORIES = ["food", "concert", "opera", "theatre", "dance", "exhibition", "cinema", "fair", "kids", "sport", "talk", "other"]
LABELS = {"food": "Food & festivals", "concert": "Concert", "opera": "Opera", "theatre": "Theatre", "dance": "Dance",
          "exhibition": "Exhibition", "cinema": "Cinema", "fair": "Fair & market", "kids": "Kids & family",
          "sport": "Sport", "talk": "Talk", "other": "Other"}
ALIASES = {"sagra": "food"}  # older ids still accepted as a source hint
DEFAULT_LANGUAGES = ["en"]
BUILTIN_DIR = Path(__file__).with_name("category_packs")

Pack = list[tuple[str, list[str]]]


def read_pack(path: Path) -> tuple[Pack | None, list[str]]:
    """Parse one pack file. Returns (rules or None if unusable, warnings)."""
    warns: list[str] = []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        rules = data["rules"]
        if not isinstance(rules, list):
            raise ValueError("'rules' must be a list")
    except Exception as exc:
        return None, [f"category pack {path.name}: unreadable ({type(exc).__name__}: {str(exc)[:80]}), skipped"]
    pack: Pack = []
    for i, r in enumerate(rules):
        cat = r.get("category") if isinstance(r, dict) else None
        cat = ALIASES.get(cat, cat)
        words = r.get("keywords") if isinstance(r, dict) else None
        if cat not in CATEGORIES or cat == "other":
            warns.append(f"category pack {path.name}: rule {i + 1} has unknown category {cat!r}, skipped")
            continue
        if not isinstance(words, list) or not all(isinstance(w, str) for w in words):
            warns.append(f"category pack {path.name}: rule {i + 1} needs a list of keyword strings, skipped")
            continue
        pack.append((cat, [w.lower() for w in words if w.strip()]))
    return pack, warns


def load_packs(languages: list[str] | None = None, dirs: list[str | Path] | None = None) -> tuple[list[Pack], list[str]]:
    """Packs for ``languages`` in order. A user dir wins over the built-in pack with the same code;
    among user dirs the first listed wins. Unknown codes are warnings; if nothing loads, ``en`` is used."""
    warns: list[str] = []
    packs: list[Pack] = []
    for lang in languages or DEFAULT_LANGUAGES:
        lang = str(lang).strip().lower()
        candidates = [Path(d).expanduser() / f"{lang}.json" for d in (dirs or [])] + [BUILTIN_DIR / f"{lang}.json"]
        path = next((p for p in candidates if p.is_file()), None)
        if path is None:
            warns.append(f"category pack {lang!r}: not found, skipped")
            continue
        pack, w = read_pack(path)
        warns.extend(w)
        if pack is not None:
            packs.append(pack)
    if not packs:
        fallback, _ = read_pack(BUILTIN_DIR / "en.json")
        packs = [fallback or []]
    return packs, warns


def _match(text: str, pack: Pack) -> str | None:
    t = f" {text.lower()} "
    for cat, words in pack:
        for w in words:
            if re.search(r"(?<![a-zà-ü])" + re.escape(w), t):
                return cat
    return None


def classify(rec: dict, languages: list[str] | None = None, packs: list[Pack] | None = None) -> str:
    """``packs`` from :func:`load_packs`; without it the built-in packs for ``languages`` are loaded."""
    hint = ALIASES.get(rec.get("category_hint"), rec.get("category_hint"))
    if hint in CATEGORIES:
        return hint
    if packs is None:
        packs, _ = load_packs(languages)
    for field in ("title", "description", "venue"):
        v = rec.get(field)
        if not v:
            continue
        for pack in packs:
            c = _match(v, pack)
            if c:
                return c
    return "other"
