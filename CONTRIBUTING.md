# Contributing

Kokpit is a personal project that welcomes issues and pull requests. There is no support guarantee and breaking changes are possible before 1.0.

## Run it locally

Python 3.11 or newer, nothing else to install.

```bash
git clone https://github.com/sahindemirbas/kokpit
cd kokpit
./run        # creates kokpit.toml and a starter vault, serves http://127.0.0.1:8501
```

To try it without touching your own notes, point it at a copy of `docs/demo-vault/` and a throwaway database:

```bash
python -m kokpit init --vault /tmp/kokpit-demo/vault --db /tmp/kokpit-demo/demo.db --city "Ghent, Belgium" --name Sam --language en --yes
```

## Run the tests

The same command CI runs on Python 3.11, 3.12 and 3.13 (`.github/workflows/tests.yml`):

```bash
python -m unittest discover -s tests -v
```

Tests use synthetic fixtures and fake servers. Do not add a test that needs the network or a real key.

## Rules for code

- Standard library only. `dependencies = []` in `pyproject.toml` stays empty. Playwright is the one optional extra (`kokpit[browser]`) and nothing may require it.
- Every module keeps a path that works without a model. A model is an opt-in.
- A value that cannot be determined is shown as unknown, never as 0.
- New modules and sources are off by default.

## Add your city

Local event sources are small adapters under `adapters/events/<city>/`. Copy `adapters/events/example-city/`, follow [docs/add-your-city.md](docs/add-your-city.md) and the [adapter contract](adapters/README.md). Category keywords in your language go in a JSON pack, see [docs/category-packs.md](docs/category-packs.md).

## Screens and languages

Every word on screen comes from `kokpit/locales/<code>.json`. When you add or change a key, add it to both `en.json` and `tr.json`; `tests/test_locale.py` fails if the two files do not have the same keys.

## No personal data

Fixtures, screenshots and examples use synthetic data only: no real names, addresses, e-mail addresses, tokens, chat ids or paths from your own machine. Use placeholders such as `Sam`, `Example City` and `/tmp/kokpit-demo/vault`. Never commit `kokpit.toml`, `.env` or a database.

## Issues and pull requests

- Bugs: open an issue with the steps, what you expected, what happened and the output of `python -m kokpit jobs`. Remove any token or private address first.
- A new city or source: open an issue with the city, the source and its terms of use, or send the adapter as a pull request.
- Pull requests: one topic per pull request, tests included, `python -m unittest discover -s tests` green, docs updated when behaviour changes. If you change the README, change `README.tr.md` too.
