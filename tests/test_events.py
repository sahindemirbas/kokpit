"""Events module on fixture pages: no network, no real city."""
from __future__ import annotations

import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from kokpit import config as cfgmod  # noqa: E402
from kokpit.modules.events import api, categories, geocode, sources, store  # noqa: E402
from kokpit.modules.today import weather  # noqa: E402

PAGE = """<html><head>
<script type="application/ld+json">[
 {"@type":"Event","name":"Sagra della zucca","url":"https://x.test/a?ref=1","startDate":"2099-01-10","endDate":"2099-01-11",
  "location":{"@type":"Place","name":"Piazza Grande","address":{"addressLocality":"Testville","streetAddress":"Via Roma 1 26900"},
  "geo":{"latitude":45.01,"longitude":9.01}},"offers":{"price":"0"}},
 {"@type":"MusicEvent","name":"Concerto jazz","url":"https://x.test/b","startDate":"2099-01-12T21:00:00+01:00",
  "location":{"@type":"Place","name":"Teatro","geo":{"latitude":46.5,"longitude":9.0}},"offers":{"price":"12"}},
 {"@type":"Event","name":"No coords talk","url":"https://x.test/c","startDate":"2099-01-13","description":"Presentazione del libro"},
 {"@type":"Event","name":"No date","url":"https://x.test/d"}
]</script></head><body></body></html>"""


def _cfg(tmp: Path, events=True) -> cfgmod.Config:
    place = {"city": "Testville", "country": "IT", "lat": 45.0, "lon": 9.0, "display": "Testville, Italy"}
    p = cfgmod.write_default(tmp / "kokpit.toml", vault=str(tmp / "vault"), db=str(tmp / "k.db"), place=place,
                             slugs={"allevents_slug": "testville-it", "eventbrite_slug": "italy--testville"})
    cfg = cfgmod.load(p)
    assert cfg.has_place and cfg.modules["events"] is True
    assert cfg.place["languages"] == ["it", "en"]
    return cfg


def test_jsonld_parse_and_radius():
    recs = [r for r in (sources.record(e, "allevents", "it") for e in sources.jsonld_events(PAGE)) if r]
    assert [r["title"] for r in recs] == ["Sagra della zucca", "Concerto jazz", "No coords talk"]
    a = recs[0]
    assert a["source_id"] == "https://x.test/a" and a["price"] == "free" and a["address"] == "Via Roma 1"
    assert a["city"] == "Testville" and a["venue"] == "Piazza Grande" and a["start"] == "2099-01-10"
    assert recs[1]["price"] == "paid" and recs[1]["start"] == "2099-01-12T21:00:00+01:00"
    kept = sources.within(recs, 45.0, 9.0, 40)
    assert [r["title"] for r in kept] == ["Sagra della zucca", "No coords talk"]  # 46.5 is ~167 km away
    assert kept[0]["distance_km"] == 1.4 and kept[1]["distance_km"] is None


def test_categories_it_en():
    assert categories.classify({"title": "Sagra della zucca"}, ["it", "en"]) == "sagra"
    assert categories.classify({"title": "Concerto jazz"}, ["it"]) == "concert"
    assert categories.classify({"title": "Serata", "description": "Presentazione del libro"}, ["it"]) == "talk"
    assert categories.classify({"title": "Laboratorio per bambini a teatro"}, ["it"]) == "kids"
    assert categories.classify({"title": "Book launch"}, ["en"]) == "talk"
    assert categories.classify({"title": "Qualcosa"}, ["it"]) == "other"
    assert categories.classify({"title": "x", "category_hint": "opera"}, ["it"]) == "opera"


def test_collect_upsert_and_view():
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        cfg = _cfg(tmp)
        fetchers = {"allevents": lambda slug, lang: [r for r in (sources.record(e, "allevents", lang) for e in sources.jsonld_events(PAGE)) if r],
                    "eventbrite": lambda slug, country, lang: []}
        conn = sqlite3.connect(tmp / "k.db"); conn.row_factory = sqlite3.Row
        rows, info, warns = api.collect(cfg, conn, fetchers)
        assert rows == 2 and info["sources"] == {"allevents": 2, "eventbrite": 0}
        assert warns == ["eventbrite: 0 records (check the slug on the Connections screen)"]
        rows2, _, _ = api.collect(cfg, conn, fetchers)  # idempotent
        assert rows2 == 2 and conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 2
        evs = store.upcoming(conn, days=5, today="2099-01-09")
        assert [e["category"] for e in evs] == ["sagra", "talk"]
        assert store.upcoming(conn, days=5, today="2099-01-11")[0]["title"] == "Sagra della zucca"  # multi-day still listed
        assert store.counts(conn, today="2099-02-01")["upcoming"] == 0
        conn.close()
        v = api.view(cfg, days=14)
        assert v["enabled"] and v["configured"] and v["place"]["city"] == "Testville" and v["counts"]["total"] == 2
        assert v["events"] == []  # 2099 is beyond the 14-day window; the table still has them
        ev = api.one(cfg, 1)
        assert ev and ev["title"] == "Sagra della zucca" and api.one(cfg, 999) is None
        text = store.ics(ev)
        assert "DTSTART;VALUE=DATE:20990110" in text and "DTEND;VALUE=DATE:20990112" in text


