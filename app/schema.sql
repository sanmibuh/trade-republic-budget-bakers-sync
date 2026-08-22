-- SQLite schema for the shared sync database.
-- All statements are idempotent: safe to run on an existing database.

CREATE TABLE IF NOT EXISTS processed_events (
    event_id         TEXT NOT NULL,
    instance         TEXT NOT NULL DEFAULT '',
    event_type       TEXT NOT NULL DEFAULT '',
    event_timestamp  TEXT NOT NULL DEFAULT '',
    amount           TEXT NOT NULL DEFAULT '',
    raw              TEXT NOT NULL DEFAULT '',
    synced_at        TEXT NOT NULL,
    wallet_record_id TEXT,
    PRIMARY KEY (event_id, instance)
);

CREATE INDEX IF NOT EXISTS idx_synced_at ON processed_events (synced_at);

CREATE TABLE IF NOT EXISTS auth_state (
    instance    TEXT PRIMARY KEY,
    status      TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sync_runs (
    instance  TEXT PRIMARY KEY,
    status    TEXT NOT NULL,
    ran_at    TEXT NOT NULL,
    saved     INTEGER NOT NULL DEFAULT 0,
    failed    INTEGER NOT NULL DEFAULT 0,
    excluded  INTEGER NOT NULL DEFAULT 0
);
