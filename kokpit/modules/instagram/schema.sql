-- Instagram analysis snapshots. Captions, usernames and media files are never stored.
CREATE TABLE IF NOT EXISTS ig_account (
    day TEXT PRIMARY KEY,          -- UTC date of the snapshot (one row per day, last collect of the day wins)
    followers INTEGER,
    media_count INTEGER,
    reach INTEGER,
    views INTEGER,
    profile_views INTEGER,
    accounts_engaged INTEGER,
    interactions INTEGER,
    collected_at TEXT
);
CREATE TABLE IF NOT EXISTS ig_media (
    media_id TEXT PRIMARY KEY,
    media_type TEXT,               -- IMAGE | VIDEO | CAROUSEL_ALBUM
    product TEXT,                  -- FEED | REELS | STORY
    posted_at TEXT,                -- UTC ISO timestamp from the API
    permalink TEXT
);
CREATE TABLE IF NOT EXISTS ig_media_snap (
    day TEXT NOT NULL,
    media_id TEXT NOT NULL,
    views INTEGER,
    reach INTEGER,
    saved INTEGER,
    shares INTEGER,
    likes INTEGER,
    comments INTEGER,
    interactions INTEGER,
    replies INTEGER,
    avg_watch_ms INTEGER,
    collected_at TEXT,
    PRIMARY KEY (day, media_id)
);
CREATE TABLE IF NOT EXISTS ig_errors (
    scope TEXT NOT NULL,           -- account | media | stories | stop
    name TEXT NOT NULL,            -- metric or call name
    message TEXT,
    at TEXT
);
CREATE TABLE IF NOT EXISTS ig_meta (
    k TEXT PRIMARY KEY,
    v TEXT
);