def test_collect_source_error_is_warning():
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d); cfg = _cfg(tmp)
        def boom(*a):
            raise OSError("network down")
        conn = sqlite3.connect(tmp / "k.db"); conn.row_factory = sqlite3.Row
        rows, info, warns = api.collect(cfg, conn, {"allevents": boom, "eventbrite": boom})
        assert rows == 0 and info["sources"] == {"allevents": 0, "eventbrite": 0}
        assert warns[0].startswith("allevents: OSError: network down")


def test_no_place_means_off():
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        cfg = cfgmod.load(cfgmod.write_default(tmp / "kokpit.toml", vault=str(tmp / "v"), db=str(tmp / "k.db")))
        assert not cfg.has_place and cfg.modules["events"] is False
        v = api.view(cfg)
        assert v == {"enabled": False, "configured": False, "events": [], "counts": {"total": 0, "upcoming": 0, "by_source": {}}}


def test_geocode_parse_and_slugs():
    body = '[{"lat":"45.31","lon":"9.50","name":"Lodi","display_name":"Lodi, Lombardia, Italia",' \
           '"address":{"city":"Lodi","county":"Lodi","country":"Italy","country_code":"it"}}]'
    p = geocode.parse(body)
    assert p["city"] == "Lodi" and p["country"] == "IT" and p["lat"] == 45.31
    assert geocode.propose_slugs(p) == {"allevents_slug": "lodi-it", "eventbrite_slug": "italy--lodi"}
    assert geocode.parse("[]") is None and geocode.parse("nope") is None


def test_readonly_reader_without_events_table():
    """The web app opens the db read-only; if only another job created it, events must read as empty, not crash."""
    import tempfile, pathlib
    d = pathlib.Path(tempfile.mkdtemp()); db = d / "k.db"
    sqlite3.connect(db).close()
    ro = sqlite3.connect(f"file:{db}?mode=ro", uri=True); ro.row_factory = sqlite3.Row
    assert store.upcoming(ro) == [] and store.counts(ro)["total"] == 0
    ro.close()


def test_weather_parse_and_cache():
    body = '{"timezone":"Europe/Rome","current":{"temperature_2m":12.3,"weather_code":61},' \
           '"daily":{"time":["2099-01-01","2099-01-02"],"weather_code":[61,0],"temperature_2m_max":[14,15],' \
           '"temperature_2m_min":[7,6],"precipitation_sum":[3.2,0],"precipitation_probability_max":[80,5]}}'
    w = weather.parse(body)
    assert w["current"]["text"] == "light rain" and w["days"][1]["text"] == "clear" and w["days"][0]["rain_pct"] == 80
    conn = sqlite3.connect(":memory:"); conn.row_factory = sqlite3.Row
    assert weather.latest(conn) is None
    weather.save(conn, 45.0, 9.0, w, now="2000-01-01T00:00:00")
    got = weather.latest(conn)
    assert got["stale"] is True and got["days"][0]["max"] == 14 and len(got["days"]) == 2


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn(); print("ok", name)


def test_verify_slugs_picks_first_slug_with_records():
    p = {"city": "Cremona", "country": "IT", "country_name": "Italy", "languages": ["it"]}
    slugs = geocode.propose_slugs(p)
    seen = []
    fetchers = {"allevents": lambda slug, lang: (seen.append(slug), [{"x": 1}] if slug == "cremona" else [])[1],
                "eventbrite": lambda slug, country, lang: (_ for _ in ()).throw(OSError("down"))}
    out, counts = geocode.verify_slugs(p, slugs, fetchers)
    assert seen == ["cremona-it", "cremona"]
    assert out == {"allevents_slug": "cremona", "eventbrite_slug": "italy--cremona"}
    assert counts == {"allevents": 1, "eventbrite": -1}
