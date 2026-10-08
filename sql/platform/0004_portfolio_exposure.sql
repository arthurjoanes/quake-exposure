-- Carteira, cálculo de exposição e publicação.
-- A carteira entra por loader direto (M3) e, depois, por CDC. O subject_token é dado pessoal
-- pseudonimizado: fica em portfolio.asset_revision, nunca em exposure nem serving.

CREATE SCHEMA portfolio;
CREATE SCHEMA exposure;
CREATE SCHEMA serving;

GRANT USAGE ON SCHEMA portfolio, exposure, serving TO qe_pipeline, qe_api_read;
ALTER DEFAULT PRIVILEGES IN SCHEMA portfolio, exposure, serving
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO qe_pipeline;
ALTER DEFAULT PRIVILEGES IN SCHEMA portfolio, exposure, serving
    GRANT USAGE ON SEQUENCES TO qe_pipeline;
-- a API lê exposure e serving; da carteira, só a lista de tenants
ALTER DEFAULT PRIVILEGES IN SCHEMA exposure, serving GRANT SELECT ON TABLES TO qe_api_read;

CREATE TABLE portfolio.tenant (
    tenant_id text PRIMARY KEY,
    name text NOT NULL,
    portfolio_epoch bigint NOT NULL DEFAULT 0,  -- +1 por comando de carteira
    source_epoch bigint NOT NULL DEFAULT 1,     -- muda num reset da origem
    privacy_epoch bigint NOT NULL DEFAULT 0     -- +1 por exclusão de titular
);
GRANT SELECT ON portfolio.tenant TO qe_api_read;

-- Uma linha por versão de intervalo, com a epoch em que ficou conhecida (as-of).
CREATE TABLE portfolio.asset_revision (
    tenant_id text NOT NULL REFERENCES portfolio.tenant,
    asset_interval_id text NOT NULL,
    row_version int NOT NULL CHECK (row_version >= 1),
    asset_id text NOT NULL,
    subject_token uuid NOT NULL,
    known_epoch bigint NOT NULL,
    source_epoch bigint NOT NULL,
    valid_from timestamptz NOT NULL,
    valid_to timestamptz,
    lon double precision NOT NULL CHECK (lon BETWEEN -180 AND 180),
    lat double precision NOT NULL CHECK (lat BETWEEN -90 AND 90),
    insured_value_minor bigint NOT NULL CHECK (insured_value_minor >= 0),
    currency text NOT NULL CHECK (currency IN ('BRL', 'USD')),
    is_deleted boolean NOT NULL,
    updated_at timestamptz NOT NULL,
    PRIMARY KEY (tenant_id, asset_interval_id, row_version),
    CHECK (valid_to IS NULL OR valid_to > valid_from)
);
CREATE INDEX asset_revision_epoch ON portfolio.asset_revision (tenant_id, known_epoch);

-- A versão atual de cada intervalo. Vigência sem sobreposição por ativo.
CREATE TABLE portfolio.asset_interval (
    tenant_id text NOT NULL REFERENCES portfolio.tenant,
    asset_interval_id text NOT NULL,
    asset_id text NOT NULL,
    row_version int NOT NULL,
    valid_from timestamptz NOT NULL,
    valid_to timestamptz,
    is_deleted boolean NOT NULL,
    PRIMARY KEY (tenant_id, asset_interval_id),
    EXCLUDE USING gist (
        tenant_id WITH =, asset_id WITH =, tstzrange(valid_from, valid_to) WITH &&
    ) WHERE (NOT is_deleted)
);

