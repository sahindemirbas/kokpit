# Welcome to your Kokpit vault

This folder is your own cockpit for Hermes. It is plain Markdown: any editor,
Obsidian, Claude Code or Codex can read and write it. Nothing here leaves your
machine.

What each file is for:

- Threads.md: one heading per ongoing piece of work. "Where we left off" says
  where it stands, "Open:" lists who owes the next step (on you, on me).
- To-Do.md: checkbox tasks under `## Open`. Add `(due: YYYY-MM-DD)` for a date.
- Last-Session.md: a short card per agent session, newest first. Hermes writes
  or updates one when a session produced a result (a capture, a file written,
  an accepted proposal, or `/kokpit done <summary>`), so the next session (in
  any agent) knows where you stopped. Chat-only sessions write nothing.
- receipts/: one short receipt per result, pointing at the notes it touched.
- inbox/: quick notes from `/kokpit note <text>`, sent from any Hermes chat.
- daily/: one file per day with journal lines from `/kokpit j <text>`.
- news/: Markdown news items; the night pass matches them to your threads.
- notes/: your longer notes. Link them from Threads.md with [[notes/name]].

Try next: `/kokpit todo <text>`, `/kokpit done <summary>`, `/kokpit night run`, `/kokpit brief`.
You can rewrite or delete this note; Kokpit never edits it.
