"""Consumer Kafka sem autocommit: o banco confirma o stage antes do offset."""

import logging
import os
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, Protocol, cast

from confluent_kafka import Consumer
from psycopg.pq import TransactionStatus

from quakeexposure.cdc.monitor import refresh
from quakeexposure.cdc.store import TOPICS, CdcError, apply_pending, stage
from quakeexposure.ingestion.store import Conn

log = logging.getLogger(__name__)


class Message(Protocol):
    def topic(self) -> str: ...
    def value(self) -> bytes | str | None: ...
    def error(self) -> Any: ...


class KafkaConsumer(Protocol):
    def subscribe(self, topics: list[str]) -> None: ...
    def poll(self, timeout: float) -> Message | None: ...
    def commit(self, *, message: Message, asynchronous: bool) -> Any: ...
    def close(self) -> None: ...


def process_message(
    conn: Conn, consumer: KafkaConsumer, message: Message, *, now: datetime | None = None
) -> int:
    """Falha antes do commit Kafka implica replay; o stage absorve a reentrega."""
    at = now or datetime.now(UTC)
    _stage_message(conn, message, at)
    applied = apply_pending(conn, now=at)
    consumer.commit(message=message, asynchronous=False)
    return applied


def _stage_message(conn: Conn, message: Message, at: datetime) -> None:
    if not conn.autocommit or conn.info.transaction_status != TransactionStatus.IDLE:
        raise CdcError("consumer exige conexão autocommit sem transação externa")
    if message.error():
        raise CdcError(f"erro Kafka: {message.error()}")
    payload = message.value()
    if payload is None:
        raise CdcError("tombstone inesperado: tombstones.on.delete deve ser false")
    stage(conn, message.topic(), payload, now=at)


def _flush(conn: Conn, consumer: KafkaConsumer, latest: dict[str, Message], at: datetime) -> None:
    count = apply_pending(conn, now=at)
    # Tópicos têm uma partição. Confirmar o último offset confirma só mensagens já
    # persistidas; falha no meio desta lista causa replay seguro dos outros tópicos.
    for message in latest.values():
        consumer.commit(message=message, asynchronous=False)
    latest.clear()
    if count:
        log.info("CDC: %d transação(ões) aplicada(s)", count)


def run_forever(
    conn: Conn,
    *,
    bootstrap_servers: str | None = None,
    group_id: str | None = None,
    consumer: KafkaConsumer | None = None,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    stopped: Callable[[], bool] = lambda: False,
    monitor: Callable[[Conn], None] = refresh,
    monotonic: Callable[[], float] = time.monotonic,
    batch_size: int = 250,
    commit_every: float = 1.0,
) -> None:
    """Consome os três tópicos; configuração inválida termina com erro explícito."""
    if batch_size < 1 or commit_every <= 0:
        raise ValueError("batch_size e commit_every precisam ser positivos")
    client: KafkaConsumer = (
        consumer
        if consumer is not None
        else cast(
            KafkaConsumer,
            Consumer(
                {
                    "bootstrap.servers": bootstrap_servers
                    or os.environ.get("QE_KAFKA_BOOTSTRAP_SERVERS", "kafka:9092"),
                    "group.id": group_id
                    or os.environ.get("QE_CDC_GROUP_ID", "quake-exposure-cdc-v1"),
                    "enable.auto.commit": False,
                    "enable.auto.offset.store": False,
                    "auto.offset.reset": "earliest",
                }
            ),
        )
    )
    try:
        client.subscribe(list(TOPICS))
        apply_pending(conn, now=clock())
        last_check = float("-inf")
        last_commit = monotonic()
        latest: dict[str, Message] = {}
        staged = 0
        while not stopped():
            if monotonic() - last_check >= 10:
                monitor(conn)
                last_check = monotonic()
            message = client.poll(min(1.0, commit_every))
            if message is not None:
                _stage_message(conn, message, clock())
                latest[message.topic()] = message
                staged += 1
            if latest and (staged >= batch_size or monotonic() - last_commit >= commit_every):
                _flush(conn, client, latest, clock())
                staged = 0
                last_commit = monotonic()
        if latest:
            _flush(conn, client, latest, clock())
    finally:
        client.close()
