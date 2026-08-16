-- Create tokens table to store EnergyID bearer tokens
CREATE TABLE IF NOT EXISTS tokens (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    bearer_token TEXT NOT NULL,
    twin_id TEXT NOT NULL,
    exp INTEGER NOT NULL,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);

-- Create index on exp for efficient expiration queries
CREATE INDEX IF NOT EXISTS idx_tokens_exp ON tokens(exp DESC);
