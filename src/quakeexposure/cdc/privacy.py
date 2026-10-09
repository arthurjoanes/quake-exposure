"""Remove os dados pessoais do stage sem perder o envelope de uma transação pendente."""

from typing import Any
from uuid import UUID

from psycopg.types.json import Jsonb

from quakeexposure.ingestion.store import Conn


def redacted_message(message: dict[str, Any]) -> dict[str, Any]:
    """O marcador conserva só a operação, a ordem e as chaves necessárias ao apply."""
    deleting = message["op"] == "d"
    row = message["before"] if deleting else message["after"]
    key = {name: row[name] for name in ("tenant_id", "asset_interval_id")}
    return {
        "erased": True,
        "op": message["op"],
        "transaction": message["transaction"],
        "after": None if deleting else key,
        "before": key if deleting else None,
    }


def stage_payload(conn: Conn, message: dict[str, Any]) -> dict[str, Any]:
    """Após o bloqueio, um dado reentregue nunca volta a ser persistido no stage."""
    for row in (message.get("after"), message.get("before")):
        if not isinstance(row, dict) or not row.get("subject_token"):
            continue
        if conn.execute(
            "SELECT 1 FROM privacy.erase_block WHERE subject_token = %s",
            (UUID(row["subject_token"]),),
        ).fetchone():
            return redacted_message(message)
    return message


def redact_subject(conn: Conn, subject_token: UUID) -> int:
    """Redige pendências sob o lock compartilhado com stage, apply e bloqueio/purge.

    A fingerprint original fica intacta para reconhecer reentregas depois da exclusão.
    """
    with conn.transaction():
        conn.execute("SELECT pg_advisory_xact_lock(hashtext('quake-exposure:cdc-apply'))")
        rows = conn.execute(
            """SELECT transaction_id, total_order, payload FROM control.cdc_stage
               WHERE payload->'after'->>'subject_token' = %s
                  OR payload->'before'->>'subject_token' = %s""",
            (str(subject_token), str(subject_token)),
        ).fetchall()
        for transaction_id, total_order, message in rows:
            conn.execute(
                """UPDATE control.cdc_stage SET payload = %s
                   WHERE transaction_id = %s AND total_order = %s""",
                (Jsonb(redacted_message(message)), transaction_id, total_order),
            )
    return len(rows)
