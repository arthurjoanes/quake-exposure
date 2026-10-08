"""Projeção: das capturas gravadas para revisões, aliases, ShakeMaps e grids.

Roda logo depois da captura, em outra transação. Um erro inesperado manda a captura para a
quarentena (motivo e hash, sem payload); captura não projetada é retomada no ciclo seguinte.
"""

import hashlib
import json
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import uuid4

import psycopg

from quakeexposure.contracts import EVENT, SHAKEMAP, first_error
from quakeexposure.domain.aliases import ingest
from quakeexposure.domain.canonical import canonical_json
from quakeexposure.domain.events import Outcome, revision_from_feature
from quakeexposure.domain.grid import UnsupportedGrid, parse_grid
from quakeexposure.ingestion import store
from quakeexposure.ingestion.blobs import BlobStore
from quakeexposure.ingestion.store import Conn

log = logging.getLogger(__name__)

FDSN_QUERY_URL = "https://earthquake.usgs.gov/fdsnws/event/1/query"


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


def feature_sha256(feature: Any) -> str:
    return hashlib.sha256(canonical_json(feature)).hexdigest()


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
    except psycopg.OperationalError:
        raise  # banco fora do ar: a captura fica pendente e volta no próximo ciclo
    except Exception as exc:
        log.exception("captura %d em quarentena", capture.capture_id)
        with conn.transaction():
            store.quarantine(
                conn, capture.scope, capture.capture_id, f"projection: {exc!r}", capture.sha256
            )
            _mark(conn, capture, "QUARANTINED", now)
        return Projected(quarantined=1)


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
    features = doc["features"] if doc.get("type") == "FeatureCollection" else [doc]
    result = Projected()
    known: set[str] = set()
    if doc.get("type") == "FeatureCollection":
        # o feed repete quase tudo a cada minuto: só passa pelo domínio o que é novo
        valid = [revision_from_feature(f).revision_id for f in features if EVENT.is_valid(f)]
        known = store.known_revisions(conn, capture.scope, valid)
    for feature in features:
        error = first_error(EVENT, feature)
        if error:
            store.quarantine(
                conn, capture.scope, capture.capture_id, f"contract: {error}",
                feature_sha256(feature),
            )  # fmt: skip
            result.quarantined += 1
            continue
        rev = revision_from_feature(feature)
        if rev.revision_id in known:
            continue
        catalog = store.load_catalog(conn, capture.scope, rev)
        _, ingested = ingest(catalog, rev, uuid4())
        store.apply_ingested(conn, capture.scope, rev, ingested, capture.capture_id)
        if ingested.outcome in (Outcome.CURRENT, Outcome.HISTORY):
            result.new_revisions += 1
        if ingested.outcome in (Outcome.CONFLICT, Outcome.ALIAS_CONFLICT):
            result.quarantined += 1
        live = capture.scope == "LIVE"
        if live and ingested.outcome is Outcome.ALIAS_CONFLICT:
            for event_id in sorted({rev.provider_event_id, *ingested.conflict_ids}):
                store.request_fetch(conn, event_id, reconsult_url(event_id), "alias", now)
        if ingested.uid is None:
            continue
        if "products" in feature["properties"]:
            _store_products(conn, capture, ingested.uid, feature, result)
        elif (
            live
            and ingested.outcome is Outcome.CURRENT
            and "detail" in feature["properties"]
            and ("shakemap" in rev.types or store.has_map(conn, capture.scope, ingested.uid))
        ):
            store.request_fetch(
                conn, rev.provider_event_id, feature["properties"]["detail"], "updated", now
            )
    return result


def _store_products(
    conn: Conn, capture: Capture, uid: Any, detail: dict[str, Any], result: Projected
) -> None:
    products = detail["properties"]["products"].get("shakemap", [])
    bad = [p for p in products if not SHAKEMAP.is_valid(p)]
    for p in bad:
        store.quarantine(
            conn, capture.scope, capture.capture_id, f"contract: {first_error(SHAKEMAP, p)}",
            feature_sha256(p),
        )  # fmt: skip
        result.quarantined += 1
    if bad:
        detail = {
            **detail,
            "properties": {
                **detail["properties"],
                "products": {**detail["properties"]["products"], "shakemap": [
                    p for p in products if SHAKEMAP.is_valid(p)
                ]},
            },
        }  # fmt: skip
    store.store_detail_products(conn, capture.scope, uid, detail, capture.capture_id)
