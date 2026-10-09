"""Mensagens Debezium reentregáveis e aplicação completa, em ordem de epoch por tenant."""

import hashlib
import json
from collections import Counter
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from psycopg.types.json import Jsonb

from quakeexposure.domain.rule import DEFAULT_RULE, Mode
from quakeexposure.exposure import jobs
from quakeexposure.ingestion.store import Conn
from quakeexposure.portfolio.store import Tenant
from quakeexposure.portfolio.synthetic import TENANTS

COLLECTIONS = frozenset({"public.asset_interval", "public.portfolio_clock"})
TOPICS = (*(f"portfolio.{table}" for table in sorted(COLLECTIONS)), "portfolio.transaction")


class CdcError(ValueError):
    """Contrato CDC inválido: parar para corrigir a origem, sem avançar o offset."""


def decode(payload: bytes | str | dict[str, Any]) -> dict[str, Any]:
    value: Any = json.loads(payload) if isinstance(payload, (bytes, str)) else payload
    if isinstance(value, dict) and "payload" in value:
        value = value["payload"]
    if not isinstance(value, dict):
        raise CdcError("mensagem CDC sem objeto JSON")
    return value


def _integer(value: Any, name: str, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise CdcError(f"{name} inválido no CDC")
    return value


def transaction_key(message: dict[str, Any], *, end: bool) -> str:
    """Debezium 3.6 emite xid:LSN, com LSN distinto a cada linha.

    xid + instante do commit agrupa os tópicos e continua distinto após wrap do xid.
    END.ts_ms e source.ts_ms são o mesmo instante da origem, confirmado no smoke real.
    """
    metadata = message if end else message.get("transaction")
    if not isinstance(metadata, dict):
        raise CdcError("linha CDC sem transaction.metadata")
    value = metadata.get("id")
    if not isinstance(value, str) or not value:
        raise CdcError("transaction_id CDC ausente")
    xid, separator, lsn = value.partition(":")
    if not separator:
        return value
    if not xid.isdecimal() or not lsn.isdecimal():
        raise CdcError("transaction.id xid:LSN inválido")
    source = message if end else message.get("source")
    if not isinstance(source, dict):
        raise CdcError("linha CDC sem source")
    committed_at = _integer(source.get("ts_ms"), "timestamp do commit")
    return f"{xid}:{committed_at}"


def timestamp(value: Any) -> datetime:
    """timestamptz vem como ZonedTimestamp; aceita também MicrosTimestamp sem float."""
    try:
        if isinstance(value, int) and not isinstance(value, bool):
            return datetime(1970, 1, 1, tzinfo=UTC) + timedelta(microseconds=value)
        if isinstance(value, str):
            instant = datetime.fromisoformat(value)
            if instant.tzinfo is not None:
                return instant.astimezone(UTC)
    except (ValueError, OverflowError) as exc:
        raise CdcError("timestamp inválido no CDC") from exc
    raise CdcError("timestamp CDC exige timezone")


def _end_counts(message: dict[str, Any]) -> tuple[int, dict[str, int]]:
    count = _integer(message.get("event_count"), "event_count")
    entries = message.get("data_collections")
    if entries is None and count == 0:
        return count, {}
    if not isinstance(entries, list):
        raise CdcError("END sem contagens por tabela")
    counts: dict[str, int] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            raise CdcError("contagem por tabela inválida")
        table = entry.get("data_collection")
        if table not in COLLECTIONS or table in counts:
            raise CdcError("END com tabela inesperada ou repetida")
        counts[table] = _integer(entry.get("event_count"), "event_count por tabela")
    if sum(counts.values()) != count:
        raise CdcError("END com soma das tabelas diferente de event_count")
    return count, counts


def stage(
    conn: Conn, topic: str, payload: bytes | str | dict[str, Any], *, now: datetime | None = None
) -> bool:
    """Persiste uma mensagem antes do offset Kafka. BEGIN não altera a plataforma.

    Retorna True para linha nova/END novo e False para BEGIN ou reentrega idêntica.
    O stage pode receber END antes dos dados de qualquer tópico.
    """
    now = now or datetime.now(UTC)
    if topic not in TOPICS:
        raise CdcError("tópico fora da publicação da carteira")
    message = decode(payload)
    end = topic == "portfolio.transaction"
    if end and message.get("status") == "BEGIN":
        return False
    if end:
        if message.get("status") != "END":
            raise CdcError("metadado CDC diferente de BEGIN/END")
        transaction_id = transaction_key(message, end=True)
        count, counts = _end_counts(message)
    else:
        if message.get("op") == "r":
            raise CdcError("op=r proibido: snapshot.mode deve ser no_data; refaça o bootstrap")
        if message.get("op") not in {"c", "u", "d"}:
            raise CdcError("op CDC desconhecida")
        transaction = message.get("transaction")
        if not isinstance(transaction, dict):
            raise CdcError("linha CDC sem transaction.metadata")
        transaction_id = transaction_key(message, end=False)
        order = _integer(transaction.get("total_order"), "total_order", 1)
        collection = topic.removeprefix("portfolio.")
        source = message.get("source")
        if isinstance(source, dict) and source.get("table") != collection.split(".")[1]:
            raise CdcError("tabela da mensagem diverge do tópico")
        stable = {k: v for k, v in message.items() if k not in {"ts_ms", "ts_us", "ts_ns"}}
        fingerprint = hashlib.sha256(
            json.dumps(stable, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
    if not isinstance(transaction_id, str) or not transaction_id:
        raise CdcError("transaction_id CDC ausente")
    with conn.transaction():
        conn.execute(
            """INSERT INTO control.cdc_transaction (transaction_id, first_seen_at)
               VALUES (%s, %s) ON CONFLICT DO NOTHING""",
            (transaction_id, now),
        )
        record = conn.execute(
            """SELECT expected_count, expected_counts, applied_at FROM control.cdc_transaction
               WHERE transaction_id = %s FOR UPDATE""",
            (transaction_id,),
        ).fetchone()
        assert record is not None
        if end:
            if record[0] is not None:
                if record[:2] != (count, counts):
                    raise CdcError("END reentregue com contagens diferentes")
                return False
            conn.execute(
                """UPDATE control.cdc_transaction SET expected_count = %s, expected_counts = %s,
                       ended_at = %s WHERE transaction_id = %s""",
                (count, Jsonb(counts), now, transaction_id),
            )
            return True
        existing = conn.execute(
            """SELECT payload_sha256 FROM control.cdc_stage
               WHERE transaction_id = %s AND total_order = %s""",
            (transaction_id, order),
        ).fetchone()
        if existing is not None:
            if existing[0] != fingerprint:
                raise CdcError("linha reentregue com conteúdo diferente")
            return False
        if record[2] is not None:
            raise CdcError("linha adicional para transação já aplicada")
        conn.execute(
            """INSERT INTO control.cdc_stage
                   (transaction_id, total_order, collection, payload, payload_sha256, staged_at)
               VALUES (%s, %s, %s, %s, %s, %s)""",
            (transaction_id, order, collection, Jsonb(message), fingerprint, now),
        )
    return True


def apply_pending(conn: Conn, *, now: datetime | None = None) -> int:
    """Drena transações completas; lacunas de epoch ficam pendentes e visíveis no status."""
    now = now or datetime.now(UTC)
    applied = 0
    while True:
        candidates = conn.execute(
            """SELECT transaction_id FROM control.cdc_transaction WHERE applied_at IS NULL
               AND ended_at IS NOT NULL ORDER BY first_seen_at, transaction_id"""
        ).fetchall()
        progress = 0
        for (transaction_id,) in candidates:
            try:
                progress += int(_apply(conn, transaction_id, now))
            except CdcError as exc:
                conn.execute(
                    """UPDATE control.cdc_transaction SET waiting_reason = %s
                       WHERE transaction_id = %s""",
                    (str(exc), transaction_id),
                )
                raise
        applied += progress
        if not progress:
            return applied


def _wait(conn: Conn, transaction_id: str, reason: str) -> bool:
    conn.execute(
        "UPDATE control.cdc_transaction SET waiting_reason = %s WHERE transaction_id = %s",
        (reason, transaction_id),
    )
    return False


def _apply(conn: Conn, transaction_id: str, now: datetime) -> bool:
    with conn.transaction():
        conn.execute("SELECT pg_advisory_xact_lock(hashtext('quake-exposure:cdc-apply'))")
        record = conn.execute(
            """SELECT expected_count, expected_counts, applied_at FROM control.cdc_transaction
               WHERE transaction_id = %s FOR UPDATE""",
            (transaction_id,),
        ).fetchone()
        assert record is not None
        expected, counts, applied_at = record
        if applied_at is not None:
            return False
        rows = conn.execute(
            """SELECT total_order, collection, payload FROM control.cdc_stage
               WHERE transaction_id = %s ORDER BY total_order""",
            (transaction_id,),
        ).fetchall()
        actual = Counter(table for _, table, _ in rows)
        if any(actual[table] > counts.get(table, 0) for table in actual):
            raise CdcError("stage excedeu a contagem por tabela do END")
        if len(rows) != expected or dict(actual) != {k: v for k, v in counts.items() if v}:
            return _wait(
                conn, transaction_id, "aguardando linhas: contagens por tabela incompletas"
            )
        if [order for order, _, _ in rows] != list(range(1, expected + 1)):
            raise CdcError("total_order não é contíguo na transação")
        clocks: dict[str, dict[str, Any]] = {}
        assets: list[dict[str, Any]] = []
        for _, table, message in rows:
            if table == "public.portfolio_clock":
                clock = message.get("after")
                if message.get("op") == "d" or not isinstance(clock, dict):
                    raise CdcError("DELETE de portfolio_clock proibido")
                tenant_id = str(clock["tenant_id"])
                if tenant_id in clocks:
                    raise CdcError("mais de um clock por tenant na transação")
                clocks[tenant_id] = clock
            else:
                assets.append(message)
        for asset in assets:
            row = asset.get("before") if asset["op"] == "d" else asset.get("after")
            if not isinstance(row, dict) or str(row.get("tenant_id")) not in clocks:
                raise CdcError("transação asset_interval sem linha de portfolio_clock do tenant")
        for tenant_id, clock in sorted(clocks.items()):
            epoch = _integer(clock.get("epoch"), "epoch", 1)
            source_epoch = _integer(clock.get("source_epoch"), "source_epoch", 1)
            current = conn.execute(
                """SELECT portfolio_epoch, source_epoch FROM portfolio.tenant
                   WHERE tenant_id = %s FOR UPDATE""",
                (tenant_id,),
            ).fetchone()
            if current is None:
                if epoch != 1:
                    return _wait(
                        conn, transaction_id, f"epoch fora de sequência: {tenant_id}, 0→{epoch}"
                    )
            elif current[1] != source_epoch:
                return _wait(
                    conn, transaction_id, f"source_epoch divergente: {tenant_id}; reset manual"
                )
            elif epoch != current[0] + 1:
                return _wait(
                    conn,
                    transaction_id,
                    f"epoch fora de sequência: {tenant_id}, {current[0]}→{epoch}",
                )
        names = {tenant: name for tenant, name, _, _ in TENANTS}
        for tenant_id, clock in sorted(clocks.items()):
            conn.execute(
                """INSERT INTO portfolio.tenant (tenant_id, name, source_epoch)
                   VALUES (%s, %s, %s) ON CONFLICT DO NOTHING""",
                (tenant_id, names.get(tenant_id, tenant_id), clock["source_epoch"]),
            )
        # Retira os intervalos afetados primeiro: fechar+criar funciona em qualquer total_order,
        # sem aceitar sobreposição no estado final que será inserido abaixo.
        latest: dict[tuple[str, str], dict[str, Any]] = {}
        for message in assets:
            row = message["before"] if message["op"] == "d" else message["after"]
            key = (str(row["tenant_id"]), str(row["asset_interval_id"]))
            conn.execute(
                """DELETE FROM portfolio.asset_interval
                   WHERE tenant_id = %s AND asset_interval_id = %s""",
                key,
            )
            latest[key] = message
            if message["op"] == "d":
                conn.execute(
                    """DELETE FROM portfolio.asset_revision
                       WHERE tenant_id = %s AND asset_interval_id = %s""",
                    key,
                )
            else:
                _revision(conn, row, clocks[key[0]])
        for message in latest.values():
            if message["op"] != "d":
                _interval(conn, message["after"])
        for tenant_id, clock in sorted(clocks.items()):
            changed = conn.execute(
                """UPDATE portfolio.tenant SET portfolio_epoch = %s
                   WHERE tenant_id = %s AND portfolio_epoch = %s AND source_epoch = %s
                   RETURNING privacy_epoch""",
                (clock["epoch"], tenant_id, clock["epoch"] - 1, clock["source_epoch"]),
            ).fetchone()
            if changed is None:
                raise CdcError("CAS do clock da carteira falhou")
            _dirty(conn, Tenant(tenant_id, clock["epoch"], clock["source_epoch"], changed[0]), now)
        conn.execute(
            """UPDATE control.cdc_transaction SET applied_at = %s, waiting_reason = NULL
               WHERE transaction_id = %s""",
            (now, transaction_id),
        )
        # A chave e o hash bastam para reconhecer replay; não guardar PII após o apply.
        conn.execute(
            "UPDATE control.cdc_stage SET payload = NULL WHERE transaction_id = %s",
            (transaction_id,),
        )
    return True


def _blocked(conn: Conn, row: dict[str, Any]) -> bool:
    return (
        conn.execute(
            "SELECT 1 FROM privacy.erase_block WHERE subject_token = %s",
            (UUID(row["subject_token"]),),
        ).fetchone()
        is not None
    )


def _revision(conn: Conn, row: dict[str, Any], clock: dict[str, Any]) -> None:
    if _blocked(conn, row):
        return
    conn.execute(
        """INSERT INTO portfolio.asset_revision
               (tenant_id, asset_interval_id, row_version, asset_id,
               subject_token, known_epoch, source_epoch, valid_from, valid_to, lon, lat,
               insured_value_minor, currency, is_deleted, updated_at)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
        (
            row["tenant_id"],
            row["asset_interval_id"],
            row["row_version"],
            row["asset_id"],
            UUID(row["subject_token"]),
            clock["epoch"],
            clock["source_epoch"],
            timestamp(row["valid_from"]),
            timestamp(row["valid_to"]) if row["valid_to"] else None,
            row["lon"],
            row["lat"],
            row["insured_value_minor"],
            row["currency"],
            row["is_deleted"],
            timestamp(row["updated_at"]),
        ),
    )


def _interval(conn: Conn, row: dict[str, Any]) -> None:
    if _blocked(conn, row):
        return
    conn.execute(
        """INSERT INTO portfolio.asset_interval
               (tenant_id, asset_interval_id, asset_id, row_version,
                valid_from, valid_to, is_deleted)
           VALUES (%s, %s, %s, %s, %s, %s, %s)""",
        (
            row["tenant_id"],
            row["asset_interval_id"],
            row["asset_id"],
            row["row_version"],
            timestamp(row["valid_from"]),
            timestamp(row["valid_to"]) if row["valid_to"] else None,
            row["is_deleted"],
        ),
    )


def _dirty(conn: Conn, tenant: Tenant, now: datetime) -> None:
    """Somente eventos em escopo; a geração muda no mesmo commit da carteira."""
    conn.execute("SELECT pg_advisory_xact_lock(hashtext('quake-exposure:mark'))")
    for target in jobs.targets_in_scope(conn, now):
        for mode in Mode:
            vector = jobs.vector_for(target, tenant, mode, DEFAULT_RULE)
            if vector is None:
                continue
            conn.execute(
                """INSERT INTO control.exposure_job AS j (tenant_id, earthquake_uid, stream_scope,
                       mode, desired_calculation_id, desired_generation, marked_at)
                   VALUES (%s, %s, %s, %s, %s, 1, %s)
                   ON CONFLICT (tenant_id, earthquake_uid, stream_scope, mode) DO UPDATE SET
                       desired_calculation_id = EXCLUDED.desired_calculation_id,
                       desired_generation = j.desired_generation + 1,
                       marked_at = EXCLUDED.marked_at,
                       attempts = 0, last_error = NULL
                   WHERE j.desired_calculation_id <> EXCLUDED.desired_calculation_id""",
                (
                    tenant.tenant_id,
                    target.earthquake_uid,
                    target.stream_scope,
                    mode,
                    vector.calculation_id,
                    now,
                ),
            )
