"""Locale files and the panel's use of them: same keys in every language, clean values,
every key the page asks for exists, server label words have a translation, missing keys fall back to English."""
import json
import re
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from kokpit import config as cfgmod, wizard  # noqa: E402
from kokpit.modules.events import categories  # noqa: E402
from kokpit.modules.organiser import collect as og_collect  # noqa: E402
from kokpit.modules.today import weather  # noqa: E402

LOC = ROOT / "kokpit" / "locales"
INDEX = (ROOT / "kokpit" / "static" / "index.html").read_text(encoding="utf-8")
EMOJI = re.compile("[\U0001F300-\U0001FAFF\U00002600-\U000027BF\U0001F000-\U0001F2FF]")
PH = re.compile(r"\{(\w+)\}")


def load(code):
    return json.loads((LOC / f"{code}.json").read_text(encoding="utf-8"))


def tkey(x):
    """Same as TKey in index.html: a server word as a key suffix."""
    return re.sub(r"^_|_$", "", re.sub(r"[^a-z0-9]+", "_", str(x).strip().lower()))


class LocaleFileTests(unittest.TestCase):
    def setUp(self):
        self.codes = cfgmod.locale_codes()
        self.d = {c: load(c) for c in self.codes}

    def test_two_languages_same_keys(self):
        self.assertEqual(self.codes, ["en", "tr"])
        self.assertEqual(set(self.d["en"]), set(self.d["tr"]))
        self.assertGreater(len(self.d["en"]), 400)

    def test_values_clean(self):
        for code, d in self.d.items():
            for k, v in d.items():
                with self.subTest(code=code, key=k):
                    self.assertIsInstance(v, str)
                    self.assertTrue(v.strip())
                    self.assertNotIn("\u2014", v)
                    self.assertIsNone(EMOJI.search(v))
                    self.assertRegex(k, r"^(_label|[a-z0-9_]+(\.[a-z0-9_]+)+)$")
        self.assertNotIn("radar", json.dumps(self.d["tr"]).lower())

    def test_placeholders_match(self):
        for k, v in self.d["en"].items():
            with self.subTest(key=k):
                self.assertEqual(set(PH.findall(v)), set(PH.findall(self.d["tr"][k])))

    def test_dates_complete(self):
        en = self.d["en"]
        for i in range(7):
            self.assertIn(f"date.day.{i}", en)
            self.assertIn(f"date.day_short.{i}", en)
        for i in range(12):
            self.assertIn(f"date.month.{i}", en)
            self.assertIn(f"date.month_short.{i}", en)
        for st in ("full", "day", "short", "dm", "month"):
            self.assertIn(f"date.fmt.{st}", en)
        self.assertEqual(self.d["tr"]["date.month_short.9"], "Eki")


class IndexKeyTests(unittest.TestCase):
    def setUp(self):
        self.en = load("en")

    def test_every_static_key_exists(self):
        keys = set(re.findall(r"\bT\('([^']+)'[,)]", INDEX))
        self.assertGreater(len(keys), 200)
        for k in keys:
            with self.subTest(key=k):
                self.assertIn(k, self.en)
        for k in set(re.findall(r"\bTP\('([^']+)'", INDEX)):
            with self.subTest(plural=k):
                self.assertIn(k + ".one", self.en)
                self.assertIn(k + ".other", self.en)

    def test_dynamic_prefixes_exist(self):
        prefixes = set(re.findall(r"\bT\('([^']+\.)'\+", INDEX)) | {p + "." for p in re.findall(r"\bTL\('([^']+)'", INDEX)}
        self.assertIn("status.", prefixes)
        for p in prefixes:
            with self.subTest(prefix=p):
                self.assertTrue(any(k.startswith(p) for k in self.en))

    def test_embedded_english_matches_en_json(self):
        m = re.search(r"const EN=(\{.*?\});/\*EN\*/", INDEX, re.S)
        assert m is not None, "embedded English dictionary not found"
        emb = json.loads(m.group(1))
        want = {k: v for k, v in self.en.items() if not k.startswith("setup.") and k != "_label"}
        self.assertEqual(emb, want)

    def test_number_format_follows_language(self):
        self.assertFalse("toLocaleString('en')" in INDEX)
        self.assertIn("const fmtN=n=>Number(n).toLocaleString(LOC.lang||'en')", INDEX)


class ServerLabelTests(unittest.TestCase):
    """Short words the server sends and the page shows as labels need a key; free text is not listed here."""

    def setUp(self):
        self.en = load("en")

    def has(self, group, word):
        self.assertIn(f"{group}.{tkey(word)}", self.en, word)

    def test_connection_status_words(self):
        src = (ROOT / "kokpit" / "modules" / "connections" / "api.py").read_text(encoding="utf-8")
        words = set(re.findall(r'status = "([^"]+)"', src)) | set(re.findall(r'"status": "([^"]+)"', src))
        words |= set(re.findall(r'"(ok|off|0 records|not run yet|not done|missing|not created yet|not set|needs a place|error)"', src))
        self.assertGreater(len(words), 8)
        for w in words:
            with self.subTest(word=w):
                self.has("status", w)

    def test_connection_names_and_kinds(self):
        src = (ROOT / "kokpit" / "modules" / "connections" / "api.py").read_text(encoding="utf-8")
        for w in re.findall(r'"name": "([^"]+)"', src):
            with self.subTest(name=w):
                self.has("connections.name", w)
        for w in re.findall(r'"kind": "([^"]+)"', src):
            with self.subTest(kind=w):
                self.has("connections.kind", w)

    def test_weather_categories_organiser(self):
        for w in set(weather.CODES.values()) | {"unknown"}:
            with self.subTest(weather=w):
                self.has("weather", w)
        for k, label in categories.LABELS.items():
            with self.subTest(category=k):
                self.assertEqual(self.en[f"events.cat.{k}"], label)
        for k in og_collect.KINDS:
            with self.subTest(kind=k):
                self.has("organiser.kind", k)


class FallbackTests(unittest.TestCase):
    def test_unknown_language_is_english(self):
        self.assertEqual(cfgmod.ui_language("zz"), "en")
        cfg: Any = SimpleNamespace(ui_language="en", user_name="Ada Example")
        view = wizard.locale_view(cfg, "zz")
        self.assertEqual(view["language"], "en")
        self.assertEqual(view["strings"]["common.close"], "Close")
        tr = wizard.locale_view(cfg, "tr")
        self.assertEqual(tr["strings"]["common.close"], "Kapat")

    def test_missing_key_falls_back_to_english(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            (d / "en.json").write_text((LOC / "en.json").read_text(encoding="utf-8"), encoding="utf-8")
            (d / "xx.json").write_text(json.dumps({"_label": "Example", "common.close": "Shut"}), encoding="utf-8")
            with mock.patch.object(cfgmod, "LOCALES_DIR", d):
                self.assertIn("xx", cfgmod.locale_codes())
                s = wizard.strings("xx")
                self.assertEqual(s["common.close"], "Shut")
                self.assertEqual(s["common.loading"], "Loading...")
                self.assertEqual(set(s), set(load("en")))
                self.assertIn({"code": "xx", "label": "Example"}, wizard.languages())

    def test_page_helper_falls_back(self):
        # T(): locale string, then the embedded English, then the key itself
        self.assertIn("function T(k,v){let s=LOC.s[k];if(s==null)s=EN[k];if(s==null)s=k;", INDEX)


if __name__ == "__main__":
    unittest.main()
