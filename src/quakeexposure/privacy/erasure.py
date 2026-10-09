"""Exclusão idempotente: BLOCKED → PURGED → CLOSED, com ledger antes do bloqueio local."""

import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from psycopg.types.json import Jsonb

from quakeexposure.cdc.privacy import redact_subject
from quakeexposure.ingestion.store import Conn
from quakeexposure.portfolio.source import block_subject, erase_subject
from quakeexposure.portfolio.synthetic import TENANTS
from quakeexposure.privacy.ledger import Ledger, LedgerEntry

REQUEST_NAMESPACE = uuid.UUID("f5eb030d-7c11-4fc4-b7df-5d2cbe4e806b")


def _lock_apply(conn: Conn) -> None:
    conn.execute("SELECT pg_advisory_xact_lock(hashtext('quake-exposure:cdc-apply'))")


def _lock_readers(conn: Conn, tenant_id: str) -> None:
    conn.execute(
        "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (f"privacy-api:{tenant_id}",)
    )


def _dirty_all(conn: Conn, tenant_id: str, at: datetime) -> None:
    # Primeiro o lock dos jobs: um worker que ganhou o CAS termina antes da purga.
    conn.execute(
        """UPDATE control.exposure_job SET desired_generation = desired_generation + 1,
               lease_until = NULL, attempts = 0, last_error = NULL, marked_at = %s
           WHERE tenant_id = %s""",
        (at, tenant_id),
    )


def block_platform(conn: Conn, entry: LedgerEntry) -> bool:
    """Bloqueio, privacy_epoch e invalidação dos jobs entram no mesmo COMMIT."""
    token = uuid.UUID(entry.subject_token)
    at = datetime.fromisoformat(entry.blocked_at)
    with conn.transaction():
        _lock_apply(conn)
        _lock_readers(conn, entry.tenant_id)
        name = next(
            (name for tenant, name, _, _ in TENANTS if tenant == entry.tenant_id), entry.tenant_id
        )
        conn.execute(
            """INSERT INTO portfolio.tenant (tenant_id, name, source_epoch)
               VALUES (%s, %s, %s) ON CONFLICT DO NOTHING""",
            (entry.tenant_id, name, entry.source_epoch),
        )
        conn.execute(
            """INSERT INTO privacy.erase_block (subject_token, tenant_id, asset_id, blocked_at)
               VALUES (%s, %s, %s, %s) ON CONFLICT (subject_token) DO UPDATE SET
                   tenant_id = EXCLUDED.tenant_id, asset_id = EXCLUDED.asset_id""",
            (token, entry.tenant_id, entry.asset_id, at),
        )
        created = conn.execute(
            """INSERT INTO privacy.erasure_request (subject_token, request_id, tenant_id,
                   asset_id, source_epoch, status, blocked_at, source_command_id)
               VALUES (%s, %s, %s, %s, %s, 'BLOCKED', %s, %s)
               ON CONFLICT (subject_token) DO NOTHING RETURNING 1""",
            (
                token,
                uuid.UUID(entry.request_id),
                entry.tenant_id,
                entry.asset_id,
                entry.source_epoch,
                at,
                source_command_id(entry),
            ),
        ).fetchone()
        if created:
            _dirty_all(conn, entry.tenant_id, at)
            conn.execute(
                """UPDATE portfolio.tenant SET privacy_epoch = privacy_epoch + 1
                   WHERE tenant_id = %s""",
                (entry.tenant_id,),
            )
        return created is not None


def source_command_id(entry: LedgerEntry) -> uuid.UUID:
    return uuid.uuid5(uuid.UUID(entry.request_id), "source-purge")


