CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,       -- allevents | eventbrite | adapter slug
    source_id TEXT NOT NULL,    -- stable id or URL inside the source
    title TEXT NOT NULL,
    description TEXT,
    start TEXT NOT NULL,        -- ISO 8601 with offset, or YYYY-MM-DD when the source has no time
    end TEXT,
    venue TEXT,
    address TEXT,
    city TEXT,
    lat REAL,
    lon REAL,
    url TEXT,
    image TEXT,
    price TEXT NOT NULL DEFAULT 'unknown',   -- free | paid | unknown
    category TEXT NOT NULL DEFAULT 'other',
    distance_km REAL,           -- from the configured place, null when the record has no coordinates
    language TEXT,
    title_tr TEXT,              -- translation of title into [events] translate_to, null when off
    description_tr TEXT,
    translated_to TEXT,
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    UNIQUE(source, source_id)
);
CREATE INDEX IF NOT EXISTS idx_events_start ON events(start);
