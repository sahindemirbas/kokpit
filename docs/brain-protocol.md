# Brain protocol: one vault, many agents

The second brain is a folder of Markdown files owned by the user. It is not a
Kokpit database and it is not tied to one AI tool. Several agents (the reference
instance uses Hermes, Claude Code, Codex and Antigravity) read and write the same
folder, and Kokpit renders it. This document is the contract that makes that
possible. Any tool that follows the file conventions below takes part; no
agent is required.

Status: the conventions are in daily use in the reference instance; the
packaged hook scripts and the night organiser are **planned** for this
repository (see `docs/roadmap.md`).

## 0. Origin and credit

The vault conventions, the per-message context hook, the `Last-Session` card,
receipts and the "update, never rewrite" rule come from **Avenox Beyin**
(`github.com/avenoxai/avenoxbeyin`, MIT licence, copyright Avenox). The
reference instance runs a local fork of Beyin 3.11.0 that has diverged since
October 2026 (multi-agent hooks, Turkish-language rules, the night organiser,
the agenda link). The night organiser and the brain-driven agenda are Kokpit
additions, not part of upstream.

What this means for the repository: this document is our own text and
describes a contract, not Avenox's code. If a hook script or any other code
derived from Beyin is packaged here (planned for M1), it ships with the
original MIT copyright and licence text, and the derived files say so in their
header. Kokpit's own licence is MIT (`LICENSE`), which is compatible with
that obligation; see `THIRD_PARTY_NOTICES.md`.

## 1. Vault layout (starter structure)

Kokpit ships an empty starter vault. The author's own vault is never shipped.

```
vault/
  Threads.md            active threads: where we left off, open items
  To-Do.md              ## Open with checkbox lines, optional "due: YYYY-MM-DD"
  Last-Session.md       short card written at the end of a work session
  inbox/                quick captures, unsorted; the night organiser files them
  notes/<area>/         filed notes (areas chosen by the user)
  projects/<name>/      per-project rules, decisions, logs
  people/               optional
  ideas/                ideas are not projects and not tasks
```

Obsidian-compatible: wikilinks `[[path/Note]]`, YAML frontmatter, plain
Markdown. Nothing proprietary.

## 2. Who writes what

| Actor | Reads | Writes | Never |
|---|---|---|---|
| Context hook (any agent) | Threads, Last-Session, relevant notes | nothing | writes |
| Working agent (Hermes, Claude Code, Codex, ...) | whole vault | Last-Session card, Threads update, the note the work belongs to, a receipt line | rewrites old text, deletes |
| Night organiser (scheduled) | `inbox/`, today's changed notes, unreviewed notes | moves inbox notes to a folder, adds links, appends dated status blocks | edits text meaning, touches files outside its journal |
| Kokpit web app | whole vault | quick capture only (append a line to `inbox/` or `To-Do.md`) | rewrites, deletes |
| User | everything | everything | - |

### Context hook

Runs as a separate process on every agent message, so a change does not need
an agent restart. It injects a compact context block (open threads, last
session, matching notes) and a short reminder when a meaningful piece of work
appears finished. It writes nothing. The same script serves every agent; each
tool just points its hook mechanism at it.

### Session close (working agent)

When a meaningful piece of work ends, the agent writes without asking:

1. `Last-Session.md`: what was done, what was verified, what is open. Hard
   size limit; the card is a summary, not a log.
2. `Threads.md`: the thread's "where we left off" and open items. Each open
   item is tagged with who owes the next step (`on you`, `on me`, `on <name>`).
   Only items under the open heading reach the Kokpit "waiting on you" lane.
3. The project or topic note the work belongs to: dated paragraph, sources
   named.
4. A receipt line (session id, files touched). Receipts let a later agent see
   what an earlier one did.

Size limits for `Last-Session.md` and `Threads.md` are shared constants between
the hook and the Kokpit reader. If one changes, the other changes.

### Dates

- `due: YYYY-MM-DD` on an open task becomes an upcoming date in the Today tab.
- A date in a log or a source citation is not a plan; it is ignored.
- A time is never invented from a date.
- Calendar events are a separate connection. A plan found in a note is not
  written to the calendar automatically; that needs explicit approval per
  item.

## 3. Night organiser (planned module)

A scheduled job, default 04:00 local time, that uses a language model and
therefore appears in the Connections screen as an opt-in with the model
provider, the exact data sent (note bodies from `inbox/` and today's changed
notes) and the last successful run.

Steps, in order, each with its own journal and recovery:

1. **Preflight**: recover any half-finished operation from the journal.
2. **Categorise**: move each `inbox/` note into a folder that fits its
   meaning, give it a filename that fits the content, fix spelling without
   changing meaning. Original bytes are kept in a backup outside the vault;
   frontmatter is kept as is.
3. **Link**: add reciprocal wikilinks between notes that genuinely relate.
   Each link carries a reason type: `Condition`, `Continuation`,
   `Preparation`, `Shared goal (interpretation)`. No keyword matching, no
   quota of links per note. Older notes are read-only here except for a
   one-way link.
4. **Conditions**: if a new note states that a condition in an older note has
   come true, append a dated status block to the older note with a link to the
   source. Earlier text is not changed. Ambiguous evidence is not resolved
   automatically.
5. **Enrich**: fold new information into the note it belongs to (a course
   detail into the course note, a status change into the project note); split
   standalone ideas into `ideas/` and people into `people/`. An idea is not an
   active project and not a task.
6. **Sync**: run the vault's own sync if one is configured; report conflicts,
   never resolve them silently.

Morning report, default 07:00: a deterministic summary of what the night job
did (moved, linked, updated, skipped, errors). No model call. Delivered to the
Kokpit Today tab and, if the bot module is on, to Telegram.

Guarantees the implementation must keep: external backup before any write,
durable journal per step, re-running the same plan produces no change,
partial failure is reported and recoverable, multi-file writes are not
globally atomic and the docs say so.

## 4. Brain-driven agenda

The Agenda module can read the brain to decide what matters today instead of
using a fixed topic list.

- Input: active threads, project names and rules, stated goals and current
  blockers from `Threads.md` and `projects/*/`. Only titles, tags and short
  summaries leave the vault; full note bodies do not.
- Matching: each agenda item (news, release, podcast episode, event) gets
  project tags when it clearly relates to an active thread. The tag shows the
  reason ("matches project X: keyword Y in thread Z"). Items without a clear
  reason get no tag; they are not hidden, just untagged.
- Output: the digest groups tagged items first, then the user's standing
  topics. The audio summary follows the same order.
- Boundaries: the brain is read-only for this module. The matching step may
  use a model; if it does, the Connections screen states which fields are
  sent. A rule-based fallback (title and tag overlap) must exist so the module
  works without any model.

Status: live in the reference instance since October 2026 as a first version;
**generalise** for this repository (the project list and goals are currently
read from the author's vault paths).

## 5. What this protocol is not

- Not a sync service. The vault lives where the user puts it (local disk,
  Syncthing, Obsidian Sync, Git). Kokpit never uploads it.
- Not a dependency on any agent. A vault kept by hand follows the same
  contract and renders the same.
- Not a memory store for the agents' own configuration. Agent settings stay in
  their own homes; the vault holds the user's knowledge.
