"""`kokpit place`: geocoder candidates, source table, slug confirmation, safe --write. No network."""
from __future__ import annotations

import contextlib
import email.message
import io
import json
import sys
import tempfile
import tomllib
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from kokpit import __main__ as cli  # noqa: E402
from kokpit.modules.events import geocode, place  # noqa: E402

ANSWER = {"results": [
    {"name": "Bergamo", "latitude": 45.69601, "longitude": 9.66721, "country_code": "IT", "country": "Italy",
     "admin1": "Lombardy", "admin2": "Province of Bergamo", "population": 121200},
    {"name": "München", "latitude": 48.13743, "longitude": 11.57549, "country_code": "DE", "country": "Germany",
     "admin1": "Bavaria", "population": 1260391},
    {"name": "Reggio nell'Emilia", "latitude": 44.69825, "longitude": 10.63125, "country_code": "IT",
     "country": "Italy", "admin1": "Emilia-Romagna"},
]}

CONFIG = """# my settings
[modules]
today = true
events = false   # switched on later

[place]                       # set by init
city = "Ghent"   # old city
country = "BE"
lat = 51.05
lon = 3.72
radius_km = 25
languages = ["nl", "en"]

[events]
allevents_slug = "gent-be"   # allevents.in/<slug>
eventbrite_slug = "belgium--gent"
adapters = []

[server]
port = 8600
"""


class FakeResponse:
    def __init__(self, body: bytes):
        self.body = body
        self.headers = email.message.Message()
        self.headers["Content-Type"] = "application/json; charset=utf-8"

    def read(self):
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def fake_urlopen(payload):
    def _open(req, timeout=None):
        if isinstance(payload, Exception):
            raise payload
        return FakeResponse(json.dumps(payload).encode())
    return _open


def run_cli(argv, payload):
    out, err = io.StringIO(), io.StringIO()
    with mock.patch("urllib.request.urlopen", fake_urlopen(payload)), \
            contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = cli.main(argv)
    return code, out.getvalue(), err.getvalue()


class SlugTest(unittest.TestCase):
    def test_accents_spaces_apostrophes(self):
        self.assertEqual(geocode.slugify("Reggio nell'Emilia"), "reggio-nell-emilia")
        self.assertEqual(geocode.slugify("München"), "munchen")
        self.assertEqual(geocode.slugify("  São Paulo "), "sao-paulo")
        self.assertEqual(geocode.slugify("Ghent"), "ghent")
        self.assertEqual(geocode.slugify("Sint-Martens-Latem"), "sint-martens-latem")

    def test_block_proposes_slugs_as_comments_until_confirmed(self):
        cand = place.parse(json.dumps(ANSWER))[2]
        b = place.block(cand)
        self.assertIn('# allevents_slug = "reggio-nell-emilia-it"', b)
        self.assertIn('# eventbrite_slug = "italy--reggio-nell-emilia"', b)
        self.assertNotIn('\nallevents_slug', b)
        self.assertIn('\nallevents_slug = "reggio-nell-emilia-it"', place.block(cand, confirm_slug=True))

    def test_sources_by_country(self):
        self.assertEqual(place.sources_for("IT"), {"allevents": "yes", "eventbrite": "yes"})
        self.assertEqual(place.sources_for("DE"), {"allevents": "unverified", "eventbrite": "yes"})
        self.assertEqual(place.sources_for("ZZ"), {"allevents": "unverified", "eventbrite": "unverified"})
        self.assertIn("allevents (unverified), eventbrite (yes)", place.block(place.parse(json.dumps(ANSWER))[1]))