-- Um cálculo por vetor de entrada (calculation_id = SHA-256 do vetor canônico).
CREATE TABLE exposure.calculation (
    calculation_id text PRIMARY KEY CHECK (calculation_id ~ '^[0-9a-f]{64}$'),
    stream_scope control.stream_scope NOT NULL,
    earthquake_uid uuid NOT NULL,
    event_semantic_sha256 text NOT NULL,
    event_revision_id text NOT NULL,
    product_revision_id text,
    tenant_id text NOT NULL,
    portfolio_epoch bigint NOT NULL,
    portfolio_source_epoch bigint NOT NULL,
    mode text NOT NULL CHECK (mode IN ('RADIUS_TRIAGE', 'SHAKEMAP_MMI')),
    privacy_epoch bigint NOT NULL,
    rule_version text NOT NULL,
    status text NOT NULL CHECK (status IN ('COMPLETE', 'NO_MAP', 'UNSUPPORTED_GRID',
                                           'RETRACTED', 'REDACTED_BY_ERASURE')),
    eligible_count int,
    qualifying_count int,
    limitations text[] NOT NULL,
    calculated_at timestamptz NOT NULL,
    CHECK ((status = 'COMPLETE') = (eligible_count IS NOT NULL AND qualifying_count IS NOT NULL))
);

-- Soma por moeda em numeric: nunca float, nunca entre moedas.
CREATE TABLE exposure.calculation_total (
    calculation_id text NOT NULL REFERENCES exposure.calculation,
    currency text NOT NULL CHECK (currency IN ('BRL', 'USD')),
    qualifying_value_minor numeric(38, 0) NOT NULL CHECK (qualifying_value_minor >= 0),
    PRIMARY KEY (calculation_id, currency)
);

CREATE TABLE exposure.fact (
    calculation_id text NOT NULL REFERENCES exposure.calculation,
    asset_id text NOT NULL,
    currency text NOT NULL,
    insured_value_minor bigint NOT NULL,
    lon double precision NOT NULL,
    lat double precision NOT NULL,
    eligible boolean NOT NULL,
    qualifying boolean NOT NULL,
    mmi double precision,
    PRIMARY KEY (calculation_id, asset_id),
    CHECK (eligible OR NOT qualifying)
);

-- O que deveria estar publicado (desired) e o que está (published). O marcador sobe a geração
-- quando o vetor muda; o worker só publica se a geração não mudou durante o cálculo (CAS).
CREATE TABLE control.exposure_job (
    tenant_id text NOT NULL,
    earthquake_uid uuid NOT NULL,
    stream_scope control.stream_scope NOT NULL,
    mode text NOT NULL,
    desired_calculation_id text NOT NULL,
    desired_generation bigint NOT NULL,
    published_generation bigint NOT NULL DEFAULT 0,
    lease_until timestamptz,
    attempts int NOT NULL DEFAULT 0,
    last_error text,
    marked_at timestamptz NOT NULL,
    PRIMARY KEY (tenant_id, earthquake_uid, stream_scope, mode)
);
CREATE INDEX exposure_job_pending ON control.exposure_job (marked_at)
    WHERE desired_generation > published_generation;
GRANT SELECT ON control.exposure_job TO qe_api_read;

CREATE TABLE serving.current_publication (
    tenant_id text NOT NULL,
    earthquake_uid uuid NOT NULL,
    stream_scope control.stream_scope NOT NULL,
    mode text NOT NULL,
    calculation_id text NOT NULL REFERENCES exposure.calculation,
    generation bigint NOT NULL,
    published_at timestamptz NOT NULL,
    PRIMARY KEY (tenant_id, earthquake_uid, stream_scope, mode)
);

-- Histórico de trocas de publicação: base do explain (antes e depois).
CREATE TABLE serving.publication_log (
    publication_seq bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    tenant_id text NOT NULL,
    earthquake_uid uuid NOT NULL,
    stream_scope control.stream_scope NOT NULL,
    mode text NOT NULL,
    calculation_id text REFERENCES exposure.calculation,  -- nulo: publicação retirada
    published_at timestamptz NOT NULL
);
CREATE INDEX publication_log_key
    ON serving.publication_log (tenant_id, earthquake_uid, stream_scope, mode, publication_seq);
