CREATE TABLE IF NOT EXISTS ai_usage_rows (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tool TEXT NOT NULL,            -- hermes | claude_code | codex
    model_raw TEXT NOT NULL,       -- model id as the tool wrote it
    model TEXT NOT NULL,           -- normalised model id (prices are looked up by this)
    session_hash TEXT NOT NULL,    -- first 12 hex chars of sha256(session id); the id itself is never stored
    place TEXT NOT NULL DEFAULT '',-- where the session came from (cron, cli, exec, ...)
    started_at TEXT,               -- UTC ISO seconds
    last_seen TEXT,                -- UTC ISO seconds
    input_tokens INTEGER NOT NULL DEFAULT 0,        -- excludes cache reads
    output_tokens INTEGER NOT NULL DEFAULT 0,
    cache_read_tokens INTEGER NOT NULL DEFAULT 0,
    cache_write_tokens INTEGER NOT NULL DEFAULT 0,
    UNIQUE(tool, session_hash, model_raw)
);
CREATE INDEX IF NOT EXISTS idx_aiu_seen ON ai_usage_rows(last_seen);
CREATE TABLE IF NOT EXISTS ai_usage_readers (
    tool TEXT PRIMARY KEY,
    last_run TEXT,
    status TEXT NOT NULL,          -- ok | error | missing | empty
    error TEXT,
    rows INTEGER NOT NULL DEFAULT 0,
    last_data TEXT,                -- newest last_seen among the rows
    source_mtime TEXT              -- newest modification time of the source files (UTC ISO)
);
