# Connections

The Connections screen is where a user enables modules and links accounts. It is the only place that touches secrets, and it must make three facts visible for every connection:

1. **What is read** (and, separately, what may be written or published).
2. **Which permission was granted** (scope, token kind, account name).
3. **Last successful update** (time and record count; `0 records` is shown, not hidden).

## Connection catalogue

| Connection | Module | Kind | Read | Write/publish | Notes |
|---|---|---|---|---|---|
| Notes folder | Brain | local path | Markdown files | append-only quick capture | never deletes or rewrites |
| Calendar | Today | ICS URL (working); Google OAuth read-only (planned) | events of today plus the next 6 days | none | read-only scope only; ICS address is a secret |
| City / place | Events, Today | setting | geocode once (Nominatim) | none | radius or neighbouring provinces |
| AllEvents, Eventbrite, Sagramap | Events | public pages | listings | none | slug confirmed by user; rate limits respected |
| Local event adapters | Events | per-city adapter | listings | none | shipped disabled; offline `example-city` template |
| Weather | Today, Events | public API (Open-Meteo) | forecast | none | no key |
| CV file | Career | local path | text of the CV | none | parsing is local; model-assisted parsing opt-in |
| Job adapters | Career | per-source adapter | listings | none | shipped disabled; offline `example-careers` template |
| LinkedIn guest search | Career | public guest pages, no login | listings of the last 7 days | none | OFF by default; see the notice below |
| Instagram | Social | user's own Meta app, Instagram Login | insights, media | publish only with the publish toggle and an approval | token stored locally; separate toggles |
| TikTok stats | Social | local analytics DB | statistics | none | optional, browser-based collector |
| Telegram bot | all | bot token | commands from the owner chat only | sends messages to the owner | single allowed chat id |
| Model API | Agenda, Events, Career | API key | n/a | sends stated text to the provider | each use is listed with the data it sends |
| TTS provider | Agenda | API key | n/a | sends summary text | optional |
| AI tool logs | AI usage | local paths | usage rows | none | Hermes `state.db`, Claude Code, Codex |
| Hermes agent | Brain, AI usage | local | vault files, usage rows | may append notes if enabled | not required; see `integrations/hermes` |

## LinkedIn guest search (off by default)

`[career] linkedin_guest = true` makes the `career` job read LinkedIn's public guest job pages (no login, no account) using the target roles and locations of your profile, with a 7-day window, a pause between requests and a small cap on queries. Read this before switching it on:

- LinkedIn's User Agreement restricts automated access. Use is at your own risk.
- It can break or be blocked at any time. On the first HTTP 429 Kokpit stops, records a warning in Health and keeps what it already fetched. Kokpit makes no promise to bypass blocks and does not try.
- Nothing is sent to LinkedIn except the search words and places from your profile.

## AI usage: local files (off by default)

`[modules] ai_usage = true` plus `[ai_usage] readers = [...]` lets the `ai_usage` job read, read-only, these local files. Each reader is a separate switch:

- `hermes`: the SQLite file `state.db` of the Hermes agent (opened with `mode=ro`); tables `sessions` and `session_model_usage`.
- `claude_code`: `*.jsonl` logs under the Claude Code projects folder; only the model name, token counters, time and session id of assistant messages. One API message can appear on several lines (verified on real logs: 45 usage lines held 33 unique message id + requestId pairs, no id with two requestIds); the reader counts each message once, using the largest value of each counter.
- `codex`: `*.jsonl` logs under the Codex sessions folder; only the model, the cumulative `token_count` totals, time and session id. Field notes: `cached_input_tokens` is a subset of `input_tokens` (checked: `total_tokens` = input + output), so Kokpit stores input minus cached as input and the cached part as cache reads; reasoning tokens are treated as part of output and not added again (evidence: the openai/codex `TokenUsage` struct is filled from the API usage object, where `output_tokens` includes `output_tokens_details.reasoning_tokens` and `total_tokens` is the API total, see codex-rs/ext/goal/tests/accounting.rs in openai/codex: input 100, output 30, reasoning 5, total 135 = input + output; OpenAI's token help page says reasoning tokens count toward output usage and are billed as output; no real local event with reasoning > 0 was available to count, the only real token_count event found on two machines had reasoning 0, so this rests on the source and docs); cache write is always 0 (OpenAI has no cache write charge, the log's `cache_write_input_tokens` is ignored).

Message text, prompts, file contents and project names are never read into Kokpit's database. Session ids are stored as a 12 character sha256 prefix. Nothing leaves the device. The only network request is the opt-in `[ai_usage] price_refresh = true`: one GET of `https://models.dev/api.json` at most every 24 hours, with no user data sent. A missing or unreadable file becomes a Health warning and the other readers still run.

## Secrets

- Stored in a local `.env` (or the OS keyring where available). Never in `kokpit.db`, never in the Markdown vault, never in logs.
- The dashboard shows the account name and scope, never the token.
- A calendar ICS address is a secret: anyone holding it can read the calendar. Keep it in your local `kokpit.toml` or `KOKPIT_CALENDAR_ICS_URLS`; API answers, the health log and error messages show only its scheme and host.
- `kokpit doctor` (planned) checks that each enabled connection has its secret and that the secret still works, and reports the result as pass/fail without printing values.

## Permission separation

- Social media: `analyse` and `publish` are separate switches, both off by default.
- Brain: `read` is required; `quick capture` is a separate switch.
- Model API: each module that can call a model has its own switch, and the Connections screen lists the exact fields it sends (e.g. Events: title + description for classification; Career: skills list + listing text, no identity fields).

## Failure visibility

A connection that fails or returns nothing must say so where the user looks: on the Connections screen and, if the module is in the morning line, in that line. Silent empty results are a bug, not a state.
