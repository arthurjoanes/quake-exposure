"""Projeção: das capturas gravadas para revisões, aliases, ShakeMaps e grids.

Roda logo depois da captura, em outra transação. Cada feature tem o seu savepoint: erro numa
feature vai para a quarentena (motivo e hash, sem payload) e o resto segue. Erro transitório
do banco deixa a captura pendente para o ciclo seguinte.
"""

import hashlib
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from uuid import uuid4

import psycopg

from quakeexposure.contracts import EVENT, SHAKEMAP, first_error
from quakeexposure.domain.aliases import AliasKind, ingest
from quakeexposure.domain.events import EventRevision, Outcome, revision_from_feature
from quakeexposure.domain.grid import UnsupportedGrid, parse_grid
from quakeexposure.exposure import jobs
from quakeexposure.ingestion import store
from quakeexposure.ingestion.blobs import BlobStore
from quakeexposure.ingestion.store import Conn

log = logging.getLogger(__name__)

FDSN_QUERY_URL = "https://earthquake.usgs.gov/fdsnws/event/1/query"
# espera de lock, timeout, deadlock: a captura volta no próximo ciclo
TRANSIENT = (
    psycopg.errors.QueryCanceled,
    psycopg.errors.LockNotAvailable,
    psycopg.errors.DeadlockDetected,
    psycopg.errors.SerializationFailure,
)
# classes de SQLSTATE do ambiente, não do dado: conexão (08), recursos como disco e memória
# (53), operador (57) e sistema (58). 54 (limite do programa) fica de fora: é o dado
TRANSIENT_CLASSES = {"08", "53", "57", "58"}


def reconsult_url(event_id: str) -> str:
    return f"{FDSN_QUERY_URL}?eventid={event_id}&includedeleted=true&format=geojson"


@dataclass(frozen=True)
class Capture:
    capture_id: int
    scope: str
    kind: str
    sha256: str
    blob_path: str
    product_revision_id: str | None


@dataclass
class Projected:
    new_revisions: int = 0
    quarantined: int = 0
    status: str = "OK"


