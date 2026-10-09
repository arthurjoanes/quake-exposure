-- Bloqueio e execução de exclusão. Sem leitura para a API.
ALTER TABLE privacy.erase_block ADD COLUMN tenant_id text;
ALTER TABLE privacy.erase_block ADD COLUMN asset_id text;

CREATE TABLE privacy.erasure_request (
    subject_token uuid PRIMARY KEY,
    request_id uuid NOT NULL UNIQUE,
    tenant_id text NOT NULL,
    asset_id text NOT NULL,
    source_epoch bigint NOT NULL,
    status text NOT NULL CHECK (status IN ('BLOCKED', 'PURGED', 'CLOSED')),
    blocked_at timestamptz NOT NULL,
    purged_at timestamptz,
    closed_at timestamptz,
    source_command_id uuid NOT NULL,
    source_command_epoch bigint,
    receipt jsonb
);
REVOKE ALL ON privacy.erasure_request, privacy.erase_block FROM qe_api_read;
CREATE INDEX asset_revision_subject ON portfolio.asset_revision (subject_token);
