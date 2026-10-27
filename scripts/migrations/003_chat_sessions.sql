BEGIN;

CREATE TABLE chat_sessions (
    customer_id BIGINT PRIMARY KEY REFERENCES customers(id) ON DELETE CASCADE,
    nonce BYTEA NOT NULL,
    ciphertext BYTEA NOT NULL,
    key_version INTEGER NOT NULL DEFAULT 1,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at TIMESTAMPTZ NOT NULL,
    CONSTRAINT ck_chat_sessions_key_version
        CHECK (key_version >= 1),
    CONSTRAINT ck_chat_sessions_nonce_length
        CHECK (octet_length(nonce) = 12),
    CONSTRAINT ck_chat_sessions_ciphertext_length
        CHECK (octet_length(ciphertext) >= 16)
);

CREATE INDEX ix_chat_sessions_expires_at ON chat_sessions (expires_at);

COMMIT;
