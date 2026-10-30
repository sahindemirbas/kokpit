CREATE TABLE IF NOT EXISTS agenda_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    day TEXT NOT NULL,          -- digest day, YYYY-MM-DD
    topic TEXT NOT NULL,        -- topic name from [agenda] topics
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
    UNIQUE(day, url)
);
CREATE INDEX IF NOT EXISTS idx_agenda_day ON agenda_items(day, score);
