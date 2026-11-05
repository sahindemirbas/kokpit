# kokpit (Hermes Agent plugin)

Your Kokpit day inside Hermes: calendar, tasks, weather, brain notes, events,
job listings, AI usage and job health as tools, `/kokpit today|brain|health`,
brain context on every turn, and a session card with a receipt written to your
Markdown vault when a session ends, so Claude Code or Codex can continue from
the same place.

Standard library only. No network calls and no model calls; the Kokpit
database is opened read-only, and the only writes are quick capture and the
session card. Without a Kokpit setup it runs on bundled synthetic demo data.

```
ln -s /path/to/kokpit/integrations/hermes/kokpit ~/.hermes/plugins/kokpit
hermes plugins enable kokpit
```

Settings, tools and the install model: [docs/hermes-plugin.md](../../../docs/hermes-plugin.md).
