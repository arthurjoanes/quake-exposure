-- Ingestão do USGS: capturas, revisões de evento, aliases e ShakeMap.
-- Toda tabela de evento tem stream_scope: LIVE, REPLAY e FAULT nunca se misturam.

CREATE SCHEMA IF NOT EXISTS control;
CREATE SCHEMA quake;

GRANT USAGE ON SCHEMA control, quake TO qe_pipeline, qe_api_read;
ALTER DEFAULT PRIVILEGES IN SCHEMA control, quake
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO qe_pipeline;
ALTER DEFAULT PRIVILEGES IN SCHEMA control, quake
    GRANT USAGE ON SEQUENCES TO qe_pipeline;
ALTER DEFAULT PRIVILEGES IN SCHEMA control, quake
    GRANT SELECT ON TABLES TO qe_api_read;

CREATE DOMAIN control.stream_scope AS text CHECK (VALUE IN ('LIVE', 'REPLAY', 'FAULT'));

-- Uma linha por resposta guardada. O blob (gzip dos bytes originais) é gravado antes, no
-- mesmo processo: arquivo temporário, fsync, rename e só então esta linha.
CREATE TABLE control.capture (
    capture_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    stream_scope control.stream_scope NOT NULL,
    kind text NOT NULL CHECK (kind IN ('feed', 'fdsn', 'detail', 'grid')),
    url text NOT NULL,
    fetched_at timestamptz NOT NULL,
    http_status int NOT NULL,
    bytes bigint NOT NULL,
    sha256 text NOT NULL CHECK (sha256 ~ '^[0-9a-f]{64}$'),
    blob_path text NOT NULL,
    product_revision_id text,
    parent_sha256 text,
    projected_at timestamptz,
    projection_status text CHECK (projection_status IN ('OK', 'QUARANTINED')),
    CHECK ((stream_scope = 'FAULT') = (parent_sha256 IS NOT NULL)),
    CHECK ((kind = 'grid') = (product_revision_id IS NOT NULL)),
    CHECK ((projected_at IS NULL) = (projection_status IS NULL))
);
CREATE INDEX capture_pending ON control.capture (capture_id) WHERE projected_at IS NULL;
CREATE INDEX capture_sha ON control.capture (stream_scope, sha256);

