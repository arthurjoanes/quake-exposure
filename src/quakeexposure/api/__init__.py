"""API somente leitura, sem login, para 127.0.0.1. O tenant é filtro, não autorização.

Dinheiro sai como string em unidade menor. Nenhuma rota lê a carteira bruta: o subject_token
não chega aqui.
"""

from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any
from uuid import UUID

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.staticfiles import StaticFiles

from quakeexposure.db import ROOT, connect
from quakeexposure.domain.canonical import iso_ms
from quakeexposure.exposure.explain import calculation, diff, previous
from quakeexposure.ingestion.store import Conn
from quakeexposure.status import report

WEB_DIST = ROOT / "web" / "dist"
Scope = Annotated[str, Query(pattern="^(LIVE|REPLAY|FAULT)$")]
ModeQ = Annotated[str, Query(pattern="^(RADIUS_TRIAGE|SHAKEMAP_MMI)$")]


def create_app(
    connector: Callable[[], Conn] = lambda: connect(user="qe_api_read"),
    static_dir: Path | None = WEB_DIST,
) -> FastAPI:
    app = FastAPI(title="QuakeExposure", docs_url="/api/docs", openapi_url="/api/openapi.json")

    def db() -> Iterator[Conn]:
        with connector() as conn, conn.transaction():
            yield conn

    Db = Annotated[Conn, Depends(db)]

    @app.get("/api/tenants")
    def list_tenants(conn: Db) -> list[dict[str, Any]]:
        rows = conn.execute("SELECT tenant_id, name FROM portfolio.tenant ORDER BY 1").fetchall()
        return [{"tenant_id": t, "name": n} for t, n in rows]

    @app.get("/api/events")
    def list_events(
        conn: Db, scope: Scope = "LIVE", limit: Annotated[int, Query(ge=1, le=2000)] = 500
    ) -> list[dict[str, Any]]:
        """Os eventos com publicação vêm primeiro: o limite corta só os que não têm cálculo."""
        rows = conn.execute(
            """SELECT e.earthquake_uid, e.preferred_id, e.lifecycle, e.event_time, r.lon, r.lat,
                      r.depth_km, r.mag, r.event_type, r.source_updated_at, m.product_revision_id,
                      (SELECT count(*) FROM quake.event_revision x
                       WHERE x.stream_scope = e.stream_scope
                         AND x.earthquake_uid = e.earthquake_uid)
               FROM quake.earthquake e
               JOIN quake.event_revision r
                 ON r.stream_scope = e.stream_scope AND r.revision_id = e.current_revision_id
               LEFT JOIN quake.event_map m
                 ON m.stream_scope = e.stream_scope AND m.earthquake_uid = e.earthquake_uid
               WHERE e.stream_scope = %s AND e.merged_into IS NULL
               ORDER BY EXISTS (SELECT 1 FROM serving.current_publication p
                                WHERE p.stream_scope = e.stream_scope
                                  AND p.earthquake_uid = e.earthquake_uid) DESC,
                        e.event_time DESC
               LIMIT %s""",
            (scope, limit),
        ).fetchall()
        return [
            {
                "earthquake_uid": str(uid),
                "preferred_id": pref,
                "stream_scope": scope,
                "lifecycle": life,
                "event_time": iso_ms(time),
                "lon": lon,
                "lat": lat,
                "depth_km": depth,
                "mag": mag,
                "event_type": etype,
                "source_updated_at": iso_ms(updated),
                "product_revision_id": product,
                "revisions": n,
            }
            for uid, pref, life, time, lon, lat, depth, mag, etype, updated, product, n in rows
        ]

    @app.get("/api/events/{uid}/revisions")
    def list_revisions(uid: UUID, conn: Db, scope: Scope = "LIVE") -> list[dict[str, Any]]:
        rows = conn.execute(
            """SELECT revision_id, provider_event_id, source_updated_at, mag, lon, lat, status,
                      event_type
               FROM quake.event_revision WHERE stream_scope = %s AND earthquake_uid = %s
               ORDER BY source_updated_at DESC""",
            (scope, uid),
        ).fetchall()
        return [
            {
                "revision_id": rid,
                "provider_event_id": pid,
                "source_updated_at": iso_ms(updated),
                "mag": mag,
                "lon": lon,
                "lat": lat,
                "status": status,
                "event_type": etype,
            }
            for rid, pid, updated, mag, lon, lat, status, etype in rows
        ]

    @app.get("/api/publications/current")
    def current_publication(
        conn: Db, tenant: str, earthquake_uid: UUID, mode: ModeQ, scope: Scope = "LIVE"
    ) -> dict[str, Any]:
        row = conn.execute(
            """SELECT calculation_id, generation, published_at FROM serving.current_publication
               WHERE tenant_id = %s AND earthquake_uid = %s AND stream_scope = %s AND mode = %s""",
            (tenant, earthquake_uid, scope, mode),
        ).fetchone()
        if row is None:
            raise HTTPException(404, "sem publicação para este evento, tenant e modo")
        calc_id, generation, published_at = row
        _own(conn, calc_id, tenant)
        return {
            "publication_id": calc_id,
            "generation": generation,
            "result": _result(conn, calc_id, published_at),
        }

    @app.get("/api/publications/{publication_id}/assets")
    def publication_assets(publication_id: str, tenant: str, conn: Db) -> dict[str, Any]:
        _own(conn, publication_id, tenant)
        rows = conn.execute(
            """SELECT asset_id, lon, lat, currency, insured_value_minor::text, eligible,
                      qualifying, mmi
               FROM exposure.fact WHERE calculation_id = %s ORDER BY asset_id""",
            (publication_id,),
        ).fetchall()
        return {
            "publication_id": publication_id,
            "assets": [
                {
                    "asset_id": a,
                    "lon": lon,
                    "lat": lat,
                    "currency": cur,
                    "insured_value_minor": value,
                    "eligible": eligible,
                    "qualifying": qualifying,
                    "mmi": mmi,
                }
                for a, lon, lat, cur, value, eligible, qualifying, mmi in rows
            ],
        }

    @app.get("/api/publications/{publication_id}/diff")
    def publication_diff(publication_id: str, tenant: str, conn: Db) -> dict[str, Any]:
        """Antes e depois: a publicação anterior desta mesma chave e o que mudou."""
        _own(conn, publication_id, tenant)
        before = previous(conn, publication_id)
        if before is not None:
            try:
                _own(conn, before, tenant)
            except HTTPException:
                before = None
        d = diff(conn, before, publication_id)
        return {
            "publication_id": publication_id,
            "previous_publication_id": before,
            "causes": d["causes"],
            "entered": d["entered"],
            "left": d["left"],
        }

    @app.get("/api/status")
    def status(conn: Db) -> dict[str, Any]:
        now = datetime.now(UTC)
        r = report(conn, now)
        outdated = conn.execute(
            """SELECT count(*) FROM control.exposure_job
               WHERE published_generation > 0 AND desired_generation > published_generation"""
        ).fetchone()
        assert outdated is not None
        jobs = r["exposure_jobs"]
        r["jobs_pending"] = jobs["pending"] + jobs["leased"]
        r["jobs_failed"] = jobs["exhausted"]
        r["publications_outdated"] = outdated[0]
        r["generated_at"] = iso_ms(now)
        return r

    if static_dir is not None and static_dir.is_dir():
        app.mount("/", StaticFiles(directory=static_dir, html=True), name="web")
    return app


