-- Revisões paradas em ALIAS_CONFLICT. Quando um id disputado é solto por outra revisão,
-- a revisão parada é reconsultada por eventid.
CREATE TABLE control.alias_conflict (
    stream_scope control.stream_scope NOT NULL,
    revision_id text NOT NULL,
    provider_event_id text NOT NULL,
    disputed text[] NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (stream_scope, revision_id)
);
CREATE INDEX alias_conflict_disputed ON control.alias_conflict USING gin (disputed);
