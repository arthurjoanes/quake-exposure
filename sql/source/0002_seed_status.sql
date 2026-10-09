-- Só metadados: permite distinguir carga completa de um seed interrompido entre lotes.
CREATE TABLE ops.seed_run (
    singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
    assets int NOT NULL,
    seed bigint NOT NULL,
    completed boolean NOT NULL DEFAULT false,
    started_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    completed_at timestamptz
);

CREATE FUNCTION ops.seed_begin(p_assets int, p_seed bigint) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
BEGIN
    PERFORM pg_advisory_xact_lock(hashtext('quake-exposure:source-seed'));
    IF EXISTS (SELECT 1 FROM public.portfolio_clock) OR EXISTS (SELECT 1 FROM ops.seed_run) THEN
        RAISE EXCEPTION 'seed exige origem vazia; carga parcial exige reset manual';
    END IF;
    INSERT INTO ops.seed_run (assets, seed) VALUES (p_assets, p_seed);
END;
$$;

CREATE FUNCTION ops.seed_finish(p_assets int, p_seed bigint, p_revisions int) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
BEGIN
    PERFORM pg_advisory_xact_lock(hashtext('quake-exposure:source-seed'));
    IF NOT EXISTS (SELECT 1 FROM ops.seed_run WHERE assets = p_assets AND seed = p_seed)
       OR (SELECT count(DISTINCT subject_token) FROM public.asset_interval) <> p_assets
       OR (SELECT coalesce(sum(row_version), 0) FROM public.asset_interval) <> p_revisions THEN
        RAISE EXCEPTION 'seed incompleto: ativos ou revisões não conferem';
    END IF;
    UPDATE ops.seed_run SET completed = true, completed_at = clock_timestamp();
END;
$$;

CREATE FUNCTION ops.seed_status() RETURNS TABLE (assets int, seed bigint, completed boolean)
LANGUAGE sql SECURITY DEFINER SET search_path = pg_catalog AS $$
    SELECT assets, seed, completed FROM ops.seed_run;
$$;

REVOKE ALL ON FUNCTION ops.seed_begin(int, bigint), ops.seed_finish(int, bigint, int),
    ops.seed_status() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION ops.seed_begin(int, bigint), ops.seed_finish(int, bigint, int),
    ops.seed_status() TO qe_source_writer;
