"""Leitura e gravação do estado de eventos no PostgreSQL. A decisão fica no domínio."""

from collections.abc import Iterable, Mapping
from datetime import datetime
from typing import Any
from uuid import UUID

import psycopg

from quakeexposure.domain.aliases import AliasEntry, AliasKind, Catalog, Ingested
from quakeexposure.domain.canonical import from_epoch_ms
from quakeexposure.domain.events import EventHistory, EventRevision, Outcome
from quakeexposure.domain.grid import Grid
from quakeexposure.domain.shakemap import GRID_PATH, product_revision_id, select_shakemap

Conn = psycopg.Connection[Any]


def store_capture(
    conn: Conn,
    *,
    scope: str,
    kind: str,
    url: str,
    fetched_at: datetime,
    http_status: int,
    body_bytes: int,
    sha256: str,
    blob_path: str,
    product_revision_id: str | None = None,
    parent_sha256: str | None = None,
) -> int:
    row = conn.execute(
        """INSERT INTO control.capture (stream_scope, kind, url, fetched_at, http_status, bytes,
               sha256, blob_path, product_revision_id, parent_sha256)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING capture_id""",
        (scope, kind, url, fetched_at, http_status, body_bytes, sha256, blob_path,
         product_revision_id, parent_sha256),
    ).fetchone()  # fmt: skip
    assert row is not None
    return int(row[0])


def captured(conn: Conn, scope: str, sha256: str) -> bool:
    return (
        conn.execute(
            "SELECT 1 FROM control.capture WHERE stream_scope = %s AND sha256 = %s",
            (scope, sha256),
        ).fetchone()
        is not None
    )


def quarantine(
    conn: Conn, scope: str, capture_id: int | None, reason: str, item_sha256: str
) -> None:
    conn.execute(
        """INSERT INTO control.quarantine (stream_scope, capture_id, reason, item_sha256)
           VALUES (%s, %s, %s, %s)""",
        (scope, capture_id, reason[:500], item_sha256),
    )


def quarantined(conn: Conn, scope: str, item_sha256: str) -> bool:
    return (
        conn.execute(
            "SELECT 1 FROM control.quarantine WHERE stream_scope = %s AND item_sha256 = %s",
            (scope, item_sha256),
        ).fetchone()
        is not None
    )


def quarantine_reasons(conn: Conn, scope: str, items: Iterable[str]) -> dict[str, str]:
    """Motivo da primeira quarentena de cada item (revision_id ou sha256)."""
    rows = conn.execute(
        """SELECT DISTINCT ON (item_sha256) item_sha256, reason FROM control.quarantine
           WHERE stream_scope = %s AND item_sha256 = ANY(%s)
           ORDER BY item_sha256, quarantine_id""",
        (scope, list(items)),
    )
    return {item: reason for item, reason in rows}


def preferred_ids_of(conn: Conn, scope: str, provider_ids: Iterable[str]) -> set[str]:
    rows = conn.execute(
        """SELECT DISTINCT e.preferred_id FROM quake.alias a
           JOIN quake.earthquake e USING (stream_scope, earthquake_uid)
           WHERE a.stream_scope = %s AND a.provider_id = ANY(%s)""",
        (scope, list(provider_ids)),
    )
    return {r[0] for r in rows}


def known_revisions(conn: Conn, scope: str, revision_ids: Iterable[str]) -> set[str]:
    rows = conn.execute(
        """SELECT revision_id FROM quake.event_revision
           WHERE stream_scope = %s AND revision_id = ANY(%s)""",
        (scope, list(revision_ids)),
    )
    return {r[0] for r in rows}


