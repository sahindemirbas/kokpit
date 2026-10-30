CREATE TABLE IF NOT EXISTS job_listings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,        -- linkedin | adapter name
    external_id TEXT NOT NULL,   -- stable id inside the source
    title TEXT NOT NULL,
    company TEXT,
    location TEXT,
    remote TEXT NOT NULL DEFAULT 'unknown',   -- remote | hybrid | onsite | unknown
    url TEXT,
    posted_at TEXT,              -- YYYY-MM-DD or null when the source gives no date
    description TEXT,
    language TEXT,
    norm_key TEXT,               -- normalised company|title, used to merge the same job from two sources
    sources TEXT NOT NULL DEFAULT '',   -- every source that listed it, comma separated
    status TEXT NOT NULL DEFAULT 'open',   -- open | closed (reopened is shown through reopened_at)
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    closed_at TEXT,
    reopened_at TEXT,
    checked_at TEXT,             -- last posting-page check (LinkedIn only)
    score INTEGER,               -- null = could not be judged
    reasons TEXT NOT NULL DEFAULT '[]',
    UNIQUE(source, external_id)
);
CREATE INDEX IF NOT EXISTS idx_job_norm ON job_listings(norm_key);
CREATE INDEX IF NOT EXISTS idx_job_status ON job_listings(status);
CREATE TABLE IF NOT EXISTS career_weekly (   -- one market note per ISO week, written by the career job when [model.uses] career_weekly is on
    week TEXT PRIMARY KEY,       -- YYYY-Www
    start TEXT NOT NULL,
    end TEXT NOT NULL,
    created TEXT NOT NULL,
    model TEXT,
    stats TEXT NOT NULL DEFAULT '{}',
    text TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS career_skills (   -- the user's mark per skill gap: learning | known | skip
    skill TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    note TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL
);