def purge_platform(conn: Conn, entry: LedgerEntry, at: datetime) -> dict[str, int]:
    """Apaga revisões e fatos do ativo, e retira valores dos cálculos anteriores do tenant."""
    token = uuid.UUID(entry.subject_token)
    with conn.transaction():
        _lock_apply(conn)
        _lock_readers(conn, entry.tenant_id)
        _dirty_all(conn, entry.tenant_id, at)
        asset_ids = {entry.asset_id}
        asset_ids.update(
            str(row[0])
            for row in conn.execute(
                "SELECT DISTINCT asset_id FROM portfolio.asset_revision WHERE subject_token = %s",
                (token,),
            )
        )
        conn.execute(
            "DELETE FROM portfolio.asset_interval WHERE tenant_id = %s AND asset_id = ANY(%s)",
            (entry.tenant_id, sorted(asset_ids)),
        )
        revisions = conn.execute(
            "DELETE FROM portfolio.asset_revision WHERE subject_token = %s", (token,)
        ).rowcount
        facts = conn.execute(
            """DELETE FROM exposure.fact f USING exposure.calculation c
               WHERE f.calculation_id = c.calculation_id AND c.tenant_id = %s
                 AND f.asset_id = ANY(%s)""",
            (entry.tenant_id, sorted(asset_ids)),
        ).rowcount
        conn.execute(
            """DELETE FROM exposure.calculation_total t USING exposure.calculation c,
                   portfolio.tenant p
               WHERE t.calculation_id = c.calculation_id AND c.tenant_id = p.tenant_id
                 AND p.tenant_id = %s AND c.privacy_epoch < p.privacy_epoch""",
            (entry.tenant_id,),
        )
        conn.execute(
            """UPDATE exposure.calculation c SET status = 'REDACTED_BY_ERASURE',
                   eligible_count = NULL, qualifying_count = NULL,
                   limitations = ARRAY['Valores removidos após exclusão de titular.']
               FROM portfolio.tenant p WHERE c.tenant_id = p.tenant_id
                 AND p.tenant_id = %s AND c.privacy_epoch < p.privacy_epoch""",
            (entry.tenant_id,),
        )
        redact_subject(conn, token)
        conn.execute(
            """UPDATE privacy.erasure_request SET status = 'PURGED', purged_at = %s
               WHERE subject_token = %s AND status = 'BLOCKED'""",
            (at, token),
        )
    return {"revisions": revisions, "facts": facts}


def erase(
    conn: Conn,
    source: Conn,
    subject_token: uuid.UUID,
    *,
    ledger: Ledger | None = None,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
    after_step: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Retomável após qualquer etapa; after_step é um failpoint exclusivo dos testes."""
    ledger = ledger or Ledger()
    conn.execute("SELECT pg_advisory_lock(hashtext('quake-exposure:erase-ledger'))")
    try:
        identity = block_subject(source, subject_token)
        clock = source.execute(
            "SELECT source_epoch FROM public.portfolio_clock WHERE tenant_id = %s",
            (identity["tenant_id"],),
        ).fetchone()
        assert clock is not None
        entry = ledger.append(
            LedgerEntry(
                str(uuid.uuid5(REQUEST_NAMESPACE, str(subject_token))),
                str(subject_token),
                identity["tenant_id"],
                identity["asset_id"],
                int(clock[0]),
                now().isoformat(),
            )
        )
        block_platform(conn, entry)
        row = conn.execute(
            "SELECT status, receipt FROM privacy.erasure_request WHERE subject_token = %s",
            (subject_token,),
        ).fetchone()
        assert row is not None
        if row[0] == "CLOSED":
            receipt: dict[str, Any] = row[1]
            return receipt
        if after_step is not None:
            after_step("BLOCKED")
        source_result = erase_subject(source, subject_token, source_command_id(entry))
        if row[0] == "BLOCKED":
            purge_platform(conn, entry, now())
        with conn.transaction():
            conn.execute(
                """UPDATE privacy.erasure_request SET source_command_epoch = %s
                   WHERE subject_token = %s""",
                (source_result.epoch, subject_token),
            )
        if after_step is not None:
            after_step("PURGED")
        at = now()
        receipt = {
            "request_id": entry.request_id,
            "subject_token": entry.subject_token,
            "tenant_id": entry.tenant_id,
            "status": "CLOSED",
            "blocked_at": entry.blocked_at,
            "closed_at": at.isoformat(),
            "source_command_epoch": source_result.epoch,
            "residuals": {
                "kafka": {
                    "retention_days": 7,
                    "eligible_for_expiry_at": (at + timedelta(days=7)).isoformat(),
                    "note": "A remoção depende da limpeza dos segmentos do broker.",
                },
                "wal": "Pode conter registros até consumo do slot e reciclagem dos segmentos.",
                "dumps": "Backups anteriores podem conter dados; restore-verify reaplica o ledger.",
                "ledger": "Mantém o token bloqueado para impedir reintrodução após restore.",
            },
        }
        with conn.transaction():
            conn.execute(
                """UPDATE privacy.erasure_request SET status = 'CLOSED', closed_at = %s,
                       receipt = %s WHERE subject_token = %s""",
                (at, Jsonb(receipt), subject_token),
            )
        if after_step is not None:
            after_step("CLOSED")
        return receipt
    finally:
        conn.execute("SELECT pg_advisory_unlock(hashtext('quake-exposure:erase-ledger'))")


def replay_ledger(conn: Conn, ledger: Ledger) -> dict[str, int]:
    """Reaplica bloqueios e purgas numa restauração isolada, sem tocar a origem viva."""
    result = {"subjects": 0, "revisions": 0, "facts": 0}
    for entry in ledger.entries():
        block_platform(conn, entry)
        purged = purge_platform(conn, entry, datetime.now(UTC))
        result["subjects"] += 1
        for key in ("revisions", "facts"):
            result[key] += purged[key]
    return result