def load_catalog(conn: Conn, scope: str, rev: EventRevision) -> Catalog:
    """O pedaço do estado que o `ingest` precisa para esta revisão (ver `Catalog`)."""
    owners = {
        r[0]
        for r in conn.execute(
            """SELECT DISTINCT earthquake_uid FROM quake.alias
               WHERE stream_scope = %s AND provider_id = ANY(%s)""",
            (scope, sorted(rev.ids)),
        )
    }
    holders = {
        r[0]
        for r in conn.execute(
            """SELECT DISTINCT earthquake_uid FROM quake.event_revision
               WHERE stream_scope = %s AND (revision_id = %s
                     OR (provider_event_id = %s AND source_updated_at = %s))""",
            (scope, rev.revision_id, rev.provider_event_id, rev.source_updated_at),
        )
    }
    merged_into: dict[UUID, UUID] = {}
    frontier = owners | holders
    while frontier:
        rows = conn.execute(
            """SELECT earthquake_uid, merged_into FROM quake.earthquake
               WHERE stream_scope = %s AND earthquake_uid = ANY(%s) AND merged_into IS NOT NULL""",
            (scope, list(frontier)),
        ).fetchall()
        merged_into.update({uid: into for uid, into in rows})
        frontier = {into for _, into in rows} - merged_into.keys()

    ids: dict[UUID, set[str]] = {}
    preferred: dict[UUID, str] = {}
    for uid, pref, provider_id in conn.execute(
        """SELECT e.earthquake_uid, e.preferred_id, a.provider_id
           FROM quake.earthquake e JOIN quake.alias a USING (stream_scope, earthquake_uid)
           WHERE e.stream_scope = %s AND e.earthquake_uid = ANY(%s)""",
        (scope, list(owners)),
    ):
        ids.setdefault(uid, set()).add(provider_id)
        preferred[uid] = pref
    aliases = {uid: AliasEntry(preferred[uid], frozenset(ids[uid])) for uid in ids}

    revisions: dict[UUID, list[EventRevision]] = {uid: [] for uid in owners | holders}
    for row in conn.execute(
        """SELECT earthquake_uid, revision_id, provider_event_id, source_updated_at, lon, lat,
               depth_km, event_time, mag, event_type, status, ids, types
           FROM quake.event_revision
           WHERE stream_scope = %s AND earthquake_uid = ANY(%s)
           ORDER BY source_updated_at""",
        (scope, list(revisions)),
    ):
        revisions[row[0]].append(_revision(row))
    histories = {uid: EventHistory(tuple(revs)) for uid, revs in revisions.items()}
    return Catalog(aliases, histories, merged_into)


def _revision(row: tuple[Any, ...]) -> EventRevision:
    _, revision_id, provider_id, updated, lon, lat, depth, time, mag, etype, status, ids, types = (
        row
    )
    rev = EventRevision(
        provider_id, updated, lon, lat, depth, time, mag, etype, status,
        frozenset(ids), frozenset(types),
    )  # fmt: skip
    if rev.revision_id != revision_id:
        raise ValueError(f"revisão {revision_id} não confere com as colunas gravadas")
    return rev


