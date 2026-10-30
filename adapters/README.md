# Adapters

An adapter turns one data source into Kokpit records. Community adapters live here, one directory per source, and are disabled until the user enables them on the Connections screen.

```
adapters/
  events/<city-or-source>/   e.g. events/example-city/ (offline template, disabled by default)
  jobs/<source>/             e.g. jobs/accenture/
```

Each adapter directory contains `collect.py` (required; the event loader imports it only when the id is listed in `[events] adapters`) and optionally `adapter.toml` (metadata, not read by the loader yet). `collect.py` may set `NAME` (becomes `source`; defaults to the folder name), `CITY`, `COUNTRY`, `ENABLED_BY_DEFAULT = False`, and exposes one function:

```python
def collect(settings: dict) -> list[dict]:
    """Return normalised records. Raise on hard failure; return [] when the source is genuinely empty."""
```

`settings` carries the user's place (`lat`, `lon`, `country`, `province`, `radius_km`, `provinces`) and language, so an adapter never hard-codes them.

## adapter.toml

```toml
name = "example-city-riverside-hall"    # unique slug
kind = "events"                          # events | jobs
scope = "city"                           # city | country | global
country = "XX"                           # required for city/country scope
city = "Example City"
homepage = "https://example.org/events"
schedule = "daily"                       # daily | twice-daily | weekly
terms_reviewed = false                   # set true after reading the source's terms
maintainer = "github-handle"
```

## Event record

```json
{
  "source": "example-city-riverside-hall",
  "source_id": "stable id or URL",
  "title": "string",
  "description": "string or null",
  "start": "2026-11-05T20:30:00+01:00",
  "end": null,
  "venue": "Riverside Hall",
  "address": "1 River Street",
  "city": "Example City",
  "province": "EX",
  "lat": null,
  "lon": null,
  "url": "https://example.org/events/autumn-concert",
  "image": null,
  "price": "free | paid | unknown",
  "category_hint": "opera",
  "language": "en"
}
```

Rules: `start` is ISO 8601 with offset; a date without time is `YYYY-MM-DD` (never invent a time). `price` unknown stays `unknown`. `category_hint` is optional and must be one of the English category ids (see [docs/category-packs.md](../docs/category-packs.md)); without it the core classifier decides.

## Job record

Job adapters live in `adapters/jobs/<source>/collect.py` (or in a folder listed in `[career] adapter_dirs`) and are loaded like event adapters: off until the id is listed in `[career] adapters`, discovery imports nothing, a broken adapter becomes a Health warning and never stops the others, `kokpit adapters` lists them. `settings` carries the profile targets: `target_roles`, `locations`, `remote_pref`, `languages`, `country`. The offline template is `adapters/jobs/example-careers/` (fictional company, fixed data).

```json
{
  "external_id": "stable id inside the source",
  "title": "string",
  "company": "string",
  "location": "string or null",
  "remote": "remote | hybrid | onsite | unknown",
  "url": "https://...",
  "posted_at": "2026-10-28",
  "description": "string or null",
  "language": "en"
}
```

`source` is set by the loader from `NAME`. Only `title` is required; a record without one is dropped. `posted_at` is `YYYY-MM-DD` or null when the source gives no date (never invent one). `remote` unknown stays `unknown`. A listing is identified by `source` plus `external_id`; the same company and normalised title from two sources is kept as one row.

## Health

The core records row count and outcome for each adapter run. An adapter that returns `[]` is shown as "last successful update: 0 records", so a broken selector is visible rather than silent.

## Contribution rules

- No credentials in adapters. If a source needs login, the adapter declares a connection in `adapter.toml` and reads the secret from settings.
- Respect robots and rate limits; one request per page, cached where the source allows.
- Set `terms_reviewed = true` only after reading the source's terms; adapters with `false` carry a notice on the Connections screen.
- Fixtures for tests must be synthetic or reduced to structure only.
