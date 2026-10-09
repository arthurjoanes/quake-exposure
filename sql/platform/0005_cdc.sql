-- Stage tem dados pessoais: não herda a leitura pública do esquema control.
ALTER DEFAULT PRIVILEGES IN SCHEMA control REVOKE SELECT ON TABLES FROM qe_api_read;

CREATE TABLE control.cdc_transaction (
    transaction_id text PRIMARY KEY,
    expected_count bigint,
    expected_counts jsonb,
    first_seen_at timestamptz NOT NULL,
    ended_at timestamptz,
    applied_at timestamptz,
    waiting_reason text,
    CHECK (expected_count IS NULL OR expected_count >= 0)
);
CREATE INDEX cdc_transaction_pending ON control.cdc_transaction (first_seen_at)
    WHERE applied_at IS NULL;
GRANT SELECT ON control.cdc_transaction TO qe_api_read;

CREATE TABLE control.cdc_health (
    singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
    checked_at timestamptz NOT NULL,
    connector_state text,
    task_state text,
    slot_active boolean,
    retained_wal_bytes bigint,
    last_error text
);
GRANT SELECT ON control.cdc_health TO qe_api_read;

CREATE TABLE control.cdc_stage (
    transaction_id text NOT NULL REFERENCES control.cdc_transaction,
    total_order bigint NOT NULL CHECK (total_order > 0),
    collection text NOT NULL CHECK (collection IN ('public.asset_interval', 'public.portfolio_clock')),
    payload jsonb,
    payload_sha256 text NOT NULL,
    staged_at timestamptz NOT NULL,
    PRIMARY KEY (transaction_id, total_order)
);
REVOKE ALL ON control.cdc_stage FROM qe_api_read;

-- O bloqueio precede o purge; um replay antigo não pode recriar o titular.
CREATE SCHEMA privacy;
GRANT USAGE ON SCHEMA privacy TO qe_pipeline;
ALTER DEFAULT PRIVILEGES IN SCHEMA privacy
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO qe_pipeline;
ALTER DEFAULT PRIVILEGES IN SCHEMA privacy GRANT USAGE ON SEQUENCES TO qe_pipeline;
CREATE TABLE privacy.erase_block (
    subject_token uuid PRIMARY KEY,
    blocked_at timestamptz NOT NULL DEFAULT now()
);
