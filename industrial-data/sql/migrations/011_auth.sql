-- Accounts, sessions and an append-only, hash-chained audit log.
CREATE TABLE IF NOT EXISTS users (
    user_id             BIGSERIAL PRIMARY KEY,
    username            TEXT NOT NULL UNIQUE CHECK (username ~ '^[a-z0-9][a-z0-9._-]{2,63}$'),
    display_name        TEXT,
    role                TEXT NOT NULL CHECK (role IN ('viewer', 'analyst', 'admin')),
    password_hash       TEXT NOT NULL,
    active              BOOLEAN NOT NULL DEFAULT TRUE,
    must_change_password BOOLEAN NOT NULL DEFAULT FALSE,
    failed_logins       INTEGER NOT NULL DEFAULT 0,
    locked_until        TIMESTAMPTZ,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_login_at       TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS sessions (
    token_hash   TEXT PRIMARY KEY,                -- sha256 of the cookie value; the cookie itself is never stored
    user_id      BIGINT NOT NULL REFERENCES users (user_id) ON DELETE CASCADE,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at   TIMESTAMPTZ NOT NULL,            -- absolute limit
    last_seen    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    ip           TEXT,
    user_agent   TEXT
);
CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions (user_id);

-- Every row carries the SHA-256 of (previous row's hash + its own canonical content): editing or deleting a row breaks the chain
-- from that point on, which `GET /api/admin/audit/verify` and `python -m backend.app.users verify-audit` detect.
CREATE TABLE IF NOT EXISTS audit_log (
    audit_id   BIGSERIAL PRIMARY KEY,
    at         TIMESTAMPTZ NOT NULL,
    actor      TEXT NOT NULL,
    role       TEXT,
    action     TEXT NOT NULL,
    target     TEXT,
    detail     JSONB,
    ip         TEXT,
    prev_hash  TEXT NOT NULL,
    row_hash   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_audit_at ON audit_log (at DESC);
CREATE INDEX IF NOT EXISTS idx_audit_action ON audit_log (action, audit_id DESC);

CREATE OR REPLACE FUNCTION audit_log_append_only() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'audit_log is append-only';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS audit_log_no_update ON audit_log;
CREATE TRIGGER audit_log_no_update BEFORE UPDATE OR DELETE ON audit_log
    FOR EACH ROW EXECUTE FUNCTION audit_log_append_only();
DROP TRIGGER IF EXISTS audit_log_no_truncate ON audit_log;
CREATE TRIGGER audit_log_no_truncate BEFORE TRUNCATE ON audit_log
    FOR EACH STATEMENT EXECUTE FUNCTION audit_log_append_only();
