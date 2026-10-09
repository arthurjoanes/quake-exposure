-- A origem publica só intervalos e clocks. Dados pessoais e deduplicação ficam de fora.
CREATE SCHEMA IF NOT EXISTS ops;
CREATE SCHEMA privacy;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
REVOKE ALL ON SCHEMA ops, privacy FROM PUBLIC;
ALTER DEFAULT PRIVILEGES REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC;

CREATE TABLE public.portfolio_clock (
    tenant_id text PRIMARY KEY CHECK (tenant_id <> ''),
    epoch bigint NOT NULL CHECK (epoch >= 1),
    source_epoch bigint NOT NULL CHECK (source_epoch >= 1),
    last_command_id uuid NOT NULL,
    updated_at timestamptz NOT NULL
);

CREATE TABLE privacy.subject (
    subject_token uuid PRIMARY KEY,
    tenant_id text NOT NULL,
    asset_id text NOT NULL,
    contact text NOT NULL CHECK (contact ~ '^[^@]+@example\.invalid$'),
    UNIQUE (tenant_id, asset_id)
);

CREATE TABLE privacy.erase_block (
    subject_token uuid PRIMARY KEY,
    tenant_id text NOT NULL,
    asset_id text NOT NULL,
    blocked_at timestamptz NOT NULL DEFAULT clock_timestamp()
);

CREATE TABLE public.asset_interval (
    tenant_id text NOT NULL,
    asset_interval_id text NOT NULL CHECK (asset_interval_id <> ''),
    asset_id text NOT NULL CHECK (asset_id <> ''),
    subject_token uuid NOT NULL,
    row_version int NOT NULL CHECK (row_version >= 1),
    valid_from timestamptz NOT NULL,
    valid_to timestamptz,
    lon double precision NOT NULL CHECK (lon BETWEEN -180 AND 180),
    lat double precision NOT NULL CHECK (lat BETWEEN -90 AND 90),
    insured_value_minor bigint NOT NULL CHECK (insured_value_minor >= 0),
    currency text NOT NULL CHECK (currency IN ('BRL', 'USD')),
    is_deleted boolean NOT NULL,
    updated_at timestamptz NOT NULL,
    PRIMARY KEY (tenant_id, asset_interval_id),
    CHECK (valid_to IS NULL OR valid_to > valid_from),
    EXCLUDE USING gist (
        tenant_id WITH =, asset_id WITH =, tstzrange(valid_from, valid_to, '[)') WITH &&
    ) WHERE (NOT is_deleted) DEFERRABLE INITIALLY DEFERRED
);
CREATE INDEX asset_interval_subject ON public.asset_interval (subject_token);
ALTER TABLE public.asset_interval REPLICA IDENTITY DEFAULT;
ALTER TABLE public.portfolio_clock REPLICA IDENTITY DEFAULT;

CREATE TABLE ops.command_dedup (
    command_id uuid PRIMARY KEY,
    request_hash text NOT NULL,
    result jsonb NOT NULL
);

CREATE FUNCTION ops.portfolio_command(
    p_command_id uuid, p_tenant_id text, p_rows jsonb,
    p_source_epoch bigint DEFAULT 1, p_retroactive boolean DEFAULT false
) RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE
    v_hash text;
    v_dedup ops.command_dedup%ROWTYPE;
    v_clock public.portfolio_clock%ROWTYPE;
    v_old public.asset_interval%ROWTYPE;
    v_row jsonb;
    v_token uuid;
    v_asset text;
    v_epoch bigint;
    v_result jsonb;
    v_changed_at timestamptz := clock_timestamp();