def _own(conn: Conn, publication_id: str, tenant: str) -> tuple[str, UUID, str, str]:
    # Mantido até o fim da resposta: block/purge não pode mudar a privacy_epoch
    # entre este guard e as leituras de fatos, totais e diff.
    conn.execute(
        "SELECT pg_advisory_xact_lock_shared(hashtextextended(%s, 0))",
        (f"privacy-api:{tenant}",),
    )
    row = conn.execute(
        """SELECT c.tenant_id, c.earthquake_uid, c.stream_scope, c.mode,
                  c.privacy_epoch = t.privacy_epoch
           FROM exposure.calculation c JOIN portfolio.tenant t USING (tenant_id)
           WHERE c.calculation_id = %s""",
        (publication_id,),
    ).fetchone()
    if row is None or row[0] != tenant:
        raise HTTPException(404, "publicação não encontrada para este tenant")
    if not row[4]:
        raise HTTPException(409, "recalculando após exclusão")
    return row[0], row[1], row[2], row[3]


def _result(conn: Conn, calculation_id: str, published_at: datetime) -> dict[str, Any]:
    """O exposure-result do contrato."""
    calc = calculation(conn, calculation_id)
    assert calc is not None
    return {
        "calculation_id": calc["calculation_id"],
        "input_vector": calc["input_vector"],
        "status": calc["status"],
        "eligible_count": calc["eligible_count"],
        "qualifying_count": calc["qualifying_count"],
        "qualifying_value_minor": calc["qualifying_value_minor"],
        "calculated_at": iso_ms(calc["calculated_at"]),
        "published_at": iso_ms(published_at),
        "limitations": calc["limitations"],
    }


app = create_app()
