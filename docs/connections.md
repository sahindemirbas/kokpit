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
| Telegram bot | all | bot token | commands from the owner chat only | sends messages to the owner | single allowed chat id; off by default, see below |
| Model API | Briefing, Events, Career | API key (environment) | n/a | sends stated text to the provider | off by default; each use is listed with the data it sends; see below |
| TTS provider | Briefing | API key (environment) | n/a | sends the digest summary text | off by default (`[briefing.tts] provider`); key named by `key_env`, never in the toml; shown on this screen |
| AI tool logs | AI usage | local paths | usage rows | none | Hermes `state.db`, Claude Code, Codex |
| Hermes agent | Brain, AI usage | local | vault files, usage rows | may append notes if enabled | not required; see `integrations/hermes` |

## Model API (off by default)

Any OpenAI-compatible chat-completions endpoint. In `kokpit.toml` set `[model] enabled = true`, `base_url`, `model`, and switch on single uses under `[model.uses]` (`events_translate`, `events_classify`, `digest`, `night`, `career_weekly`; all false by default). The key is read from the environment variable named by `key_env` (default `KOKPIT_MODEL_API_KEY`) and is never written to the toml, the database or the screen. A use that is off, or a missing key, stops the call before any network traffic. The Connections row shows which uses are on, the fields each one sends, the host, and the last successful call; every call (and failure) is a row in the `runs` table under `model:<use>`, so Health shows it.

Events translation: `[events] translate_to = "en"` (empty = off) translates title and description when `events_translate` is on. Only those two fields are sent; translations are cached per event, and the original text is kept if the call fails.

## Telegram bot (off by default)

1. In Telegram open a chat with BotFather, send `/newbot` and follow the questions. BotFather answers with the bot token.
2. Give the token to Kokpit in the environment (`KOKPIT_TELEGRAM_TOKEN`, preferred) or as `token` under `[bot]` in the git-ignored `kokpit.toml`.
3. Open a chat with your new bot and send it any message.
4. Find your chat id: with the token in a shell variable, open `https://api.telegram.org/bot<token>/getUpdates` in a browser or with curl after step 3; the number at `message.chat.id` is your chat id. Keep that URL private, it contains the token.
5. Put the number in `chat_id` under `[bot]`, set `bot = true` under `[modules]`.
6. Run `python -m kokpit bot send-test`, then `python -m kokpit bot poll --once` after sending `/help` to the bot.

Only that one chat is answered. Anything from other chats is ignored and counted. Schedule `python -m kokpit bot poll --once` every minute with cron (or keep `python -m kokpit bot poll` running), and `python -m kokpit run-job bot-morning` at the time you want the morning line.

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

## Instagram analysis (off by default)

Kokpit reads your own account through the official Instagram API with Instagram Login (`graph.instagram.com`, GET requests only). It never publishes from this job.

Setup, once:

1. You need an Instagram business or creator account (a personal account has no insights API).
2. In the Meta developer portal create an app, add the product "Instagram" with "API setup with Instagram login", and add your account as an Instagram tester or app role holder (development mode is enough for your own account).
3. Request two permissions: `instagram_business_basic` and `instagram_business_manage_insights`.
4. Generate a token for the account in the portal and exchange it for a long-lived token (valid 60 days).
5. Give the token to Kokpit in one of these ways (first match wins): the token file written by `python -m kokpit instagram refresh-token`, the environment variable `KOKPIT_INSTAGRAM_TOKEN`, or `token = "..."` under `[instagram]` in `kokpit.toml`. The environment variable is preferred; `kokpit.toml`, the token file and `data/` are git-ignored. The token is never printed, logged or shown on the dashboard, and it is removed from every error message.
6. Set `instagram = true` under `[modules]` and `analyse = true` under `[instagram]`. `publish` is a separate switch, off by default, and unused by the analysis job.

What is read: account fields (follower and media count), account insights (views, reach, profile views, accounts engaged, interactions), recent posts and reels (up to `media_limit`) with views, reach, likes, comments, saves, shares, interactions and, for reels, average watch time, and active stories with views, reach, replies, shares, interactions. Captions, usernames and media files are not stored; the table keeps the media type, time, link and numbers.

Schedule: stories expire after 24 hours, so run the job twice a day, for example `0 7,19 * * * cd /path/to/kokpit && python -m kokpit instagram collect`. Snapshots are kept per account and day, per media and day; a second run on the same day updates that day's row.

Errors: every call is separate. A metric the API rejects for a media type (unsupported, deprecated) is stored in the errors table and shown on the Social tab; the rest of the run continues. An authentication error (expired token, missing permission), a rate limit or a network error stops that run at once, with no retry loop, keeps what was already stored, and appears as a warning in Health and Connections.

Token refresh: `python -m kokpit instagram refresh-token` calls the `refresh_access_token` endpoint only when you run it, writes the new token to the token file (mode 600) and prints the new expiry date, not the token. The API refuses tokens younger than 24 hours or already expired; then the old token stays in place. Refresh before day 60.

Commands: `python -m kokpit instagram collect`, `python -m kokpit instagram summary` (counts and totals only, no media ids or links).

## Instagram publishing (off by default)

Publishing is a separate switch from the analysis: `publish = true` under `[instagram]` (and `instagram = true` under `[modules]`). It needs one more permission on the same token, `instagram_business_content_publish`; generate the token again with it ticked. Without the switch, the Approve button is disabled and the API answers 403.

How it works: you add a slot in the Social tab (content calendar), mark it ready, and press Approve. Only then Kokpit reads `content_publishing_limit`, creates a media container, polls its status every `poll_seconds` (default 3) until FINISHED or `poll_timeout` (default 120 seconds), and calls `media_publish`. Nothing publishes from a timer; the daily job `instagram-plan` (`python -m kokpit run-job instagram-plan`) only warns. If something goes wrong (platform error, container error, timeout, quota used up) the slot becomes failed with the platform's message; Kokpit never retries by itself, and Retry is another approval. After a timeout check the account before retrying, because the platform may still finish the container.

Media must be a public URL that Instagram's servers can fetch (JPEG for images; MP4 or MOV for video). Where you host it is your choice: any file host, a bucket, or a tunnel such as cloudflared to a folder on your own machine. Kokpit does not host or upload media and does not require any particular service. Collaborators (up to 3 usernames) are sent in the `collaborators` field for posts, reels and carousels; the invitees still have to accept.

What the API cannot do, so Kokpit does not pretend to: add library music to a reel or story, add stickers (polls, questions, links, countdowns), pin a post or a comment, edit the caption or media of a post after publishing, post to a personal account, schedule on Instagram's side (Kokpit keeps the plan and you approve it), and some story features such as interactive elements. Posts are limited per 24 hours (the quota is read before each publish). A reel is posted with `share_to_feed`.

Test it first on a spare account: the first real publish is the only way to confirm your app role, permission and media URL work together.


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
