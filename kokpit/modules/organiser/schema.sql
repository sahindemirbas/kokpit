CREATE TABLE IF NOT EXISTS organiser_proposals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,         -- categorise | link | conditions | enrich | sync | todo
    path TEXT NOT NULL,         -- note path relative to the vault ("" for vault-wide items)
    target TEXT NOT NULL DEFAULT '',  -- other note or thread, when the proposal links two things
    detail TEXT NOT NULL DEFAULT '',  -- short machine key that makes the proposal unique
    text TEXT NOT NULL,         -- the suggestion, one sentence
    status TEXT NOT NULL DEFAULT 'open',  -- open | gone (cause disappeared) | dismissed | done (decided by the user)
    first_seen TEXT NOT NULL,   -- YYYY-MM-DD
    last_seen TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'rule',  -- rule | model
    reason TEXT NOT NULL DEFAULT '',      -- model proposals: why (for link: one of the four relation types)
    evidence TEXT NOT NULL DEFAULT '',    -- model proposals: exact quote from the source note
    UNIQUE(kind, path, target, detail)
);
CREATE INDEX IF NOT EXISTS idx_organiser_status ON organiser_proposals(status, kind);
CREATE TABLE IF NOT EXISTS organiser_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
