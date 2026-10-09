"""Origem, Kafka e Debezium reais no projeto descartável dos testes."""

from __future__ import annotations

import json
import os
import socket
import time
import uuid
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from threading import Event
from typing import TYPE_CHECKING, Any

import httpx
import psycopg
import pytest
from confluent_kafka import Consumer, TopicPartition

from quakeexposure.db import ROOT
from quakeexposure.portfolio.source import migrate_source

if TYPE_CHECKING:
    from .conftest import Database

TOPICS = (
    "portfolio.public.asset_interval",
    "portfolio.public.portfolio_clock",
    "portfolio.transaction",
)
Record = tuple[str, dict[str, Any]]


@dataclass(frozen=True)
class CdcStack:
    database: Database
    env: dict[str, str]
    source_port: int
    bootstrap_servers: str
    connect_url: str

    def source(self, user: str = "qe_source_writer") -> psycopg.Connection[Any]:
        return psycopg.connect(
            host="127.0.0.1",
            port=self.source_port,
            dbname="qe_source",
            user=user,
            password=(self.database.secrets_dir / user).read_text("utf-8").strip(),
            autocommit=True,
            options="-c TimeZone=UTC",
        )

    def compose(self, *args: str) -> str:
        from .conftest import _compose

        return _compose(self.env, *args)

    def wait_running(self, timeout: float = 60) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        status: dict[str, Any] = {}
        while time.monotonic() < deadline:
            try:
                response = httpx.get(f"{self.connect_url}/connectors/portfolio/status", timeout=2)
                response.raise_for_status()
                status = response.json()
                if status.get("tasks") and all(t["state"] == "RUNNING" for t in status["tasks"]):
                    with self.source("qe_source_owner") as conn:
                        row = conn.execute(
                            "SELECT active FROM pg_replication_slots WHERE slot_name='qe_portfolio'"
                        ).fetchone()
                    if row == (True,):
                        return status
            except httpx.HTTPError:
                pass
            Event().wait(0.1)
        raise AssertionError(f"Debezium não iniciou: {status}")

    def capture(
        self, action: Callable[[], Any], *, transactions: int = 1, timeout: float = 30
    ) -> list[Record]:
        """Obtém apenas transações da ação, mesmo com WAL anterior ainda não publicado.

        Os offsets Kafka não são uma barreira da origem: o Connect pode estar atrasado.
        Dois XIDs reservados na origem delimitam os commits produzidos pela ação. As
        chamadas são sequenciais neste fixture; os XIDs fora da janela são descartados.
        """
        consumer = Consumer(
            {
                "bootstrap.servers": self.bootstrap_servers,
                "group.id": f"qe-capture-{uuid.uuid4().hex}",
                "enable.auto.commit": False,
                "enable.auto.offset.store": False,
                "auto.offset.reset": "error",
            }
        )
        try:
            partitions = []
            for topic in TOPICS:
                _, high = consumer.get_watermark_offsets(TopicPartition(topic, 0), timeout=10)
                partitions.append(TopicPartition(topic, 0, high))
            consumer.assign(partitions)
            lower_xid = self._xid_boundary()
            action()
            upper_xid = self._xid_boundary()
            span = upper_xid - lower_xid
            assert 0 < span < 2**32, "janela de captura excedeu uma volta do XID"
            records: list[Record] = []
            ends: dict[str, int] = {}
            rows: Counter[str] = Counter()
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                message = consumer.poll(0.2)
                if message is None:
                    continue
                if message.error():
                    raise AssertionError(str(message.error()))
                value, received_topic = message.value(), message.topic()
                assert value is not None and received_topic is not None
                payload = json.loads(value)
                topic = received_topic
                transaction_id = (
                    payload["id"]
                    if topic == "portfolio.transaction"
                    else payload["transaction"]["id"]
                )
                xid = int(transaction_id.split(":", 1)[0])
                # pg_current_xact_id retorna xid8; o protocolo lógico usa os 32 bits
                # baixos. A distância modular mantém a barreira válida no wrap.
                if not 0 < (xid - lower_xid) % (2**32) < span:
                    continue
                records.append((topic, payload))
                if topic == "portfolio.transaction":
                    if payload["status"] == "END":
                        ends[payload["id"].split(":", 1)[0]] = int(payload["event_count"])
                else:
                    rows[payload["transaction"]["id"].split(":", 1)[0]] += 1
                if len(ends) == transactions and all(
                    rows[tx] == count for tx, count in ends.items()
                ):
                    return records
            raise AssertionError(
                f"CDC incompleto para XIDs ({lower_xid}, {upper_xid}): "
                f"END={ends}, linhas={dict(rows)}"
            )
        finally:
            consumer.close()

    def _xid_boundary(self) -> int:
        with self.source("qe_source_owner") as source:
            row = source.execute("SELECT pg_current_xact_id()::text").fetchone()
            assert row is not None
            return int(row[0])


@pytest.fixture(scope="session")
def cdc_stack(database: Database) -> CdcStack:
    from .conftest import _compose

    if database.external:
        pytest.fail("teste CDC exige projeto Compose; não pertence ao job PostGIS isolado")
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        kafka_port = listener.getsockname()[1]
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        connect_host_port = listener.getsockname()[1]
    env = {
        **os.environ,
        "QE_SECRETS_DIR": str(database.secrets_dir),
        "QE_TEST_KAFKA_PORT": str(kafka_port),
        "QE_TEST_CONNECT_PORT": str(connect_host_port),
    }
    _compose(env, "up", "-d", "--wait", "source-db", "kafka", "connect")
    source_port = int(_compose(env, "port", "source-db", "5432").strip().rsplit(":", 1)[1])
    connect_port = int(_compose(env, "port", "connect", "8083").strip().rsplit(":", 1)[1])
    stack = CdcStack(
        database, env, source_port, f"127.0.0.1:{kafka_port}", f"http://127.0.0.1:{connect_port}"
    )
    with stack.source("qe_source_owner") as conn:
        migrate_source(conn)
    config = json.loads((ROOT / "docker" / "portfolio-connector.json").read_text("utf-8"))
    response = httpx.put(
        f"{stack.connect_url}/connectors/portfolio/config", json=config, timeout=15
    )
    response.raise_for_status()
    stack.wait_running()
    return stack
