"""Estado de operação da carteira, sem payload CDC nem erros individuais dos jobs."""

from datetime import datetime
from typing import Any

from quakeexposure.domain.canonical import iso_ms
from quakeexposure.exposure.worker import MAX_ATTEMPTS
from quakeexposure.ingestion.store import Conn
from quakeexposure.status import report


def operations(
    conn: Conn, tenant: str, epochs: tuple[int, int, int], now: datetime
) -> dict[str, Any]:
    shared = report(conn, now)
    row = conn.execute(
        """SELECT count(*) FILTER (WHERE attempts < %(max)s
                                  AND (lease_until IS NULL OR lease_until < %(now)s)),
                  count(*) FILTER (WHERE lease_until >= %(now)s),
                  count(*) FILTER (WHERE attempts >= %(max)s
                                  AND (lease_until IS NULL OR lease_until < %(now)s)),
                  count(*) FILTER (WHERE published_generation > 0)
           FROM control.exposure_job
           WHERE tenant_id = %(tenant)s AND desired_generation > published_generation""",
        {"max": MAX_ATTEMPTS, "now": now, "tenant": tenant},
    ).fetchone()
    assert row is not None
    pending, leased, failed, outdated = row
    publications = conn.execute(
        """SELECT p.calculation_id, p.earthquake_uid, p.stream_scope, p.mode, p.generation,
                  c.status, p.published_at, c.calculated_at
           FROM serving.current_publication p
           JOIN exposure.calculation c USING (calculation_id)
           JOIN portfolio.tenant t ON t.tenant_id = p.tenant_id
           WHERE p.tenant_id = %s AND c.privacy_epoch = t.privacy_epoch
           ORDER BY p.published_at DESC, p.calculation_id""",
        (tenant,),
    ).fetchall()
    return {
        "generated_at": iso_ms(now),
        "tenant_id": tenant,
        "cdc": shared["cdc"],
        "sources": shared["sources"],
        "jobs": {"pending": pending, "leased": leased, "failed": failed, "outdated": outdated},
        "epochs": {"portfolio": epochs[0], "source": epochs[1], "privacy": epochs[2]},
        "last_publication_at": iso_ms(publications[0][6]) if publications else None,
        "publications": [
            {
                "publication_id": publication_id,
                "earthquake_uid": str(uid),
                "stream_scope": scope,
                "mode": mode,
                "generation": generation,
                "status": status,
                "published_at": iso_ms(published),
                "calculated_at": iso_ms(calculated),
                "publication_delay_s": max(0.0, (published - calculated).total_seconds()),
                "calculation_duration_s": None,
            }
            for (
                publication_id,
                uid,
                scope,
                mode,
                generation,
                status,
                published,
                calculated,
            ) in publications
        ],
    }
