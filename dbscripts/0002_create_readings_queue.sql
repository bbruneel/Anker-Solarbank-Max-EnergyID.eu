-- Offline reading queue: store webhook payloads until flushed / pruned by retention
CREATE TABLE IF NOT EXISTS readings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts INTEGER NOT NULL,
    payload_json TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    sent_at INTEGER
);

CREATE INDEX IF NOT EXISTS idx_readings_pending_ts ON readings(sent_at, ts);
CREATE INDEX IF NOT EXISTS idx_readings_ts ON readings(ts);

-- Single-row sync metadata for upload rate limiting and cached hello policy
CREATE TABLE IF NOT EXISTS sync_state (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    last_successful_upload_at INTEGER,
    hello_upload_interval_seconds INTEGER,
    updated_at INTEGER NOT NULL
);

INSERT OR IGNORE INTO sync_state (id, last_successful_upload_at, hello_upload_interval_seconds, updated_at)
VALUES (1, NULL, NULL, 0);
