CREATE TABLE IF NOT EXISTS briefing_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    day TEXT NOT NULL,          -- digest day, YYYY-MM-DD
    topic TEXT NOT NULL,        -- topic name from [briefing] topics
    url TEXT NOT NULL,
    title TEXT NOT NULL,
    tags TEXT NOT NULL DEFAULT '[]',      -- JSON list, feed categories
    published TEXT,
    score REAL NOT NULL DEFAULT 0,
    headline INTEGER NOT NULL DEFAULT 0,  -- 1 for the top item of its section
    summary TEXT NOT NULL DEFAULT '',
    why TEXT NOT NULL DEFAULT '',
    by_model INTEGER NOT NULL DEFAULT 0,  -- 1 when summary and why came from the model
    project TEXT,               -- project tag from brain-driven matching, null when none
    reason TEXT,                -- "matches project X: keyword Y in thread Z"
    extra TEXT NOT NULL DEFAULT '{}',     -- JSON: measured facts (stars, likes, age_days...) and personal_context titles
    visual TEXT,                -- JSON visual spec from the model after strict validation, null when none
    UNIQUE(day, url)
);
CREATE INDEX IF NOT EXISTS idx_briefing_day ON briefing_items(day, score);
CREATE TABLE IF NOT EXISTS briefing_podcast (
    day TEXT PRIMARY KEY,       -- digest day, YYYY-MM-DD
    engine TEXT NOT NULL,       -- tts provider and model, e.g. "gemini:<model>"
    file TEXT NOT NULL,         -- file name under data/audio, never a path
    seconds REAL NOT NULL DEFAULT 0,
    note TEXT NOT NULL DEFAULT '',        -- e.g. "intro skipped: no weather or calendar data"
    fingerprint TEXT NOT NULL,            -- sha256 of items, context fingerprint, language and minutes
    created TEXT NOT NULL
);
