# Docker

## Quick start

    git clone https://github.com/sahindemirbas/kokpit
    cd kokpit
    docker compose up -d

Open http://127.0.0.1:8501. Two services start from one image: `kokpit` (the dashboard) and `scheduler` (runs the jobs of the enabled modules, see docs/architecture.md, Scheduling). On the first start the entrypoint writes `/data/kokpit.toml` and a starter vault. It never overwrites an existing file.

Set `TZ` (for example `TZ=Europe/Rome docker compose up -d`) so job times follow your local time. Without it the containers use UTC.

## Where the data is

Everything is in the named volume `kokpit-data`, mounted at `/data`: `kokpit.toml`, `kokpit.db`, `vault/`. Read or edit the config by copying it out and back:

    docker compose cp kokpit:/data/kokpit.toml .
    docker compose cp kokpit.toml kokpit:/data/kokpit.toml

Modules are off until you switch them on in that file. The scheduler reads the file at start, so restart the scheduler service after a change. To use your own notes folder, add a bind mount under `volumes:` in `compose.yaml` and set `vault_path` to the path inside the container.

## Secrets

Put them in a `.env` file next to `compose.yaml` (git-ignored, optional), never in `compose.yaml` or a committed file. Supported variables include `KOKPIT_CALENDAR_ICS_URLS`, `KOKPIT_INSTAGRAM_TOKEN` and `KOKPIT_TELEGRAM_TOKEN`. The bot chat id goes in the `[bot]` section of `kokpit.toml`.

## Telegram bot (optional)

    docker compose --profile bot up -d

Optional podcast: the scheduler runs `podcast` at 07:00, after the 06:45 digest (table in docs/architecture.md). With `tts_provider = "gemini"` put `KOKPIT_GEMINI_API_KEY` in `.env`; the episode lands in `/data/audio/`. The image has no ffmpeg, so it serves wav.

Needs `KOKPIT_TELEGRAM_TOKEN` in `.env`, `chat_id` under `[bot]` and `bot = true` under `[modules]`. The `bot` service runs `kokpit bot poll`; the scheduler does not poll.

## Open to your LAN (conscious choice)

Kokpit has no login. The port is published on 127.0.0.1 only. To reach it from other devices on your network:

    KOKPIT_BIND=0.0.0.0 docker compose up -d

Do this only on a network you trust, never on a public address. `KOKPIT_PORT=9000` changes the host port. Inside the container the server listens on 0.0.0.0 through `KOKPIT_HOST`; the config default 127.0.0.1 is unchanged for a normal install.

## Health

`docker compose ps` shows both services as healthy. The dashboard check is an HTTP GET on port 8501. The scheduler check is a heartbeat file (`/data/.scheduler-heartbeat`, touched every 15 s, unhealthy when older than 90 s).

## Update

    git pull
    docker compose up -d --build

## Backup

Stop the services, archive the volume, start again:

    docker compose stop
    docker run --rm -v kokpit-data:/data -v "$PWD":/backup python:3.12-slim tar czf /backup/kokpit-data.tgz -C /data .
    docker compose start
