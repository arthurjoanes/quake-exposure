"""Posições e classificação entre duas publicações da mesma chave."""

from typing import Any

from fastapi import HTTPException
from psycopg import sql

from quakeexposure.api.regional import asset_json
from quakeexposure.domain.canonical import iso_ms
from quakeexposure.exposure.explain import CAUSES, calculation
from quakeexposure.ingestion.store import Conn

_CHANGES = """WITH changes AS (
    SELECT coalesce(a.asset_id, b.asset_id) AS asset_id,
           b.asset_id IS NOT NULL AS in_before, a.asset_id IS NOT NULL AS in_after,
           b.lon AS before_lon, b.lat AS before_lat, b.currency AS before_currency,
           b.insured_value_minor::text AS before_value, b.eligible AS before_eligible,
           b.qualifying AS before_qualifying, b.mmi AS before_mmi,
           a.lon AS after_lon, a.lat AS after_lat, a.currency AS after_currency,
           a.insured_value_minor::text AS after_value, a.eligible AS after_eligible,
           a.qualifying AS after_qualifying, a.mmi AS after_mmi
    FROM (SELECT * FROM exposure.fact WHERE calculation_id = %(after)s) a
    FULL JOIN (SELECT * FROM exposure.fact WHERE calculation_id = %(before)s) b USING (asset_id)
    WHERE ROW(a.lon, a.lat, a.currency, a.insured_value_minor, a.eligible, a.qualifying, a.mmi)
          IS DISTINCT FROM
          ROW(b.lon, b.lat, b.currency, b.insured_value_minor, b.eligible, b.qualifying, b.mmi)
) """


def publication(conn: Conn, publication_id: str) -> dict[str, Any]:
    calc = calculation(conn, publication_id)
    assert calc is not None
    row = conn.execute(
        """SELECT max(published_at),
                  (SELECT generation FROM serving.current_publication WHERE calculation_id = %s)
           FROM serving.publication_log WHERE calculation_id = %s""",
        (publication_id, publication_id),
    ).fetchone()
    assert row is not None
    published_at, generation = row
    if published_at is None:
        raise HTTPException(404, "cálculo ainda não publicado")
    return {
        "publication_id": publication_id,
        "generation": generation,
        "result": {
            "calculation_id": publication_id,
            "input_vector": calc["input_vector"],
            "status": calc["status"],
            "eligible_count": calc["eligible_count"],
            "qualifying_count": calc["qualifying_count"],
            "qualifying_value_minor": calc["qualifying_value_minor"],
            "calculated_at": iso_ms(calc["calculated_at"]),
            "published_at": iso_ms(published_at),
            "limitations": calc["limitations"],
        },
    }


def comparison(
    conn: Conn,
    before: str | None,
    after: str,
    unavailable_reason: str | None,
    limit: int,
    offset: int,
) -> dict[str, Any]:
    old = publication(conn, before) if before else None
    new = publication(conn, after)
    causes = (
        [
            label
            for field, label in CAUSES.items()
            if old["result"]["input_vector"][field] != new["result"]["input_vector"][field]
        ]
        if old
        else [
            "exclusão de titular"
            if unavailable_reason == "privacy_epoch"
            else "primeira publicação"
        ]
    )
    params = {"before": before, "after": after, "limit": limit, "offset": offset}
    count = 0
    changes: list[dict[str, Any]] = []
    if before is not None:
        total = conn.execute(
            sql.SQL(_CHANGES) + sql.SQL("SELECT count(*) FROM changes"), params
        ).fetchone()
        assert total is not None
        count = total[0]
        rows = conn.execute(
            sql.SQL(_CHANGES)
            + sql.SQL("SELECT * FROM changes ORDER BY asset_id LIMIT %(limit)s OFFSET %(offset)s"),
            params,
        ).fetchall()
        for row in rows:
            asset_id, in_before, in_after, *positions = row
            changes.append(
                {
                    "asset_id": asset_id,
                    "change": "changed"
                    if in_before and in_after
                    else "added"
                    if in_after
                    else "removed",
                    "before": asset_json((asset_id, *positions[:7])) if in_before else None,
                    "after": asset_json((asset_id, *positions[7:])) if in_after else None,
                }
            )
    return {
        "publication_id": after,
        "previous_publication_id": before,
        "previous_available": before is not None,
        "unavailable_reason": unavailable_reason,
        "causes": causes,
        "before": old,
        "after": new,
        "total_changes": count,
        "offset": offset,
        "limit": limit,
        "changes": changes,
    }
