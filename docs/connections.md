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
| Job sources | Career | public pages, adapters | listings | none | LinkedIn guest search without login |
| Instagram | Social | user's own Meta app, Instagram Login | insights, media | publish only with the publish toggle and an approval | token stored locally; separate toggles |
| TikTok stats | Social | local analytics DB | statistics | none | optional, browser-based collector |
| Telegram bot | all | bot token | commands from the owner chat only | sends messages to the owner | single allowed chat id |
| Model API | Agenda, Events, Career | API key | n/a | sends stated text to the provider | each use is listed with the data it sends |
| TTS provider | Agenda | API key | n/a | sends summary text | optional |
| AI tool logs | AI usage | local paths | usage rows | none | Hermes `state.db`, Claude Code, Codex |
| Hermes agent | Brain, AI usage | local | vault files, usage rows | may append notes if enabled | not required; see `integrations/hermes` |

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
