"""Keyword categories per language. Order: source hint > title > description > venue name.

Categories: sagra, concert, opera, theatre, dance, exhibition, cinema, fair, kids, sport, talk, other.
A language pack is a list of (category, [keywords]); the first match wins, so the
list order inside a pack matters (opera before concert, kids before theatre).
"""
from __future__ import annotations

import re

CATEGORIES = ["sagra", "concert", "opera", "theatre", "dance", "exhibition", "cinema", "fair", "kids", "sport", "talk", "other"]

PACKS: dict[str, list[tuple[str, list[str]]]] = {
    "it": [
        ("kids", ["bambini", "famiglie", "ragazzi", "laboratorio per"]),
        ("sagra", ["sagra", "festa del", "festa della", "street food", "degustazion", "cantine aperte"]),
        ("opera", ["opera", "lirica", "lirico", "melodramma"]),
        ("dance", ["danza", "balletto", "ballo"]),
        ("theatre", ["teatro", "spettacolo teatrale", "commedia", "prosa", "monologo"]),
        ("cinema", ["cinema", "film", "proiezione", "rassegna cinematografica"]),
        ("exhibition", ["mostra", "esposizione", "museo", "vernissage", "inaugurazione"]),
        ("concert", ["concerto", "live", "musica", "jazz", "rock", "dj set", "coro", "orchestra", "recital"]),
        ("fair", ["fiera", "mercatino", "mercato", "antiquariato", "expo"]),
        ("sport", ["corsa", "maratona", "torneo", "partita", "calcio", "ciclismo", "trekking", "camminata"]),
        ("talk", ["conferenza", "incontro con", "presentazione del libro", "presentazione", "convegno", "seminario", "workshop", "dibattito", "lezione"]),
    ],
    "en": [
        ("kids", ["kids", "children", "family", "families"]),
        ("sagra", ["food festival", "street food", "tasting", "wine festival"]),
        ("opera", ["opera"]),
        ("dance", ["dance", "ballet"]),
        ("theatre", ["theatre", "theater", "play", "comedy", "stand-up", "standup"]),
        ("cinema", ["cinema", "film", "screening", "movie"]),
        ("exhibition", ["exhibition", "gallery", "museum", "vernissage"]),
        ("concert", ["concert", "live music", "gig", "jazz", "rock", "dj", "choir", "orchestra", "recital"]),
        ("fair", ["fair", "market", "flea", "expo"]),
        ("sport", ["run", "marathon", "tournament", "match", "football", "cycling", "hike", "yoga"]),
        ("talk", ["talk", "lecture", "conference", "book launch", "seminar", "workshop", "meetup", "panel"]),
    ],
}


def _match(text: str, pack: list[tuple[str, list[str]]]) -> str | None:
    t = f" {text.lower()} "
    for cat, words in pack:
        for w in words:
            if re.search(r"(?<![a-zà-ü])" + re.escape(w), t):
                return cat
    return None


def classify(rec: dict, languages: list[str] | None = None) -> str:
    hint = rec.get("category_hint")
    if hint in CATEGORIES:
        return hint
    packs = [PACKS[lang] for lang in (languages or ["en"]) if lang in PACKS] or [PACKS["en"]]
    for field in ("title", "description", "venue"):
        v = rec.get(field)
        if not v:
            continue
        for pack in packs:
            c = _match(v, pack)
            if c:
                return c
    return "other"