def apply_ingested(
    conn: Conn, scope: str, rev: EventRevision, result: Ingested, capture_id: int
) -> None:
    """Grava o que o `ingest` decidiu. DUPLICATE e conflitos não gravam nada aqui; a
    quarentena dos conflitos fica com a projeção."""
    if result.outcome in (Outcome.DUPLICATE, Outcome.CONFLICT, Outcome.ALIAS_CONFLICT):
        return
    assert result.uid is not None
    uid = result.uid
    if result.outcome is Outcome.CURRENT:
        conn.execute(
            """INSERT INTO quake.earthquake AS e (stream_scope, earthquake_uid, preferred_id,
                   current_revision_id, lifecycle, event_time, epicenter, source_updated_at)
               VALUES (%(scope)s, %(uid)s, %(pref)s, %(rev)s, %(life)s, %(time)s,
                       ST_SetSRID(ST_MakePoint(%(lon)s, %(lat)s), 4326)::geography, %(upd)s)
               ON CONFLICT (stream_scope, earthquake_uid) DO UPDATE SET
                   preferred_id = EXCLUDED.preferred_id,
                   current_revision_id = EXCLUDED.current_revision_id,
                   lifecycle = EXCLUDED.lifecycle, event_time = EXCLUDED.event_time,
                   epicenter = EXCLUDED.epicenter,
                   source_updated_at = EXCLUDED.source_updated_at""",
            {"scope": scope, "uid": uid, "pref": rev.provider_event_id, "rev": rev.revision_id,
             "life": rev.lifecycle, "time": rev.time, "lon": rev.lon, "lat": rev.lat,
             "upd": rev.source_updated_at},
        )  # fmt: skip
    conn.execute(
        """INSERT INTO quake.event_revision (stream_scope, revision_id, earthquake_uid,
               provider_event_id, source_updated_at, semantic_sha256, lon, lat, depth_km,
               event_time, mag, event_type, status, ids, types, capture_id)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
        (scope, rev.revision_id, uid, rev.provider_event_id, rev.source_updated_at,
         rev.semantic_sha256, rev.lon, rev.lat, rev.depth_km, rev.time, rev.mag, rev.event_type,
         rev.status, sorted(rev.ids), sorted(rev.types), capture_id),
    )  # fmt: skip
    if result.outcome is not Outcome.CURRENT:
        return
    for change in result.changes:
        if change.kind is AliasKind.MERGE:
            conn.execute(
                "DELETE FROM quake.alias WHERE stream_scope = %s AND earthquake_uid = %s",
                (scope, change.uid),
            )
            conn.execute(
                """UPDATE quake.earthquake SET merged_into = %s
                   WHERE stream_scope = %s AND earthquake_uid = %s""",
                (change.merged_into, scope, change.uid),
            )
        conn.execute(
            """INSERT INTO quake.event_alias_revision (stream_scope, kind, earthquake_uid, ids,
                   merged_into, cause_revision_id) VALUES (%s, %s, %s, %s, %s, %s)""",
            (scope, change.kind, change.uid, sorted(change.ids), change.merged_into,
             rev.revision_id),
        )  # fmt: skip
    conn.execute(
        "DELETE FROM quake.alias WHERE stream_scope = %s AND earthquake_uid = %s", (scope, uid)
    )
    with conn.cursor() as cur:
        cur.executemany(
            """INSERT INTO quake.alias (stream_scope, provider_id, earthquake_uid)
               VALUES (%s, %s, %s)""",
            [(scope, provider_id, uid) for provider_id in sorted(rev.ids)],
        )


def has_map(conn: Conn, scope: str, uid: UUID) -> bool:
    return (
        conn.execute(
            """SELECT 1 FROM quake.event_map WHERE stream_scope = %s AND earthquake_uid = %s
               AND product_revision_id IS NOT NULL""",
            (scope, uid),
        ).fetchone()
        is not None
    )


def store_detail_products(
    conn: Conn, scope: str, uid: UUID, detail: Mapping[str, Any], capture_id: int
) -> None:
    """Produtos ShakeMap do detail e a escolha (§4.3) se este for o detail mais recente."""
    detail_updated = from_epoch_ms(detail["properties"]["updated"])
    for p in detail["properties"].get("products", {}).get("shakemap", []):
        grid = p["contents"].get(GRID_PATH) or {}
        conn.execute(
            """INSERT INTO quake.shakemap_product (stream_scope, product_revision_id,
                   earthquake_uid, source, code, update_time, preferred_weight, status, grid_url,
                   grid_length, grid_sha256, detail_updated_at, capture_id)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
               ON CONFLICT (stream_scope, product_revision_id) DO NOTHING""",
            (scope, product_revision_id(p), uid, p["source"], p["code"], p["updateTime"],
             p["preferredWeight"], p["status"], grid.get("url"), grid.get("length"),
             grid.get("sha256"), detail_updated, capture_id),
        )  # fmt: skip
    chosen = select_shakemap(detail)
    if chosen is not None and GRID_PATH not in chosen["contents"]:
        store_grid(
            conn, scope, product_revision_id(chosen), status="UNSUPPORTED_GRID",
            reason=f"produto sem {GRID_PATH}",
        )  # fmt: skip
    conn.execute(
        """INSERT INTO quake.event_map (stream_scope, earthquake_uid, product_revision_id,
               detail_updated_at) VALUES (%s, %s, %s, %s)
           ON CONFLICT (stream_scope, earthquake_uid) DO UPDATE SET
               product_revision_id = EXCLUDED.product_revision_id,
               detail_updated_at = EXCLUDED.detail_updated_at
           WHERE quake.event_map.detail_updated_at <= EXCLUDED.detail_updated_at""",
        (scope, uid, product_revision_id(chosen) if chosen else None, detail_updated),
    )


def store_grid(
    conn: Conn,
    scope: str,
    product: str,
    *,
    status: str,
    reason: str | None = None,
    capture_id: int | None = None,
    grid: Grid | None = None,
) -> None:
    conn.execute(
        """INSERT INTO quake.shakemap_grid (stream_scope, product_revision_id, status, reason,
               capture_id, lon_min, lat_min, lon_max, lat_max, nlon, nlat)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
           ON CONFLICT (stream_scope, product_revision_id) DO NOTHING""",
        (scope, product, status, reason, capture_id,
         *((grid.lon_min, grid.lat_min, grid.lon_max, grid.lat_max, grid.nlon, grid.nlat)
           if grid else (None,) * 6)),
    )  # fmt: skip


def request_fetch(conn: Conn, provider_event_id: str, url: str, reason: str, now: datetime) -> None:
    conn.execute(
        """INSERT INTO control.fetch_request (provider_event_id, url, reason, requested_at)
           VALUES (%s, %s, %s, %s)
           ON CONFLICT (provider_event_id) DO UPDATE SET url = EXCLUDED.url,
               reason = EXCLUDED.reason, requested_at = EXCLUDED.requested_at,
               attempts = 0, last_error = NULL""",
        (provider_event_id, url, reason, now),
    )


def watch(conn: Conn, provider_event_id: str, url: str, now: datetime) -> None:
    """Põe o evento na watchlist e pede o detail dele (que traz o mapa e, daí, o grid)."""
    with conn.transaction():
        conn.execute(
            """INSERT INTO control.watchlist (provider_event_id) VALUES (%s)
               ON CONFLICT DO NOTHING""",
            (provider_event_id,),
        )
        request_fetch(conn, provider_event_id, url, "watch", now)
