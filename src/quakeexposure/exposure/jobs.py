"""Escopo de cálculo (§4.6) e marcação de jobs: o vetor desejado de cada (tenant, evento, escopo,
modo). Quando o vetor muda, a geração desejada sobe; o worker publica o que estiver atrasado.

LIVE: eventos com ShakeMap dos últimos 7 dias e a watchlist. REPLAY e FAULT: todos.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from quakeexposure.domain.events import Lifecycle
from quakeexposure.domain.exposure import InputVector, StreamScope
from quakeexposure.domain.rule import DEFAULT_RULE, Mode, Rule
from quakeexposure.ingestion.store import Conn
from quakeexposure.portfolio.store import Tenant, tenants

SCOPE_WINDOW = timedelta(days=7)


@dataclass(frozen=True)
class Target:
    """O que o cálculo de um evento precisa saber do lado do evento."""

    stream_scope: str
    earthquake_uid: UUID
    lifecycle: str
    revision_id: str
    semantic_sha256: str
    event_type: str | None
    types: frozenset[str]
    event_time: datetime
    lon: float
    lat: float
    map_known: bool
    product_revision_id: str | None
    grid_status: str | None
    grid_capture_id: int | None


_TARGETS = """
SELECT e.stream_scope, e.earthquake_uid, e.lifecycle, r.revision_id, r.semantic_sha256,
       r.event_type, r.types, e.event_time, r.lon, r.lat, m.earthquake_uid IS NOT NULL,
       m.product_revision_id, g.status, g.capture_id
FROM quake.earthquake e
JOIN quake.event_revision r
  ON r.stream_scope = e.stream_scope AND r.revision_id = e.current_revision_id
LEFT JOIN quake.event_map m
  ON m.stream_scope = e.stream_scope AND m.earthquake_uid = e.earthquake_uid
LEFT JOIN quake.shakemap_grid g
  ON g.stream_scope = m.stream_scope AND g.product_revision_id = m.product_revision_id
WHERE e.merged_into IS NULL
"""

_IN_SCOPE = """
  AND (e.stream_scope <> 'LIVE'
       OR (e.event_time >= %(since)s
           AND ('shakemap' = ANY(r.types) OR m.product_revision_id IS NOT NULL))
       OR EXISTS (SELECT 1 FROM quake.alias a
                  JOIN control.watchlist w ON w.provider_event_id = a.provider_id
                  WHERE a.stream_scope = e.stream_scope AND a.earthquake_uid = e.earthquake_uid))
