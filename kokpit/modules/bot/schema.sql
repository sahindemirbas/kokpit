-- Bot state: update offset, counters, last poll outcome. Never stores message text, names or tokens.
CREATE TABLE IF NOT EXISTS bot_state (
    k TEXT PRIMARY KEY,
    v TEXT
);

-- One row per reminder sent for an Instagram slot: mode is buttons (ready) or plain (planned). Ids and times only.
CREATE TABLE IF NOT EXISTS bot_reminder (
    slot_id INTEGER NOT NULL,
    mode TEXT NOT NULL,
    message_id INTEGER,
    sent_at TEXT NOT NULL,
    PRIMARY KEY (slot_id, mode)
);