BEGIN
    IF p_command_id IS NULL OR p_tenant_id IS NULL OR p_tenant_id = ''
       OR p_source_epoch IS NULL OR p_source_epoch < 1 OR p_retroactive IS NULL THEN
        RAISE EXCEPTION 'command_id, tenant e source_epoch válidos são obrigatórios';
    END IF;
    IF jsonb_typeof(p_rows) IS DISTINCT FROM 'array' THEN
        RAISE EXCEPTION 'rows deve ser um array';
    END IF;
    IF jsonb_array_length(p_rows) NOT BETWEEN 1 AND 2000 THEN
        RAISE EXCEPTION 'comando deve ter de 1 a 2000 intervalos';
    END IF;
    IF (SELECT count(DISTINCT r->>'asset_interval_id') FROM jsonb_array_elements(p_rows) r)
       <> jsonb_array_length(p_rows) THEN
        RAISE EXCEPTION 'asset_interval_id ausente ou repetido no comando';
    END IF;
    v_hash := md5(jsonb_build_array('portfolio', p_tenant_id, p_rows,
                                  p_source_epoch, p_retroactive)::text);
    PERFORM pg_advisory_xact_lock(hashtextextended('command:' || p_command_id::text, 0));
    SELECT * INTO v_dedup FROM ops.command_dedup WHERE command_id = p_command_id;
    IF FOUND THEN
        IF v_dedup.request_hash <> v_hash THEN
            RAISE EXCEPTION 'command_id reutilizado com outro conteúdo';
        END IF;
        RETURN v_dedup.result;
    END IF;
    -- O advisory lock cobre também o primeiro comando, quando ainda não existe clock.
    PERFORM pg_advisory_xact_lock(hashtextextended('tenant:' || p_tenant_id, 0));
    SELECT * INTO v_clock FROM public.portfolio_clock
        WHERE tenant_id = p_tenant_id FOR UPDATE;
    IF FOUND AND v_clock.source_epoch <> p_source_epoch THEN
        RAISE EXCEPTION 'source_epoch diferente da origem; reset exige operação manual';
    END IF;
    v_epoch := coalesce(v_clock.epoch, 0) + 1;
    FOR v_row IN SELECT value FROM jsonb_array_elements(p_rows) LOOP
        v_token := (v_row->>'subject_token')::uuid;
        v_asset := v_row->>'asset_id';
        IF EXISTS (SELECT 1 FROM privacy.erase_block WHERE subject_token = v_token) THEN
            RAISE EXCEPTION 'titular bloqueado para exclusão';
        END IF;
        SELECT * INTO v_old FROM public.asset_interval
            WHERE tenant_id = p_tenant_id
              AND asset_interval_id = v_row->>'asset_interval_id';
        IF FOUND THEN
            IF NOT p_retroactive THEN
                RAISE EXCEPTION 'alteração exige comando de correção retroativa explícito';
            END IF;
            IF v_old.asset_id IS DISTINCT FROM v_asset
               OR v_old.subject_token IS DISTINCT FROM v_token THEN
                RAISE EXCEPTION 'a identidade do intervalo é imutável';
            END IF;
            IF v_row ? 'expected_row_version'
               AND (v_row->>'expected_row_version')::int IS DISTINCT FROM v_old.row_version THEN
                RAISE EXCEPTION 'row_version mudou durante a correção';
            END IF;
        ELSIF v_row ? 'expected_row_version' THEN
            RAISE EXCEPTION 'intervalo da correção não existe';
        ELSIF NOT p_retroactive AND EXISTS (
            SELECT 1 FROM public.asset_interval
            WHERE tenant_id = p_tenant_id AND asset_id = v_asset
        ) THEN
            RAISE EXCEPTION 'novo intervalo de ativo existente exige correção explícita';
        END IF;
        INSERT INTO privacy.subject (subject_token, tenant_id, asset_id, contact)
            VALUES (v_token, p_tenant_id, v_asset, v_token::text || '@example.invalid')
            ON CONFLICT (subject_token) DO NOTHING;
        IF NOT EXISTS (SELECT 1 FROM privacy.subject WHERE subject_token = v_token
                       AND tenant_id = p_tenant_id AND asset_id = v_asset) THEN
            RAISE EXCEPTION 'subject_token já pertence a outro ativo';
        END IF;
        INSERT INTO public.asset_interval AS current (
            tenant_id, asset_interval_id, asset_id, subject_token, row_version,
            valid_from, valid_to, lon, lat, insured_value_minor, currency, is_deleted, updated_at
        ) VALUES (
            p_tenant_id, v_row->>'asset_interval_id', v_asset, v_token, 1,
            (v_row->>'valid_from')::timestamptz, (v_row->>'valid_to')::timestamptz,
            (v_row->>'lon')::double precision, (v_row->>'lat')::double precision,
            (v_row->>'insured_value_minor')::bigint, v_row->>'currency',
            coalesce((v_row->>'is_deleted')::boolean, false), v_changed_at
        ) ON CONFLICT (tenant_id, asset_interval_id) DO UPDATE SET
            row_version = current.row_version + 1,
            valid_from = EXCLUDED.valid_from, valid_to = EXCLUDED.valid_to,
            lon = EXCLUDED.lon, lat = EXCLUDED.lat,
            insured_value_minor = EXCLUDED.insured_value_minor, currency = EXCLUDED.currency,
            is_deleted = EXCLUDED.is_deleted, updated_at = EXCLUDED.updated_at;
    END LOOP;
    INSERT INTO public.portfolio_clock (tenant_id, epoch, source_epoch, last_command_id, updated_at)
        VALUES (p_tenant_id, v_epoch, p_source_epoch, p_command_id, v_changed_at)
        ON CONFLICT (tenant_id) DO UPDATE SET epoch = EXCLUDED.epoch,
            last_command_id = EXCLUDED.last_command_id, updated_at = EXCLUDED.updated_at;
    v_result := jsonb_build_object('command_id', p_command_id, 'tenant_id', p_tenant_id,
        'epoch', v_epoch, 'source_epoch', p_source_epoch, 'rows', jsonb_array_length(p_rows));
    INSERT INTO ops.command_dedup VALUES (p_command_id, v_hash, v_result);
    RETURN v_result;