class CliTest(unittest.TestCase):
    def test_lists_candidates(self):
        code, out, _ = run_cli(["place", "Bergamo"], ANSWER)
        self.assertEqual(code, 0)
        self.assertIn("1. Bergamo | Lombardy, Province of Bergamo | IT | 45.696, 9.6672 | pop 121,200", out)
        self.assertIn("2. München | Bavaria | DE", out)
        self.assertIn("pop ?", out)

    def test_pick_prints_block(self):
        code, out, _ = run_cli(["place", "Bergamo", "--pick", "1"], ANSWER)
        self.assertEqual(code, 0)
        self.assertIn('[place]\ncity = "Bergamo"\ncountry = "IT"\nlat = 45.696\nlon = 9.6672', out)
        self.assertIn("nothing written", out)
        self.assertEqual(run_cli(["place", "Bergamo", "--pick", "9"], ANSWER)[0], 2)

    def test_zero_results_and_network_error(self):
        code, _, err = run_cli(["place", "Nowhereville"], {"generationtime_ms": 0.1})
        self.assertEqual(code, 4)
        self.assertIn("no place found", err)
        code, _, err = run_cli(["place", "Bergamo"], urllib.error.URLError("offline"))
        self.assertEqual(code, 3)
        self.assertIn("could not reach the geocoder", err)


class WriteTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.cfg = self.tmp / "kokpit.toml"
        self.cfg.write_text(CONFIG, encoding="utf-8")

    def test_write_keeps_backup_comments_and_other_settings(self):
        code, out, _ = run_cli(["--config", str(self.cfg), "place", "x", "--pick", "1", "--write"], ANSWER)
        self.assertEqual(code, 0, out)
        self.assertEqual((self.tmp / "kokpit.toml.bak").read_text(encoding="utf-8"), CONFIG)
        text = self.cfg.read_text(encoding="utf-8")
        new = tomllib.loads(text)
        self.assertEqual(new["place"]["city"], "Bergamo")
        self.assertEqual((new["place"]["lat"], new["place"]["lon"]), (45.696, 9.6672))
        self.assertEqual(new["place"]["radius_km"], 25)
        self.assertEqual(new["server"]["port"], 8600)
        self.assertEqual(new["modules"], {"today": True, "events": False})
        for kept in ("# my settings", "# switched on later", "# set by init", '"Bergamo"   # old city',
                     "# allevents.in/<slug>"):
            self.assertIn(kept, text)
        # slug not confirmed: active values untouched, proposal left as comments
        self.assertEqual(new["events"]["allevents_slug"], "gent-be")
        self.assertEqual(new["events"]["eventbrite_slug"], "belgium--gent")
        self.assertIn('# allevents_slug = "bergamo-it"  # proposed', text)

    def test_confirm_slug_writes_it_and_drops_proposal(self):
        run_cli(["--config", str(self.cfg), "place", "x", "--pick", "3", "--write"], ANSWER)
        code, _, _ = run_cli(["--config", str(self.cfg), "place", "x", "--pick", "3", "--write", "--confirm-slug"],
                             ANSWER)
        self.assertEqual(code, 0)
        text = self.cfg.read_text(encoding="utf-8")
        new = tomllib.loads(text)
        self.assertEqual(new["place"]["city"], "Reggio nell'Emilia")
        self.assertEqual(new["events"]["allevents_slug"], "reggio-nell-emilia-it")
        self.assertEqual(new["events"]["eventbrite_slug"], "italy--reggio-nell-emilia")
        self.assertEqual(new["events"]["adapters"], [])
        self.assertNotIn("# proposed", text)

    def test_missing_sections_are_added(self):
        self.cfg.write_text("[server]\nport = 8600\n", encoding="utf-8")
        code, _, _ = run_cli(["--config", str(self.cfg), "place", "x", "--pick", "2", "--write"], ANSWER)
        self.assertEqual(code, 0)
        new = tomllib.loads(self.cfg.read_text(encoding="utf-8"))
        self.assertEqual(new["place"]["country"], "DE")
        self.assertEqual(new["server"]["port"], 8600)
        self.assertNotIn("allevents_slug", new.get("events", {}))

    def test_invalid_toml_is_not_touched(self):
        self.cfg.write_text("[place\ncity = 1\n", encoding="utf-8")
        code, _, err = run_cli(["--config", str(self.cfg), "place", "x", "--pick", "1", "--write"], ANSWER)
        self.assertEqual(code, 5)
        self.assertIn("did not write", err)
        self.assertEqual(self.cfg.read_text(encoding="utf-8"), "[place\ncity = 1\n")
        self.assertFalse((self.tmp / "kokpit.toml.bak").exists())


if __name__ == "__main__":
    unittest.main()
