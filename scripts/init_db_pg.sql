-- Postgres schema for Supabase (replaces SQLite init_db.sql)

CREATE TABLE IF NOT EXISTS movements (
    id                     TEXT PRIMARY KEY,
    date                   DATE NOT NULL,
    description            TEXT NOT NULL,
    amount                 REAL NOT NULL,
    movement_type          TEXT,
    account                TEXT,
    bank                   TEXT NOT NULL,
    raw_blob               TEXT,
    suggested_category     TEXT,
    suggested_subcategory  TEXT,
    confidence             REAL,
    classifier_source      TEXT,
    status                 TEXT NOT NULL DEFAULT 'pendiente',
    final_category         TEXT,
    final_subcategory      TEXT,
    decided_by             TEXT,
    decided_at             TIMESTAMP,
    notified_at            TIMESTAMP,
    inserted_at            TIMESTAMP DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_movements_status ON movements(status);
CREATE INDEX IF NOT EXISTS idx_movements_date   ON movements(date);
CREATE INDEX IF NOT EXISTS idx_movements_bank   ON movements(bank);

CREATE TABLE IF NOT EXISTS rules (
    id           SERIAL PRIMARY KEY,
    match_type   TEXT NOT NULL,
    pattern      TEXT NOT NULL,
    category     TEXT NOT NULL,
    subcategory  TEXT,
    hits         INTEGER DEFAULT 0,
    created_at   TIMESTAMP DEFAULT NOW(),
    last_used_at TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_rules_pattern ON rules(pattern);

CREATE TABLE IF NOT EXISTS telegram_log (
    id         SERIAL PRIMARY KEY,
    direction  TEXT NOT NULL,
    chat_id    TEXT,
    message_id TEXT,
    text       TEXT,
    payload    TEXT,
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS errors (
    id         SERIAL PRIMARY KEY,
    component  TEXT NOT NULL,
    message    TEXT,
    traceback  TEXT,
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS credentials (
    bank       TEXT PRIMARY KEY,
    blob       TEXT NOT NULL,
    updated_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS wizard_state (
    chat_id    TEXT PRIMARY KEY,
    state      TEXT NOT NULL,
    payload    TEXT,
    updated_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS config (
    key        TEXT PRIMARY KEY,
    value      TEXT,
    updated_at TIMESTAMP DEFAULT NOW()
);
