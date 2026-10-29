# Kokpit

A self-hosted personal cockpit: one small web dashboard that gathers the things you check every morning, runs on your own device, and reads only the data you connect to it.

**Status: design and documentation stage.** This repository currently contains the product design, module specifications, privacy model and roadmap. It does not yet contain an installable application. The design is extracted from a working single-user instance that has run daily on a Raspberry Pi since 2025; see [docs/status.md](docs/status.md) for what exists today, what still has to be generalised, and what is only planned.

Turkish version: [README.tr.md](README.tr.md)

## What it is for

You open one page in the morning and see:

- **Today**: calendar, open tasks, upcoming dates, decisions people are waiting on from you, weather for the places you care about.
- **Second brain**: your own local Markdown notes (projects, open threads, "where did I leave this"), with quick capture of notes and tasks. Notes stay plain files that Obsidian or any editor can open.
- **Career**: a skills/experience/language profile extracted from your CV and corrected by you, plus your target roles, locations and remote preferences; job listings found and matched with a written reason, not just a percentage.
- **Events**: what is happening in your city and nearby, with category and free/paid filters, list, calendar and map views, and a per-event calendar file.
- **Social media**: statistics for your own accounts, content comparison and calendar, drafts and reminders. Publishing only on supported platforms and only after explicit approval.
- **AI usage**: per tool, model and session; limits and reset times where the provider exposes them; data freshness shown. Real API spend, subscription fees and list-price equivalents are kept apart.
- **Agenda**: a daily digest of news in the areas you follow, optionally as a short audio summary.
- **System health**: last successful update per connection, failed jobs, broken links.

Everything is a module. You enable what you want from a Connections screen; nothing else runs.

## Principles

1. **Your device, your data.** Kokpit runs locally (a Raspberry Pi, a home server, a laptop). Nothing is sent to a hosted service of ours; there is none.
2. **Every connection is visible.** For each enabled connection you can see what data is read, which permission was granted, and when it last succeeded.
3. **Analysis and publishing are separate permissions.** Reading your Instagram statistics never implies the right to post.
4. **No silent AI calls.** If a module sends data to an external model, the data and the destination are stated and approved first. CV matching does not need your name, phone or e-mail.
5. **Unknown is not zero.** A price we cannot determine, a quota a provider does not expose, a measurement that failed: these are shown as unknown, never as 0.
6. **Honest labels.** Features are marked as working, needs generalisation, or planned. The docs never describe a command that does not exist as if it did.

## Repository layout

```
README.md               this file
README.tr.md            Turkish version
docs/
  architecture.md       runtime shape, data flow, storage
  modules.md            every module: purpose, inputs, connections, status
  brain-protocol.md     one vault shared by several AI agents; night organiser; brain-driven agenda
  connections.md        the Connections screen, permissions, secrets handling
  privacy.md            what never enters this repo or leaves your device
  status.md             working / needs generalisation / planned
  roadmap.md            milestones and the pre-public checklist
adapters/
  README.md             JSON contract for community data adapters (events, jobs)
integrations/
  hermes/README.md      optional integration with the Hermes agent (not required)
config/
  kokpit.example.toml   example configuration, no personal data
```

## Planned installation

Not available yet. The target is:

```
pip install kokpit        # or: docker compose up
kokpit init               # asks for city, CV path, target roles, modules to enable
kokpit run                # local dashboard on http://localhost:8501
```

These commands are a design target, not a current feature.

## Licence

Not decided yet. The licence will be chosen after reviewing third-party code and data-source terms; until then the repository is private and all rights are reserved.

## Author

Built by [Sahin Demirbas](https://github.com/sahindemirbas), a data professional based in Italy, for his own daily use first.
