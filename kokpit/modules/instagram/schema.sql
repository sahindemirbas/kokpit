-- Instagram analysis snapshots (ig_plan below is the content calendar). Analysis never stores captions, usernames or media files.
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
CREATE TABLE IF NOT EXISTS ig_plan (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    due_at TEXT NOT NULL,          -- UTC, YYYY-MM-DDTHH:MM:SS
    kind TEXT NOT NULL,            -- story | post | reel | carousel
    owner TEXT,                    -- who prepares it
    caption TEXT,
    media_urls TEXT,               -- JSON list of public URLs
    collabs TEXT,                  -- JSON list of usernames
    status TEXT NOT NULL,          -- planned | ready | approved | published | failed
    error TEXT,                    -- the platform's message for a failed slot
    media_id TEXT,
    permalink TEXT,
    created_at TEXT,
    updated_at TEXT,
    approved_at TEXT,
    published_at TEXT
);
CREATE INDEX IF NOT EXISTS ig_plan_due ON ig_plan(due_at);
