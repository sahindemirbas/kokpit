# Add your city

Two levels. The first works today; the second has a contract but no loader yet.

## 1. Generic sources (works now, no code)

```
python -m kokpit init --vault ~/notes --db ./kokpit.db --city "Ghent, Belgium"
```

`init` geocodes the name once (OpenStreetMap Nominatim), writes `kokpit.toml` and guesses two public listing slugs from the English city name, here `ghent-be` and `belgium--ghent`.

The guess is a starting point. Both sites list Ghent under its Dutch name, so the slugs that work are:

```toml
[events]
allevents_slug  = "gent-be"        # allevents.in/<slug>
eventbrite_slug = "belgium--gent"  # eventbrite.<tld>/d/<slug>/
```

Switch `events = true` in `[modules]`, run `python -m kokpit run-job events-collect`, open the Connections tab. A wrong slug is not an error: it shows as `0 rows` (allevents.in sends an unknown city to its generic search page). Fix the slug in the browser address bar of the real listing page and paste it into `kokpit.toml`.

Weather needs nothing: Open-Meteo takes the coordinates from `init`.

If the generic slugs cover your town, you are done. Many towns under ~50k people have no AllEvents page; that is what level 2 is for.

## 2. A local adapter

Theatres, municipalities and tourism offices have no city parameter, so each one is a small adapter:

```
adapters/events/<your-city>/
  adapter.toml   # name, country, city, homepage, schedule, terms_reviewed, maintainer
  collect.py     # def collect(settings: dict) -> list[dict]
```

`collect()` returns event records in the JSON contract in [adapters/README.md](../adapters/README.md): `source`, `source_id`, `title`, `start` (ISO 8601 with offset, or a plain date; never an invented time), `url`, `price` (`free | paid | unknown`), and the optional fields. `settings` already carries `lat`, `lon`, `country`, `province`, `radius_km`, so do not hard-code them.

Optional module constants: `NAME` (stored as `source`, defaults to the folder name), `CITY`, `COUNTRY`, `ENABLED_BY_DEFAULT = False`. `adapter.toml` is optional metadata; the loader does not read it yet.

Enable and run it:

```toml
[events]
adapters = ["<your-city>"]
adapter_dirs = []   # or a folder of your own outside the repository
```

```
python -m kokpit adapters
python -m kokpit run-job events-collect
```

A failing adapter shows up as a warning on the events-collect run in Health; the other sources keep working. Start from the offline template in `adapters/events/example-city/`.

## Contributing one

- One directory per source, not per city aggregate. `name` is a unique slug.
- Read the source's terms; set `terms_reviewed = true` only after that. Scrape public listing pages, never anything behind a login.
- Return `[]` when the page is genuinely empty; raise on a broken selector so Health shows the failure instead of a silent zero.
- Open a PR titled `adapter: events/<city> <source>` with one screenshot of the listing page the records came from. Tests are welcome but not required for a first adapter.