"""


def _target(row: tuple[Any, ...]) -> Target:
    scope, uid, life, rev, semantic, etype, types, time, lon, lat, known, product, gs, gc = row
    return Target(
        scope, uid, life, rev, semantic, etype, frozenset(types), time, lon, lat, known, product,
        gs, gc,
    )  # fmt: skip


def targets_in_scope(conn: Conn, now: datetime) -> list[Target]:
    rows = conn.execute(_TARGETS + _IN_SCOPE, {"since": now - SCOPE_WINDOW}).fetchall()
    return [_target(r) for r in rows]


def target(conn: Conn, scope: str, uid: UUID) -> Target | None:
    row = conn.execute(
        _TARGETS + " AND e.stream_scope = %(scope)s AND e.earthquake_uid = %(uid)s",
        {"scope": scope, "uid": uid},
    ).fetchone()
    return None if row is None else _target(row)


def vector_for(t: Target, tenant: Tenant, mode: Mode, rule: Rule) -> InputVector | None:
    """O vetor de entrada, ou None se este par não tem cálculo agora."""
    retracted = t.lifecycle == Lifecycle.RETRACTED
    if not retracted and not rule.event_eligible(t.event_type):
        return None  # tipo fora da regra: ingerido, não calculado
    product = None
    if mode is Mode.SHAKEMAP_MMI and not retracted:
        if not t.map_known and "shakemap" in t.types:
            return None  # o detail ainda não chegou: não se sabe qual é o mapa
        product = t.product_revision_id
        if product is not None and t.grid_status is None:
            return None  # mapa escolhido, grid ainda não baixado
    return InputVector(
        stream_scope=StreamScope(t.stream_scope),
        earthquake_uid=t.earthquake_uid,
        event_semantic_sha256=t.semantic_sha256,
        product_revision_id=product,
        tenant_id=tenant.tenant_id,
        portfolio_epoch=tenant.portfolio_epoch,
        portfolio_source_epoch=tenant.source_epoch,
        mode=mode,
        privacy_epoch=tenant.privacy_epoch,
        rule_version=rule.version,
    )


def mark(conn: Conn, now: datetime, rule: Rule = DEFAULT_RULE) -> int:
    """Sobe a geração dos jobs cujo vetor mudou e tira do ar as publicações de eventos que
    saíram da lista ativa (fusão) ou da regra. Devolve quantos jobs mudaram."""
    people = tenants(conn)
    desired = []
    for t in targets_in_scope(conn, now):
        for tenant in people:
            for mode in Mode:
                v = vector_for(t, tenant, mode, rule)
                if v is not None:
                    desired.append((tenant.tenant_id, t.earthquake_uid, t.stream_scope, mode,
                                    v.calculation_id, now))  # fmt: skip
    with conn.transaction():
        conn.execute("SELECT pg_advisory_xact_lock(hashtext('quake-exposure:mark'))")
        current = {
            (t, u, s, m): c
            for t, u, s, m, c in conn.execute(
                """SELECT tenant_id, earthquake_uid, stream_scope, mode, desired_calculation_id
                   FROM control.exposure_job"""
            )
        }
        changed = [row for row in desired if current.get(row[:4]) != row[4]]
        with conn.cursor() as cur:
            cur.executemany(
                """INSERT INTO control.exposure_job AS j (tenant_id, earthquake_uid,
                       stream_scope, mode, desired_calculation_id, desired_generation, marked_at)
                   VALUES (%s, %s, %s, %s, %s, 1, %s)
                   ON CONFLICT (tenant_id, earthquake_uid, stream_scope, mode) DO UPDATE SET
                       desired_calculation_id = EXCLUDED.desired_calculation_id,
                       desired_generation = j.desired_generation + 1,
                       marked_at = EXCLUDED.marked_at, attempts = 0, last_error = NULL
                   WHERE j.desired_calculation_id <> EXCLUDED.desired_calculation_id""",
                changed,
            )
        _withdraw(conn, rule, now)
    return len(changed)


def _withdraw(conn: Conn, rule: Rule, now: datetime) -> None:
    """Publicações de evento fundido (merged_into) ou fora da regra saem do ar."""
    gone = conn.execute(
        """DELETE FROM serving.current_publication p
           USING quake.earthquake e, quake.event_revision r
           WHERE e.stream_scope = p.stream_scope AND e.earthquake_uid = p.earthquake_uid
             AND r.stream_scope = e.stream_scope AND r.revision_id = e.current_revision_id
             AND (e.merged_into IS NOT NULL
                  OR (e.lifecycle = 'ACTIVE' AND NOT coalesce(r.event_type = ANY(%s), false)))
           RETURNING p.tenant_id, p.earthquake_uid, p.stream_scope, p.mode""",
        (sorted(rule.event_types),),
    ).fetchall()
    for key in gone:
        conn.execute(
            """INSERT INTO serving.publication_log (tenant_id, earthquake_uid, stream_scope,
                   mode, calculation_id, published_at) VALUES (%s, %s, %s, %s, NULL, %s)""",
            (*key, now),
        )
        conn.execute(
            """DELETE FROM control.exposure_job WHERE tenant_id = %s AND earthquake_uid = %s
               AND stream_scope = %s AND mode = %s""",
            key,
        )
