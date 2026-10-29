CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    day TEXT NOT NULL,          -- YYYY-MM-DD, local date of the run
    job TEXT NOT NULL,
    started TEXT,               -- ISO timestamp
    finished TEXT,
    ok INTEGER NOT NULL,
    rows INTEGER,               -- records produced; 0 is a signal, not a success
    info TEXT,                  -- JSON object, free form per job
    warnings TEXT               -- JSON list of short strings
);
CREATE INDEX IF NOT EXISTS idx_runs_day ON runs(day);
CREATE INDEX IF NOT EXISTS idx_runs_job ON runs(job, id);
