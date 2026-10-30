-- Bot state: update offset, counters, last poll outcome. Never stores message text, names or tokens.
CREATE TABLE IF NOT EXISTS bot_state (
    k TEXT PRIMARY KEY,
    v TEXT
);
