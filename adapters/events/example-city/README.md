# example-city: adapter template

A working, offline template. It returns two synthetic events and touches no network.

## Write an adapter for your city

1. Copy this folder: `adapters/events/<city>-<source>/` (for a private adapter, put it under a folder of your own and list that folder in `[events] adapter_dirs`).
2. In `collect.py` set `NAME` (unique slug, stored as `source`), `CITY`, `COUNTRY`.
3. Replace the body of `collect(settings)` with code that reads one public listing page and returns records in the contract of [adapters/README.md](../../README.md). `settings` carries `city`, `country`, `lat`, `lon`, `radius_km`, `languages`, `language`; do not hard-code them. Standard library only (`kokpit.fetch` is available).
4. Enable it in `kokpit.toml`:

   ```toml
   [events]
   adapters = ["<city>-<source>"]
   ```

5. Check with `python -m kokpit adapters`, then `python -m kokpit run-job events-collect`. Failures (import error, exception, wrong return type) show up as a warning on the events-collect run in Health; the other sources keep working.

Rules: `source_id` stable per event; `start` ISO 8601 with offset or `YYYY-MM-DD`; `price` is `free | paid | unknown`; records with coordinates outside `radius_km` are dropped, records without coordinates are kept.
