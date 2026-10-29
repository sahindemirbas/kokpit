"""Category language packs: built-in en/it, merging, user override, broken packs. No network."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from kokpit import config as cfgmod  # noqa: E402
from kokpit.modules.events import categories  # noqa: E402


class CategoryPacks(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)

    def tearDown(self):
        self._td.cleanup()

    def _pack(self, d: Path, code: str, rules, raw: str | None = None) -> Path:
        d.mkdir(parents=True, exist_ok=True)
        p = d / f"{code}.json"
        p.write_text(raw if raw is not None else json.dumps({"language": code, "rules": rules}), encoding="utf-8")
        return p

    def test_default_is_english(self):
        cfg = cfgmod.load(cfgmod.write_default(self.tmp / "kokpit.toml"))
        self.assertEqual(cfg.events["category_languages"], ["en"])
        self.assertEqual(cfg.events["category_pack_dirs"], [])
        packs, warns = categories.load_packs()
        self.assertEqual((len(packs), warns), (1, []))
        self.assertEqual(categories.classify({"title": "Jazz concert"}, packs=packs), "concert")
        self.assertEqual(categories.classify({"title": "Sagra della zucca"}, packs=packs), "other")
        self.assertEqual(set(categories.LABELS), set(categories.CATEGORIES))

    def test_italian_pack_when_enabled(self):
        packs, warns = categories.load_packs(["it"])
        self.assertEqual(warns, [])
        self.assertEqual(categories.classify({"title": "Sagra della zucca"}, packs=packs), "food")
        self.assertEqual(categories.classify({"title": "Mostra fotografica"}, packs=packs), "exhibition")

    def test_merge_in_order(self):
        packs, _ = categories.load_packs(["en", "it"])
        self.assertEqual(categories.classify({"title": "Book launch"}, packs=packs), "talk")
        self.assertEqual(categories.classify({"title": "Fiera del libro"}, packs=packs), "fair")
        # same title, both packs match: the first listed language wins
        self.assertEqual(categories.classify({"title": "Workshop ballo"}, packs=packs), "talk")
        packs_it_first, _ = categories.load_packs(["it", "en"])
        self.assertEqual(categories.classify({"title": "Workshop ballo"}, packs=packs_it_first), "dance")

    def test_user_pack_overrides_builtin_and_adds_language(self):
        user = self.tmp / "packs"
        self._pack(user, "en", [{"category": "sport", "keywords": ["concert"]}])
        self._pack(user, "de", [{"category": "concert", "keywords": ["konzert"]}])
        packs, warns = categories.load_packs(["en", "de"], [user])
        self.assertEqual(warns, [])
        self.assertEqual(categories.classify({"title": "Jazz concert"}, packs=packs), "sport")
        self.assertEqual(categories.classify({"title": "Konzert im Park"}, packs=packs), "concert")

    def test_broken_missing_and_unknown_are_warnings(self):
        user = self.tmp / "packs"
        self._pack(user, "xx", None, raw="{ not json")
        self._pack(user, "fr", [{"category": "brunch", "keywords": ["brunch"]},
                                {"category": "cinema", "keywords": "film"},
                                {"category": "concert", "keywords": ["concert"]}])
        packs, warns = categories.load_packs(["xx", "fr", "zz"], [user])
        self.assertEqual(len(packs), 1)  # only fr survives, with one rule
        self.assertEqual(packs[0], [("concert", ["concert"])])
        joined = "\n".join(warns)
        self.assertIn("xx.json: unreadable", joined)
        self.assertIn("unknown category 'brunch'", joined)
        self.assertIn("rule 2 needs a list", joined)
        self.assertIn("'zz': not found", joined)
        # nothing usable at all: fall back to en instead of crashing
        packs, warns = categories.load_packs(["xx"], [user])
        self.assertEqual(categories.classify({"title": "Jazz concert"}, packs=packs), "concert")
        self.assertTrue(warns)

    def test_config_reads_lists(self):
        p = cfgmod.write_default(self.tmp / "kokpit.toml")
        text = p.read_text().replace('category_languages = ["en"]', 'category_languages = ["it", "en"]')
        text = text.replace("category_pack_dirs = []", 'category_pack_dirs = ["packs"]')
        p.write_text(text)
        cfg = cfgmod.load(p)
        self.assertEqual(cfg.events["category_languages"], ["it", "en"])
        self.assertEqual(cfg.events["category_pack_dirs"], ["packs"])


if __name__ == "__main__":
    unittest.main()
