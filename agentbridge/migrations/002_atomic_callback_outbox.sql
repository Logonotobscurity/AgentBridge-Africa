BEGIN;

-- Generic reconciliation fields; no provider-specific columns enter the core ledger.
ALTER TABLE payment_transactions
    ADD COLUMN IF NOT EXISTS provider_receipt VARCHAR(160),
    ADD COLUMN IF NOT EXISTS retry_count INTEGER NOT NULL DEFAULT 0 CHECK (retry_count >= 0),
    ADD COLUMN IF NOT EXISTS max_retries INTEGER NOT NULL DEFAULT 5 CHECK (max_retries >= 0),
    ADD COLUMN IF NOT EXISTS next_reconcile_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS last_reconcile_error_code VARCHAR(64);

CREATE UNIQUE INDEX IF NOT EXISTS uq_confirmed_provider_receipt
    ON payment_transactions (provider, provider_receipt)
    WHERE status = 'CONFIRMED' AND provider_receipt IS NOT NULL;

-- Database enforcement mirrors agentbridge.core.payment_lifecycle.ALLOWED_TRANSITIONS.
CREATE OR REPLACE FUNCTION enforce_payment_status_transition()
RETURNS TRIGGER AS $$
BEGIN
    IF OLD.status IS NOT DISTINCT FROM NEW.status THEN
        RETURN NEW;
    END IF;

    IF NOT (
        (OLD.status = 'DRAFT' AND NEW.status IN ('PENDING_APPROVAL', 'FAILED')) OR
        (OLD.status = 'PENDING_APPROVAL' AND NEW.status IN ('SUBMITTED', 'FAILED')) OR
        (OLD.status = 'SUBMITTED' AND NEW.status IN ('CALLBACK_RECEIVED', 'CONFIRMED', 'FAILED')) OR
        (OLD.status = 'CALLBACK_RECEIVED' AND NEW.status IN ('CONFIRMED', 'FAILED'))
    ) THEN
        RAISE EXCEPTION 'invalid payment status transition: % -> %', OLD.status, NEW.status
            USING ERRCODE = 'check_violation';
    END IF;

    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_payment_status_transition ON payment_transactions;
CREATE TRIGGER trg_payment_status_transition
BEFORE UPDATE OF status ON payment_transactions
FOR EACH ROW EXECUTE FUNCTION enforce_payment_status_transition();

CREATE TABLE IF NOT EXISTS payment_reconciliation_outbox (
    job_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    transaction_id UUID NOT NULL REFERENCES payment_transactions(transaction_id),
    webhook_event_pk UUID REFERENCES payment_webhook_events(event_pk),
    status VARCHAR(16) NOT NULL DEFAULT 'PENDING'
        CHECK (status IN ('PENDING', 'PROCESSING', 'COMPLETED', 'DEAD')),
    attempts INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    max_attempts INTEGER NOT NULL DEFAULT 8 CHECK (max_attempts > 0),
    available_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    locked_at TIMESTAMPTZ,
    locked_by VARCHAR(128),
    last_error_code VARCHAR(64),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMPTZ,
    CONSTRAINT uq_outbox_webhook_event UNIQUE (webhook_event_pk)
);

CREATE INDEX IF NOT EXISTS idx_reconciliation_outbox_claim
    ON payment_reconciliation_outbox (available_at, created_at)
    WHERE status IN ('PENDING', 'PROCESSING');

COMMIT;