-- Motivo e hash, nunca o payload.
CREATE TABLE control.quarantine (
    quarantine_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    stream_scope control.stream_scope NOT NULL,
    capture_id bigint REFERENCES control.capture,
    reason text NOT NULL,
    item_sha256 text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE control.source_status (
    source text PRIMARY KEY CHECK (source IN ('feed', 'incremental')),
    last_attempt_at timestamptz,
    last_ok_at timestamptz,
    last_error text,
    watermark timestamptz,
    last_new_revisions int
);

-- Details a buscar: revisão nova de evento com ShakeMap, 409 e reconsulta por eventid.
CREATE TABLE control.fetch_request (
    provider_event_id text PRIMARY KEY,
    url text NOT NULL,
    reason text NOT NULL,
    requested_at timestamptz NOT NULL,
    attempts int NOT NULL DEFAULT 0,
    last_error text
);

CREATE TABLE control.watchlist (
    provider_event_id text PRIMARY KEY,
    added_at timestamptz NOT NULL DEFAULT now()
);

-- Cursor do backfill: a linha só existe se a janela de 1 dia terminou.
CREATE TABLE control.backfill_window (
    day date PRIMARY KEY,
    completed_at timestamptz NOT NULL,
    new_revisions int NOT NULL
);

-- Um evento (earthquake_uid). merged_into preenchido: saiu da lista ativa.
CREATE TABLE quake.earthquake (
    stream_scope control.stream_scope NOT NULL,
    earthquake_uid uuid NOT NULL,
    preferred_id text NOT NULL,
    current_revision_id text NOT NULL,
    lifecycle text NOT NULL CHECK (lifecycle IN ('ACTIVE', 'RETRACTED')),
    event_time timestamptz NOT NULL,
    epicenter geography(Point, 4326) NOT NULL,
    source_updated_at timestamptz NOT NULL,
    merged_into uuid,
    PRIMARY KEY (stream_scope, earthquake_uid),
    FOREIGN KEY (stream_scope, merged_into) REFERENCES quake.earthquake
);

-- Conjunto atual de ids de cada uid ativo. A PK garante: um id, um conjunto.
CREATE TABLE quake.alias (
    stream_scope control.stream_scope NOT NULL,
    provider_id text NOT NULL,
    earthquake_uid uuid NOT NULL,
    PRIMARY KEY (stream_scope, provider_id),
    FOREIGN KEY (stream_scope, earthquake_uid) REFERENCES quake.earthquake
);
CREATE INDEX alias_uid ON quake.alias (stream_scope, earthquake_uid);

CREATE TABLE quake.event_revision (
    stream_scope control.stream_scope NOT NULL,
    revision_id text NOT NULL,
    earthquake_uid uuid NOT NULL,
    provider_event_id text NOT NULL,
    source_updated_at timestamptz NOT NULL,
    semantic_sha256 text NOT NULL,
    lon double precision NOT NULL,
    lat double precision NOT NULL,
    depth_km double precision,
    event_time timestamptz NOT NULL,
    mag double precision,
    event_type text,
    status text NOT NULL,
    ids text[] NOT NULL,
    types text[] NOT NULL,
    capture_id bigint NOT NULL REFERENCES control.capture,
    PRIMARY KEY (stream_scope, revision_id),
    UNIQUE (stream_scope, provider_event_id, source_updated_at),
    FOREIGN KEY (stream_scope, earthquake_uid) REFERENCES quake.earthquake
);
CREATE INDEX event_revision_uid ON quake.event_revision (stream_scope, earthquake_uid);

CREATE TABLE quake.event_alias_revision (
    alias_revision_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    stream_scope control.stream_scope NOT NULL,
    kind text NOT NULL CHECK (kind IN ('MERGE', 'SPLIT')),
    earthquake_uid uuid NOT NULL,
    ids text[] NOT NULL,
    merged_into uuid,
    cause_revision_id text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK ((kind = 'MERGE') = (merged_into IS NOT NULL))
);

CREATE TABLE quake.shakemap_product (
    stream_scope control.stream_scope NOT NULL,
    product_revision_id text NOT NULL,
    earthquake_uid uuid NOT NULL,
    source text NOT NULL,
    code text NOT NULL,
    update_time bigint NOT NULL,
    preferred_weight int NOT NULL,
    status text NOT NULL,
    grid_url text,
    grid_length bigint,
    grid_sha256 text,
    detail_updated_at timestamptz NOT NULL,
    capture_id bigint NOT NULL REFERENCES control.capture,
    PRIMARY KEY (stream_scope, product_revision_id)
);

-- ShakeMap escolhida do detail mais recente. product_revision_id nulo: NO_MAP.
CREATE TABLE quake.event_map (
    stream_scope control.stream_scope NOT NULL,
    earthquake_uid uuid NOT NULL,
    product_revision_id text,
    detail_updated_at timestamptz NOT NULL,
    PRIMARY KEY (stream_scope, earthquake_uid),
    FOREIGN KEY (stream_scope, earthquake_uid) REFERENCES quake.earthquake
);

CREATE TABLE quake.shakemap_grid (
    stream_scope control.stream_scope NOT NULL,
    product_revision_id text NOT NULL,
    status text NOT NULL CHECK (status IN ('OK', 'UNSUPPORTED_GRID', 'REJECTED')),
    reason text,
    capture_id bigint REFERENCES control.capture,
    lon_min double precision,
    lat_min double precision,
    lon_max double precision,
    lat_max double precision,
    nlon int,
    nlat int,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (stream_scope, product_revision_id),
    CHECK ((status = 'OK') = (capture_id IS NOT NULL AND nlon IS NOT NULL))
);
