# Category language packs

Event categories have fixed English ids and labels:

| id | label |
|---|---|
| food | Food & festivals |
| concert | Concert |
| opera | Opera |
| theatre | Theatre |
| dance | Dance |
| exhibition | Exhibition |
| cinema | Cinema |
| fair | Fair & market |
| kids | Kids & family |
| sport | Sport |
| talk | Talk |
| other | Other (nothing matched) |

Which words map to which id comes from language packs. Kokpit ships `en` (on by default) and `it` (off until you list it) in `kokpit/modules/events/category_packs/`.

## Config

```toml
[events]
category_languages = ["it", "en"]           # packs to use, tried in this order; default ["en"]
category_pack_dirs = ["~/kokpit-packs"]     # optional: folders with your own <code>.json packs
```

For every event Kokpit checks the title, then the description, then the venue name; for each of those it tries the packs in the listed order. The first match wins. A source hint (`category_hint` in an adapter record) wins over all keywords.

## Pack format

One JSON file per language, named `<code>.json`:

```json
{
  "language": "de",
  "label": "Deutsch",
  "rules": [
    {"category": "kids", "keywords": ["kinder", "familie"]},
    {"category": "opera", "keywords": ["oper"]},
    {"category": "concert", "keywords": ["konzert", "live"]}
  ]
}
```

- `rules` is ordered: inside one pack the first rule that matches wins, so put narrow categories first (opera before concert, kids before theatre).
- Keywords match case-insensitively at the start of a word, so `degustazion` also matches `degustazione`.
- `category` must be one of the ids above (not `other`).

## Adding or overriding a language

1. Copy `en.json` into a folder of your own, rename it to your language code, translate the keywords.
2. Add the folder to `category_pack_dirs` and the code to `category_languages`.
3. Run `python -m kokpit run-job events-collect`; categories are recomputed for every collected event.

A pack in your folder with the same code as a built-in one (for example your own `it.json`) replaces the built-in pack. With several folders, the first listed wins.

## Errors

Nothing here stops the collect job. These show as warnings on the `events-collect` run in Health and the rest goes on:

- a code in `category_languages` with no file: skipped;
- a file that is not valid JSON or has no `rules` list: skipped;
- a rule with an unknown category id or without a keyword list: that rule is skipped, the rest of the pack is used.

If no pack can be loaded at all, the built-in `en` pack is used.

Pull requests with new general-purpose packs are welcome. Keep them free of place, venue and person names: a pack is language data, not a city adapter.
