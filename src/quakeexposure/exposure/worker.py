"""Worker de exposição.

Pega o job com FOR UPDATE SKIP LOCKED e uma lease curta, carrega o grid antes de abrir a
transação e calcula tudo numa transação só: fatos, resumo por moeda, CAS na geração e troca de
`serving.current_publication`. Se a geração desejada mudou durante o cálculo, nada é publicado
(o job continua atrasado e volta na próxima rodada). Job cujo vetor já não bate com o estado é
acertado na hora. Worker que morre no meio não deixa nada:
a transação some e a lease expira.
"""

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

import psycopg

from quakeexposure.domain.events import Lifecycle
from quakeexposure.domain.exposure import (
    AssetFact,
    ExposureResult,
    InputVector,
    ResultStatus,
    mmi_facts,
    radius_facts,
    summarize,
    without_values,
)
from quakeexposure.domain.grid import Grid, parse_grid
from quakeexposure.domain.portfolio import AssetVersion, assets_as_of
from quakeexposure.domain.rule import DEFAULT_RULE, Mode, Rule
from quakeexposure.exposure import jobs
from quakeexposure.ingestion.blobs import BlobStore
from quakeexposure.ingestion.store import Conn
from quakeexposure.portfolio.store import tenants, versions_known

log = logging.getLogger(__name__)

LEASE = timedelta(minutes=2)
MAX_ATTEMPTS = 5


class StaleJob(Exception):
    """A geração desejada mudou durante o cálculo: o CAS recusou a publicação."""


@dataclass(frozen=True)
class Job:
    tenant_id: str
    earthquake_uid: UUID
    stream_scope: str
    mode: Mode
    generation: int
    desired_calculation_id: str


def claim(conn: Conn, now: datetime, key: tuple[str, UUID, str, str] | None = None) -> Job | None:
    """O próximo job atrasado (ou um job específico, para teste e demo), com lease."""
    with conn.transaction():
        row = conn.execute(
            """WITH next AS (
                   SELECT tenant_id, earthquake_uid, stream_scope, mode
                   FROM control.exposure_job
                   WHERE desired_generation > published_generation AND attempts < %(max)s
                     AND (lease_until IS NULL OR lease_until < %(now)s)
                     AND (%(tenant)s::text IS NULL OR (tenant_id, earthquake_uid, stream_scope,
                          mode) = (%(tenant)s, %(uid)s, %(scope)s, %(mode)s))
                   ORDER BY marked_at LIMIT 1 FOR UPDATE SKIP LOCKED)
               UPDATE control.exposure_job j
               SET lease_until = %(lease)s, attempts = j.attempts + 1
               FROM next
               WHERE (j.tenant_id, j.earthquake_uid, j.stream_scope, j.mode)
                   = (next.tenant_id, next.earthquake_uid, next.stream_scope, next.mode)
               RETURNING j.tenant_id, j.earthquake_uid, j.stream_scope, j.mode,
                         j.desired_generation, j.desired_calculation_id""",
            {
                "max": MAX_ATTEMPTS,
                "now": now,
                "lease": now + LEASE,
                "tenant": key[0] if key else None,
                "uid": key[1] if key else None,
                "scope": key[2] if key else None,
                "mode": key[3] if key else None,
            },
        ).fetchone()
    if row is None:
        return None
    tenant_id, uid, scope, mode, generation, desired = row
    return Job(tenant_id, uid, scope, Mode(mode), generation, desired)


def run(
    conn: Conn,
    blobs: BlobStore,
    job: Job,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
    rule: Rule = DEFAULT_RULE,
    before_commit: Callable[[], None] | None = None,
) -> str:
    """Calcula e publica um job. Devolve 'published', 'stale' ou 'skipped'."""
    target = jobs.target(conn, job.stream_scope, job.earthquake_uid)
    tenant = next((t for t in tenants(conn) if t.tenant_id == job.tenant_id), None)
    vector = jobs.vector_for(target, tenant, job.mode, rule) if target and tenant else None
    if target is None or vector is None or vector.calculation_id != job.desired_calculation_id:
        _resync(conn, job, vector, now())
        return "skipped"
    stored = _exists(conn, vector.calculation_id)
    grid = _load_grid(conn, blobs, target) if not stored and _needs_grid(target, vector) else None
    try:
        with conn.transaction():
            if not stored and not _exists(conn, vector.calculation_id):
                result, facts, assets = _calculate(conn, target, vector, grid, rule, now())
                _store(conn, target, result, facts, assets)
            if before_commit is not None:
                before_commit()  # só para teste e demo: pausa antes do CAS
            _publish(conn, job, vector, now())
    except StaleJob:
        _release(conn, job)  # a geração nova está esperando: o job volta para a fila agora
        log.info("job %s/%s/%s recusado: geração mudou", job.tenant_id, job.earthquake_uid,
                 job.mode)  # fmt: skip
        return "stale"
    return "published"