END;
$$;

CREATE FUNCTION ops.correct_interval(
    p_command_id uuid, p_tenant_id text, p_interval_id text, p_changes jsonb
) RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE
    v_hash text;
    v_dedup ops.command_dedup%ROWTYPE;
    v_row public.asset_interval%ROWTYPE;
    v_clock public.portfolio_clock%ROWTYPE;
    v_result jsonb;
    v_payload jsonb;
BEGIN
    IF p_command_id IS NULL OR p_tenant_id IS NULL OR p_interval_id IS NULL
       OR jsonb_typeof(p_changes) IS DISTINCT FROM 'object' OR p_changes = '{}'::jsonb THEN
        RAISE EXCEPTION 'comando, tenant, intervalo e alterações são obrigatórios';
    END IF;
    IF EXISTS (SELECT 1 FROM jsonb_object_keys(p_changes) k
               WHERE k NOT IN ('lon', 'lat', 'insured_value_minor', 'is_deleted')) THEN
        RAISE EXCEPTION 'campo de correção não permitido';
    END IF;
    v_hash := md5(jsonb_build_array('correct', p_tenant_id, p_interval_id, p_changes)::text);
    PERFORM pg_advisory_xact_lock(hashtextextended('command:' || p_command_id::text, 0));
    SELECT * INTO v_dedup FROM ops.command_dedup WHERE command_id = p_command_id;
    IF FOUND THEN
        IF v_dedup.request_hash <> v_hash THEN
            RAISE EXCEPTION 'command_id reutilizado com outro conteúdo';
        END IF;
        RETURN v_dedup.result;
    END IF;
    PERFORM pg_advisory_xact_lock(hashtextextended('tenant:' || p_tenant_id, 0));
    SELECT * INTO STRICT v_clock FROM public.portfolio_clock
        WHERE tenant_id = p_tenant_id FOR UPDATE;
    SELECT * INTO STRICT v_row FROM public.asset_interval
        WHERE tenant_id = p_tenant_id AND asset_interval_id = p_interval_id;
    v_payload := to_jsonb(v_row) || p_changes;
    v_result := ops.portfolio_command(p_command_id, p_tenant_id,
        jsonb_build_array(v_payload), v_clock.source_epoch, true);
    UPDATE ops.command_dedup SET request_hash = v_hash WHERE command_id = p_command_id;
    RETURN v_result;
END;
$$;

-- Bloquear não produz transação publicada; a purga abaixo produz DELETEs e um clock.
CREATE FUNCTION privacy.block_subject(p_subject_token uuid) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE
    v_tenant text;
    v_asset text;
