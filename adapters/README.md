# Adapters

An adapter turns one data source into Kokpit records. Community adapters live here, one directory per source, and are disabled until the user enables them on the Connections screen.

```
adapters/
  events/<city-or-source>/   e.g. events/cremona/ (example, disabled by default)
  jobs/<source>/             e.g. jobs/accenture/
```

Each adapter directory contains `adapter.toml` (metadata) and `collect.py` exposing one function:

```python
def collect(settings: dict) -> list[dict]:
    """Return normalised records. Raise on hard failure; return [] when the source is genuinely empty."""
```

`settings` carries the user's place (`lat`, `lon`, `country`, `province`, `radius_km`, `provinces`) and language, so an adapter never hard-codes them.

## adapter.toml

```toml
name = "cremona-teatro-ponchielli"      # unique slug
kind = "events"                          # events | jobs
scope = "city"                           # city | country | global
country = "IT"                           # required for city/country scope
city = "Cremona"
homepage = "https://..."
schedule = "daily"                       # daily | twice-daily | weekly
terms_reviewed = false                   # set true after reading the source's terms
maintainer = "github-handle"
```

## Event record

```json
{
  "source": "cremona-teatro-ponchielli",
  "source_id": "stable id or URL",
  "title": "string",
  "description": "string or null",
  "start": "2026-11-05T20:30:00+01:00",
  "end": null,
  "venue": "string or null",
  "address": "string or null",
  "city": "Cremona",
  "province": "CR",
  "lat": null,
  "lon": null,
  "url": "https://...",
  "image": null,
  "price": "free | paid | unknown",
  "category_hint": "opera",
  "language": "it"
}
```

Rules: `start` is ISO 8601 with offset; a date without time is `YYYY-MM-DD` (never invent a time). `price` unknown stays `unknown`. `category_hint` is optional; the core classifier decides.

## Job record

```json
{
  "source": "accenture",
  "source_id": "stable id",
  "title": "string",
  "company": "string",
  "location": "string or null",
  "remote": "remote | hybrid | onsite | unknown",
  "posted": "2026-10-28",
  "url": "https://...",
  "description": "string or null",
  "language": "en"
}
```

## Health

The core records row count and outcome for each adapter run. An adapter that returns `[]` is shown as "last successful update: 0 records", so a broken selector is visible rather than silent.

## Contribution rules

- No credentials in adapters. If a source needs login, the adapter declares a connection in `adapter.toml` and reads the secret from settings.
- Respect robots and rate limits; one request per page, cached where the source allows.
- Set `terms_reviewed = true` only after reading the source's terms; adapters with `false` carry a notice on the Connections screen.
- Fixtures for tests must be synthetic or reduced to structure only.
