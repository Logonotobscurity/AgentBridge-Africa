BEGIN;

CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS payment_transactions (
    transaction_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id VARCHAR(128) NOT NULL,
    idempotency_key VARCHAR(128) NOT NULL,
    provider VARCHAR(32) NOT NULL,
    provider_reference VARCHAR(128),
    amount NUMERIC(18, 4) NOT NULL CHECK (amount > 0),
    currency VARCHAR(3) NOT NULL CHECK (currency ~ '^[A-Z]{3}$'),
    status VARCHAR(32) NOT NULL DEFAULT 'DRAFT',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_payment_idempotency UNIQUE (idempotency_key),
    CONSTRAINT uq_payment_provider_reference UNIQUE (provider, provider_reference),
    CONSTRAINT chk_payment_status CHECK (
        status IN ('DRAFT', 'PENDING_APPROVAL', 'SUBMITTED',
                   'CALLBACK_RECEIVED', 'CONFIRMED', 'FAILED')
    )
);

CREATE INDEX IF NOT EXISTS idx_payment_run_id
    ON payment_transactions (run_id);
CREATE INDEX IF NOT EXISTS idx_payment_pending_reconciliation
    ON payment_transactions (updated_at)
    WHERE status IN ('SUBMITTED', 'CALLBACK_RECEIVED');

CREATE TABLE IF NOT EXISTS payment_webhook_events (
    event_pk UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    provider VARCHAR(32) NOT NULL,
    event_id VARCHAR(160) NOT NULL,
    provider_reference VARCHAR(128) NOT NULL,
    payload_sha256 CHAR(64) NOT NULL,
    payload JSONB NOT NULL,
    received_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_payment_webhook_event UNIQUE (provider, event_id)
);

CREATE INDEX IF NOT EXISTS idx_webhook_provider_reference
    ON payment_webhook_events (provider, provider_reference);

COMMIT;