def feature_sha256(feature: Any) -> str:
    # json.dumps aceita NaN: o hash do item não pode falhar no item que está quebrado
    text = json.dumps(feature, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    # surrogatepass: um "\udc80" solto vindo do JSON também tem hash
    return hashlib.sha256(text.encode("utf-8", "surrogatepass")).hexdigest()


def revision_of(feature: Any) -> EventRevision | None:
    """A revisão, se a feature passa no contrato e tem forma canônica (sem NaN, data válida)."""
    if not EVENT.is_valid(feature):
        return None
    try:
        rev = revision_from_feature(feature)
        rev.revision_id  # noqa: B018 (calcula o hash agora: NaN e afins falham aqui)
    except ValueError, OverflowError, TypeError:
        return None
    return rev


def item_of(feature: Any) -> str:
    """Chave na quarentena: revision_id se a feature é válida, senão o sha256 dela."""
    rev = revision_of(feature)
    return rev.revision_id if rev else feature_sha256(feature)


def is_transient(conn: Conn, exc: BaseException) -> bool:
    if conn.broken or conn.closed or isinstance(exc, TRANSIENT):
        return True
    return isinstance(exc, psycopg.Error) and (exc.sqlstate or "")[:2] in TRANSIENT_CLASSES


def project_pending(conn: Conn, blobs: BlobStore, now: datetime) -> Projected:
    total = Projected()
    rows = conn.execute(
        """SELECT capture_id, stream_scope, kind, sha256, blob_path, product_revision_id
           FROM control.capture WHERE projected_at IS NULL ORDER BY capture_id"""
    ).fetchall()
    for row in rows:
        result = project_capture(conn, blobs, Capture(*row), now)
        total.new_revisions += result.new_revisions
        total.quarantined += result.quarantined
    return total


def project_capture(conn: Conn, blobs: BlobStore, capture: Capture, now: datetime) -> Projected:
    try:
        with conn.transaction():
            conn.execute("SELECT pg_advisory_xact_lock(hashtext('quake-exposure:projection'))")
            done = conn.execute(
                "SELECT projected_at FROM control.capture WHERE capture_id = %s",
                (capture.capture_id,),
            ).fetchone()
            if done and done[0] is not None:
                return Projected()  # outro processo (backfill ou coletor) projetou antes
            result = _project(conn, capture, blobs.get(capture.blob_path), now)
            _mark(conn, capture, "OK", now)
        return result
    except Exception as exc:
        if is_transient(conn, exc):
            raise  # a captura fica pendente e volta no próximo ciclo
        log.exception("captura %d em quarentena", capture.capture_id)
        with conn.transaction():
            store.quarantine(
                conn, capture.scope, capture.capture_id, f"projection: {describe(exc)}",
                capture.sha256,
            )  # fmt: skip
            _mark(conn, capture, "QUARANTINED", now)
        return Projected(quarantined=1, status="QUARANTINED")


def describe(exc: BaseException) -> str:
    """Tipo do erro e, do PostgreSQL, o código e a frase principal: nunca o valor da linha."""
    if isinstance(exc, psycopg.Error) and exc.diag.sqlstate:
        return f"{type(exc).__name__} {exc.diag.sqlstate} {exc.diag.message_primary or ''}".strip()
    return type(exc).__name__


def _mark(conn: Conn, capture: Capture, status: str, now: datetime) -> None:
    conn.execute(
        """UPDATE control.capture SET projected_at = %s, projection_status = %s
           WHERE capture_id = %s""",
        (now, status, capture.capture_id),
    )


def _project(conn: Conn, capture: Capture, body: bytes, now: datetime) -> Projected:
    if capture.kind == "grid":
        assert capture.product_revision_id is not None
        try:
            grid = parse_grid(body)
        except UnsupportedGrid as exc:
            store.store_grid(
                conn, capture.scope, capture.product_revision_id, status="UNSUPPORTED_GRID",
                reason=str(exc)[:500], capture_id=capture.capture_id,
            )  # fmt: skip
            return Projected()
        store.store_grid(
            conn, capture.scope, capture.product_revision_id, status="OK",
            capture_id=capture.capture_id, grid=grid,
        )  # fmt: skip
        return Projected()

    doc = json.loads(body)
    collection = doc.get("type") == "FeatureCollection"
    features = doc["features"] if collection else [doc]
    items = [item_of(f) for f in features]
    # o feed repete quase tudo a cada minuto: só passa pelo domínio o que é novo. Um detail
    # (Feature só) passa sempre: a revisão costuma ser a mesma do feed e os produtos, não
    known = store.known_revisions(conn, capture.scope, items) if collection else set()
    prior = store.quarantine_reasons(conn, capture.scope, items)
    result = Projected()
    retry = []
    changed = False
    for feature, item in zip(features, items, strict=True):
        if item in known:
            continue
        outcome = _project_feature(conn, capture, feature, item, prior, now, result)
        changed |= outcome is Outcome.CURRENT
        if outcome is Outcome.ALIAS_CONFLICT:
            retry.append((feature, item))
    # a revisão que solta o id disputado pode vir na mesma resposta, depois da que o pedia
    if changed:
        for feature, item in retry:
            _project_feature(conn, capture, feature, item, prior, now, result)
    return result


def _project_feature(
    conn: Conn,
    capture: Capture,
    feature: Any,
    item: str,
    prior: dict[str, str],
    now: datetime,
    result: Projected,
) -> Outcome | None:
    """Uma feature num savepoint. Item que já está na quarentena não ganha linha nova.

    Conflito de versão e erro não voltam ao domínio; conflito de alias volta, porque depende
    dos conjuntos atuais de outros eventos, que mudam.
    """
    if item in prior and not prior[item].startswith(Outcome.ALIAS_CONFLICT):
        return None
    rev = revision_of(feature)
    outcome: Outcome | None = None
    reason: str | None = None
    if rev is None:
        error = first_error(EVENT, feature)
        reason = f"contract: {error}" if error else "contract: sem forma canônica"
    else:
        try:
            with conn.transaction():
                outcome, reason = _ingest(conn, capture, feature, rev, item in prior, now)
        except Exception as exc:
            if is_transient(conn, exc):
                raise
            log.exception("feature %s em quarentena", item)
            outcome, reason = None, f"projection: {describe(exc)}"
    if outcome is Outcome.CURRENT or outcome is Outcome.HISTORY:
        result.new_revisions += 1
    if reason and item not in prior:
        store.quarantine(conn, capture.scope, capture.capture_id, reason, item)
        prior[item] = reason
        result.quarantined += 1
    return outcome


def _ingest(
    conn: Conn,
    capture: Capture,
    feature: Any,
    rev: EventRevision,
    seen_before: bool,
    now: datetime,
) -> tuple[Outcome, str | None]:
    catalog = store.load_catalog(conn, capture.scope, rev)
    _, ingested = ingest(catalog, rev, uuid4())
    store.apply_ingested(conn, capture.scope, rev, ingested, capture.capture_id)
    live = capture.scope == "LIVE"
    if ingested.outcome is Outcome.ALIAS_CONFLICT:
        disputed = sorted(ingested.conflict_ids)
        if live and not seen_before:
            store.add_alias_conflict(conn, capture.scope, rev, disputed)
            # reconsulta uma vez, nesta ordem: quem tem os ids, os ids e por último a revisão
            owners = store.preferred_ids_of(conn, capture.scope, disputed)
            for step, ids in enumerate((owners, disputed, [rev.provider_event_id])):
                for event_id in sorted(ids):
                    store.request_fetch(
                        conn, event_id, reconsult_url(event_id), "alias",
                        now + timedelta(microseconds=step),
                    )  # fmt: skip
        return ingested.outcome, f"{ingested.outcome} ids={disputed}"
    if ingested.outcome is Outcome.CONFLICT:
        return ingested.outcome, str(ingested.outcome)
    assert ingested.uid is not None
    jobs.touch(conn, capture.scope, {ingested.uid, *(c.uid for c in ingested.changes)})
    store.drop_alias_conflict(conn, capture.scope, rev)
    released = {i for c in ingested.changes if c.kind is AliasKind.SPLIT for i in c.ids}
    if live and released:
        # ids soltos: quem estava parado esperando por eles é reconsultado
        for event_id in store.alias_conflicts_on(conn, capture.scope, released):
            store.request_fetch(conn, event_id, reconsult_url(event_id), "alias", now)
    if "products" in feature["properties"]:
        _store_products(conn, capture, ingested.uid, feature)
    elif (
        live
        and ingested.outcome is Outcome.CURRENT
        and "detail" in feature["properties"]
        and ("shakemap" in rev.types or store.has_map(conn, capture.scope, ingested.uid))
    ):
        store.request_fetch(
            conn, rev.provider_event_id, feature["properties"]["detail"], "updated", now,
            min_updated=rev.source_updated_at,
        )  # fmt: skip
    return ingested.outcome, None


def _store_products(conn: Conn, capture: Capture, uid: Any, detail: dict[str, Any]) -> None:
    """Produtos num savepoint próprio: erro aqui não segura a revisão do evento."""
    products = detail["properties"]["products"].get("shakemap", [])
    valid = []
    for p in products:
        error = first_error(SHAKEMAP, p)
        if error is None:
            valid.append(p)
        elif not store.quarantined(conn, capture.scope, feature_sha256(p)):
            store.quarantine(
                conn, capture.scope, capture.capture_id, f"contract: {error}", feature_sha256(p)
            )
    products_doc = {"products": detail["properties"]["products"]}
    detail = {
        **detail,
        "properties": {
            **detail["properties"],
            "products": {**detail["properties"]["products"], "shakemap": valid},
        },
    }
    try:
        with conn.transaction():
            store.store_detail_products(conn, capture.scope, uid, detail, capture.capture_id)
    except Exception as exc:
        if is_transient(conn, exc):
            raise
        log.exception("produtos do detail em quarentena")
        sha = feature_sha256(products_doc)
        if not store.quarantined(conn, capture.scope, sha):
            store.quarantine(
                conn, capture.scope, capture.capture_id, f"products: {describe(exc)}", sha
            )
