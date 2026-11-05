# kokpit (Hermes Agent plugin)

Your Kokpit day inside Hermes: calendar, tasks, weather, brain notes, events,
job listings, AI usage and job health as tools, `/kokpit today|brain|health`,
brain context on every turn, and a session card with a receipt written to your
Markdown vault when a session ends, so Claude Code or Codex can continue from
the same place. The same data shows up as a Kokpit tab in `hermes dashboard`
and as a right pane with a status chip in the desktop app. Two optional
modules, off by default, watch training runs (`kokpit_training`) and the
local machine (`kokpit_server`).

Standard library only. No network calls and no model calls; the Kokpit
database is opened read-only, and the only writes are quick capture and the
session card. Vault files can live anywhere in the vault (settings or safe
discovery by name).

## Try it in one minute

Without any Kokpit setup the plugin runs on bundled synthetic demo data
(vault, database, training logs, server numbers), marked "Demo data".

```
ln -s /path/to/kokpit/integrations/hermes/kokpit ~/.hermes/plugins/kokpit
hermes plugins enable kokpit
hermes chat        # /kokpit today, "what is on me?", "is my training run stuck?"
hermes dashboard   # Kokpit tab
```

Settings, vault layout, tools, the dashboard, the desktop pane and the install
model: [docs/hermes-plugin.md](../../../docs/hermes-plugin.md).