def run_pending(
    conn: Conn,
    blobs: BlobStore,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
    rule: Rule = DEFAULT_RULE,
    limit: int | None = None,
) -> dict[str, int]:
    """Marca e processa jobs até não sobrar nenhum disponível."""
    done = {"published": 0, "stale": 0, "skipped": 0, "failed": 0}
    jobs.mark(conn, now(), rule)
    while limit is None or sum(done.values()) < limit:
        job = claim(conn, now())
        if job is None:
            break
        try:
            done[run(conn, blobs, job, now, rule)] += 1
        except Exception as exc:
            if conn.broken or conn.closed:
                raise
            log.exception("job %s falhou", job)
            _fail(conn, job, exc)
            done["failed"] += 1
    return done


def run_forever(
    make: Callable[[], tuple[Conn, BlobStore]],
    every: float = 1.0,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    conn: Conn | None = None
    blobs: BlobStore | None = None
    while True:
        try:
            if conn is None or conn.closed or conn.broken:
                conn, blobs = make()
            assert blobs is not None
            done = run_pending(conn, blobs)
            if any(done.values()):
                log.info("rodada: %s", done)
        except psycopg.OperationalError:
            log.exception("banco indisponível; reconectando")
            conn = None
        except Exception:
            log.exception("rodada do worker falhou")
        sleep(every)


def _needs_grid(target: jobs.Target, vector: InputVector) -> bool:
    return (
        vector.mode is Mode.SHAKEMAP_MMI
        and vector.product_revision_id is not None
        and target.grid_status == "OK"
        and target.lifecycle != Lifecycle.RETRACTED
    )


def _load_grid(conn: Conn, blobs: BlobStore, target: jobs.Target) -> Grid:
    row = conn.execute(
        "SELECT blob_path FROM control.capture WHERE capture_id = %s", (target.grid_capture_id,)
    ).fetchone()
    assert row is not None
    return parse_grid(blobs.get(row[0]))


def _exists(conn: Conn, calculation_id: str) -> bool:
    return (
        conn.execute(
            "SELECT 1 FROM exposure.calculation WHERE calculation_id = %s", (calculation_id,)
        ).fetchone()
        is not None
    )


def _calculate(
    conn: Conn,
    target: jobs.Target,
    vector: InputVector,
    grid: Grid | None,
    rule: Rule,
    at: datetime,
) -> tuple[ExposureResult, list[AssetFact], list[AssetVersion]]:
    if target.lifecycle == Lifecycle.RETRACTED:
        return without_values(vector, ResultStatus.RETRACTED, at), [], []
    if vector.mode is Mode.SHAKEMAP_MMI:
        if vector.product_revision_id is None:
            return without_values(vector, ResultStatus.NO_MAP, at), [], []
        if grid is None:
            return without_values(vector, ResultStatus.UNSUPPORTED_GRID, at), [], []
    assets = assets_as_of(
        versions_known(conn, vector.tenant_id, vector.portfolio_epoch),
        tenant_id=vector.tenant_id,
        at=target.event_time,
        epoch=vector.portfolio_epoch,
    )
    if vector.mode is Mode.SHAKEMAP_MMI:
        assert grid is not None
        facts = mmi_facts(assets, grid, rule)
    else:
        facts = radius_facts(assets, _within_radius(conn, target, assets, rule))
    return summarize(vector, facts, at), facts, assets


def _within_radius(
    conn: Conn, target: jobs.Target, assets: list[AssetVersion], rule: Rule
) -> set[str]:
    """ST_DWithin em geography (elipsoide WGS84), como manda a regra."""
    rows = conn.execute(
        """SELECT a.asset_id
           FROM unnest(%s::text[], %s::float8[], %s::float8[]) AS a(asset_id, lon, lat)
           WHERE ST_DWithin(ST_SetSRID(ST_MakePoint(a.lon, a.lat), 4326)::geography,
                            ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography, %s)""",
        (
            [a.asset_id for a in assets],
            [a.lon for a in assets],
            [a.lat for a in assets],
            target.lon,
            target.lat,
            rule.radius_m,
        ),
    ).fetchall()
    return {r[0] for r in rows}


def _store(
    conn: Conn,
    target: jobs.Target,
    result: ExposureResult,
    facts: list[AssetFact],
    assets: list[AssetVersion],
) -> None:
    v = result.vector
    conn.execute(
        """INSERT INTO exposure.calculation (calculation_id, stream_scope, earthquake_uid,
               event_semantic_sha256, event_revision_id, product_revision_id, tenant_id,
               portfolio_epoch, portfolio_source_epoch, mode, privacy_epoch, rule_version,
               status, eligible_count, qualifying_count, limitations, calculated_at)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
        (v.calculation_id, v.stream_scope, v.earthquake_uid, v.event_semantic_sha256,
         target.revision_id, v.product_revision_id, v.tenant_id, v.portfolio_epoch,
         v.portfolio_source_epoch, v.mode, v.privacy_epoch, v.rule_version, result.status,
         result.eligible_count, result.qualifying_count, list(result.limitations),
         result.calculated_at),
    )  # fmt: skip
    if result.qualifying_value_minor is not None:
        with conn.cursor() as cur:
            cur.executemany(
                """INSERT INTO exposure.calculation_total (calculation_id, currency,
                       qualifying_value_minor) VALUES (%s, %s, %s)""",
                [
                    (v.calculation_id, c, value)
                    for c, value in result.qualifying_value_minor.items()
                ],
            )
    with conn.cursor().copy(
        """COPY exposure.fact (calculation_id, asset_id, currency, insured_value_minor, lon, lat,
               eligible, qualifying, mmi) FROM STDIN"""
    ) as copy:
        for fact, asset in zip(facts, assets, strict=True):
            copy.write_row((
                v.calculation_id, fact.asset_id, fact.currency, fact.insured_value_minor,
                asset.lon, asset.lat, fact.eligible, fact.qualifying, fact.mmi,
            ))  # fmt: skip


def _publish(conn: Conn, job: Job, vector: InputVector, at: datetime) -> None:
    key = (job.tenant_id, job.earthquake_uid, job.stream_scope, job.mode)
    cas = conn.execute(
        """UPDATE control.exposure_job
           SET published_generation = %s, lease_until = NULL, attempts = 0, last_error = NULL
           WHERE tenant_id = %s AND earthquake_uid = %s AND stream_scope = %s AND mode = %s
             AND desired_generation = %s""",
        (job.generation, *key, job.generation),
    )
    if cas.rowcount != 1:
        raise StaleJob  # job velho não publica
    privacy = conn.execute(
        "SELECT privacy_epoch FROM portfolio.tenant WHERE tenant_id = %s", (job.tenant_id,)
    ).fetchone()
    if privacy is None or privacy[0] != vector.privacy_epoch:
        raise StaleJob
    previous = conn.execute(
        """SELECT calculation_id FROM serving.current_publication
           WHERE tenant_id = %s AND earthquake_uid = %s AND stream_scope = %s AND mode = %s
           FOR UPDATE""",
        key,
    ).fetchone()
    if previous is not None and previous[0] == vector.calculation_id:
        return  # mesma entrada: a publicação não muda
    conn.execute(
        """INSERT INTO serving.current_publication (tenant_id, earthquake_uid, stream_scope,
               mode, calculation_id, generation, published_at)
           VALUES (%s, %s, %s, %s, %s, %s, %s)
           ON CONFLICT (tenant_id, earthquake_uid, stream_scope, mode) DO UPDATE SET
               calculation_id = EXCLUDED.calculation_id, generation = EXCLUDED.generation,
               published_at = EXCLUDED.published_at""",
        (*key, vector.calculation_id, job.generation, at),
    )
    conn.execute(
        """INSERT INTO serving.publication_log (tenant_id, earthquake_uid, stream_scope, mode,
               calculation_id, published_at) VALUES (%s, %s, %s, %s, %s, %s)""",
        (*key, vector.calculation_id, at),
    )


def _resync(conn: Conn, job: Job, vector: InputVector | None, at: datetime) -> None:
    """O estado andou depois da marcação. O job é acertado aqui, senão volta igual para a fila e
    a rodada não termina. Sem vetor (fusão, tipo fora da regra, grid ainda não baixado), o job
    fica em dia até o marcador ver um vetor novo."""
    key = (job.tenant_id, job.earthquake_uid, job.stream_scope, job.mode, job.generation)
    if vector is None:
        done = conn.execute(
            """UPDATE control.exposure_job
               SET published_generation = desired_generation, lease_until = NULL, attempts = 0
               WHERE tenant_id = %s AND earthquake_uid = %s AND stream_scope = %s AND mode = %s
                 AND desired_generation = %s""",
            key,
        )
    else:
        done = conn.execute(
            """UPDATE control.exposure_job
               SET desired_calculation_id = %s, desired_generation = desired_generation + 1,
                   marked_at = %s, lease_until = NULL, attempts = 0, last_error = NULL
               WHERE tenant_id = %s AND earthquake_uid = %s AND stream_scope = %s AND mode = %s
                 AND desired_generation = %s""",
            (vector.calculation_id, at, *key),
        )
    if done.rowcount != 1:
        _release(conn, job)  # o marcador mexeu antes


def _release(conn: Conn, job: Job) -> None:
    conn.execute(
        """UPDATE control.exposure_job SET lease_until = NULL, attempts = greatest(attempts - 1, 0)
           WHERE tenant_id = %s AND earthquake_uid = %s AND stream_scope = %s AND mode = %s""",
        (job.tenant_id, job.earthquake_uid, job.stream_scope, job.mode),
    )


def _fail(conn: Conn, job: Job, exc: Exception) -> None:
    conn.execute(
        """UPDATE control.exposure_job SET lease_until = NULL, last_error = %s
           WHERE tenant_id = %s AND earthquake_uid = %s AND stream_scope = %s AND mode = %s""",
        (f"{type(exc).__name__}: {exc}"[:500], job.tenant_id, job.earthquake_uid,
         job.stream_scope, job.mode),
    )  # fmt: skip