BEGIN
    SELECT tenant_id, asset_id INTO v_tenant, v_asset FROM privacy.subject
        WHERE subject_token = p_subject_token;
    IF NOT FOUND THEN
        SELECT tenant_id, asset_id INTO v_tenant, v_asset FROM privacy.erase_block
            WHERE subject_token = p_subject_token;
        IF NOT FOUND THEN RAISE EXCEPTION 'titular desconhecido'; END IF;
    END IF;
    PERFORM pg_advisory_xact_lock(hashtextextended('tenant:' || v_tenant, 0));
    PERFORM 1 FROM public.portfolio_clock WHERE tenant_id = v_tenant FOR UPDATE;
    INSERT INTO privacy.erase_block (subject_token, tenant_id, asset_id)
        VALUES (p_subject_token, v_tenant, v_asset) ON CONFLICT DO NOTHING;
    RETURN jsonb_build_object('tenant_id', v_tenant, 'asset_id', v_asset);
END;
$$;

CREATE FUNCTION privacy.erase_subject(p_command_id uuid, p_subject_token uuid) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE
    v_block privacy.erase_block%ROWTYPE;
    v_clock public.portfolio_clock%ROWTYPE;
    v_dedup ops.command_dedup%ROWTYPE;
    v_hash text := md5(jsonb_build_array('erase', p_subject_token)::text);
    v_result jsonb;
    v_rows int;
BEGIN
    IF p_command_id IS NULL OR p_subject_token IS NULL THEN
        RAISE EXCEPTION 'command_id e subject_token são obrigatórios';
    END IF;
    PERFORM pg_advisory_xact_lock(hashtextextended('command:' || p_command_id::text, 0));
    SELECT * INTO v_dedup FROM ops.command_dedup WHERE command_id = p_command_id;
    IF FOUND THEN
        IF v_dedup.request_hash <> v_hash THEN
            RAISE EXCEPTION 'command_id reutilizado com outro conteúdo';
        END IF;
        RETURN v_dedup.result;
    END IF;
    SELECT * INTO v_block FROM privacy.erase_block WHERE subject_token = p_subject_token;
    IF NOT FOUND THEN RAISE EXCEPTION 'titular precisa estar bloqueado antes da purga'; END IF;
    PERFORM pg_advisory_xact_lock(hashtextextended('tenant:' || v_block.tenant_id, 0));
    SELECT * INTO STRICT v_clock FROM public.portfolio_clock
        WHERE tenant_id = v_block.tenant_id FOR UPDATE;
    DELETE FROM public.asset_interval WHERE subject_token = p_subject_token;
    GET DIAGNOSTICS v_rows = ROW_COUNT;
    DELETE FROM privacy.subject WHERE subject_token = p_subject_token;
    UPDATE public.portfolio_clock SET epoch = epoch + 1,
        last_command_id = p_command_id, updated_at = clock_timestamp()
        WHERE tenant_id = v_block.tenant_id RETURNING * INTO v_clock;
    v_result := jsonb_build_object('command_id', p_command_id, 'tenant_id', v_clock.tenant_id,
        'epoch', v_clock.epoch, 'source_epoch', v_clock.source_epoch, 'rows', v_rows);
    INSERT INTO ops.command_dedup VALUES (p_command_id, v_hash, v_result);
    RETURN v_result;
END;
$$;

CREATE FUNCTION ops.cdc_slot_status()
RETURNS TABLE (active boolean, retained_wal_bytes bigint)
LANGUAGE sql SECURITY DEFINER SET search_path = pg_catalog AS $$
    SELECT s.active, pg_wal_lsn_diff(pg_current_wal_lsn(), s.restart_lsn)::bigint
    FROM pg_replication_slots s WHERE s.slot_name = 'qe_portfolio';
$$;

GRANT USAGE ON SCHEMA public, ops, privacy TO qe_source_writer;
GRANT SELECT ON public.asset_interval, public.portfolio_clock TO qe_source_writer;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA ops, privacy FROM PUBLIC;
GRANT EXECUTE ON FUNCTION ops.portfolio_command(uuid, text, jsonb, bigint, boolean),
    ops.correct_interval(uuid, text, text, jsonb), ops.cdc_slot_status(),
    privacy.block_subject(uuid), privacy.erase_subject(uuid, uuid) TO qe_source_writer;
GRANT USAGE ON SCHEMA public TO qe_debezium;
GRANT SELECT ON public.asset_interval, public.portfolio_clock TO qe_debezium;
CREATE PUBLICATION portfolio_publication FOR TABLE public.asset_interval, public.portfolio_clock;
