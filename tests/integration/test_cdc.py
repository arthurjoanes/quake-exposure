"""Stage e apply com PostgreSQL real; casos de borda independentes do conector."""

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import psycopg
import pytest
from builders import feature

from quakeexposure.cdc import CdcError, apply_pending, stage
from quakeexposure.cdc.consumer import Message, process_message, run_forever
from quakeexposure.exposure import jobs, worker
from quakeexposure.ingestion.store import Conn

from .conftest import Database, Harness
from .test_exposure import feed, publish

NOW = datetime(2026, 10, 9, 12, tzinfo=UTC)
ASSET = "portfolio.public.asset_interval"
CLOCK = "portfolio.public.portfolio_clock"
END = "portfolio.transaction"


def asset_row(**changes: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "tenant_id": "cdc-test",
        "asset_interval_id": "a#1",
        "asset_id": "a",
        "row_version": 1,
        "subject_token": str(uuid4()),
        "valid_from": "2020-01-01T00:00:00Z",
        "valid_to": None,
        "lon": 0.0,
        "lat": 0.0,
        "insured_value_minor": 2**53 + 1,
        "currency": "BRL",
        "is_deleted": False,
        "updated_at": NOW.isoformat(),
    }
    return {**row, **changes}


def messages(
    tx: str, epoch: int, rows: list[dict[str, Any]], source_epoch: int = 1
) -> list[tuple[str, dict[str, Any]]]:
    events: list[tuple[str, dict[str, Any]]] = [
        (
            ASSET,
            {
                "op": "c" if row["row_version"] == 1 else "u",
                "after": row,
                "before": None,
                "transaction": {"id": tx, "total_order": i + 1},
            },
        )
        for i, row in enumerate(rows)
    ]
    events.append(
        (
            CLOCK,
            {
                "op": "c" if epoch == 1 else "u",
                "after": {
                    "tenant_id": "cdc-test",
                    "epoch": epoch,
                    "source_epoch": source_epoch,
                    "updated_at": NOW.isoformat(),
                    "last_command_id": str(uuid4()),
                },
                "transaction": {"id": tx, "total_order": len(rows) + 1},
            },
        )
    )
    events.append(
        (
            END,
            {
                "status": "END",
                "id": tx,
                "event_count": len(rows) + 1,
                "data_collections": [
                    {"data_collection": "public.asset_interval", "event_count": len(rows)},
                    {"data_collection": "public.portfolio_clock", "event_count": 1},
                ],
            },
        )
    )
    return events


def ingest(conn: Conn, batch: list[tuple[str, dict[str, Any]]]) -> None:
    for topic, payload in batch:
        stage(conn, topic, payload, now=NOW)


def test_end_before_data_waits_for_exact_counts(conn: Conn) -> None:
    batch = messages(
        "out-of-order", 1, [asset_row(), asset_row(asset_id="b", asset_interval_id="b")]
    )
    ingest(conn, [batch[-1], *batch[1:-1]])
    assert apply_pending(conn, now=NOW) == 0
    assert conn.execute("SELECT count(*) FROM portfolio.tenant").fetchone() == (0,)
    ingest(conn, [batch[0]])
    assert apply_pending(conn, now=NOW) == 1
    assert conn.execute("SELECT count(*) FROM portfolio.asset_revision").fetchone() == (2,)
    ingest(conn, batch)
    assert apply_pending(conn, now=NOW) == 0
    assert conn.execute("SELECT portfolio_epoch FROM portfolio.tenant").fetchone() == (1,)
    assert conn.execute("SELECT count(payload) FROM control.cdc_stage").fetchone() == (0,)


def test_clock_epoch_gap_and_source_epoch_never_apply_partial(conn: Conn) -> None:
    row = asset_row()
    ingest(conn, messages("second", 2, [{**row, "row_version": 2, "lon": 20.0}]))
    assert apply_pending(conn, now=NOW) == 0
    ingest(conn, messages("first", 1, [row]))
    assert apply_pending(conn, now=NOW) == 2
    ingest(conn, messages("wrong-source", 3, [{**row, "row_version": 3}], source_epoch=2))
    assert apply_pending(conn, now=NOW) == 0
    assert conn.execute("SELECT portfolio_epoch FROM portfolio.tenant").fetchone() == (2,)
    assert conn.execute("SELECT count(*) FROM portfolio.asset_revision").fetchone() == (2,)
    reason = conn.execute(
        "SELECT waiting_reason FROM control.cdc_transaction WHERE transaction_id='wrong-source'"
    ).fetchone()
    assert reason is not None and "source_epoch" in reason[0]


def test_transaction_without_tenant_clock_is_explicit_error(conn: Conn) -> None:
    batch = messages("missing-clock", 1, [asset_row()])
    end = batch[-1][1]
    end["event_count"] = 1
    end["data_collections"] = [{"data_collection": "public.asset_interval", "event_count": 1}]
    ingest(conn, [batch[0], batch[-1]])
    with pytest.raises(CdcError, match="sem linha de portfolio_clock"):
        apply_pending(conn, now=NOW)
    assert conn.execute("SELECT count(*) FROM portfolio.asset_revision").fetchone() == (0,)


def test_same_total_count_but_wrong_table_counts_are_rejected(conn: Conn) -> None:
    batch = messages("wrong-count", 1, [asset_row()])
    batch[-1][1]["data_collections"] = [
        {"data_collection": "public.asset_interval", "event_count": 2}
    ]
    ingest(conn, batch)
    with pytest.raises(CdcError, match="contagem por tabela"):
        apply_pending(conn, now=NOW)
    assert conn.execute("SELECT count(*) FROM portfolio.tenant").fetchone() == (0,)


def test_failed_apply_rolls_back_revisions_and_epoch(conn: Conn) -> None:
    row = asset_row()
    ingest(conn, messages("bad-interval", 1, [row, {**row, "asset_interval_id": "overlap"}]))
    with pytest.raises(psycopg.errors.ExclusionViolation):
        apply_pending(conn, now=NOW)
    assert conn.execute("SELECT count(*) FROM portfolio.asset_revision").fetchone() == (0,)
    assert conn.execute("SELECT count(*) FROM portfolio.tenant").fetchone() == (0,)
    assert conn.execute("SELECT count(payload) FROM control.cdc_stage").fetchone() == (3,)


def test_old_cdc_cannot_restore_blocked_subject(conn: Conn) -> None:
    row = asset_row()
    conn.execute(
        "INSERT INTO privacy.erase_block(subject_token) VALUES (%s)", (row["subject_token"],)
    )
    ingest(conn, messages("blocked", 1, [row]))
    assert apply_pending(conn, now=NOW) == 1
    assert conn.execute("SELECT count(*) FROM portfolio.asset_revision").fetchone() == (0,)
    assert conn.execute("SELECT count(*) FROM portfolio.asset_interval").fetchone() == (0,)
    assert conn.execute("SELECT portfolio_epoch FROM portfolio.tenant").fetchone() == (1,)


def test_api_cannot_read_subject_token_in_stage(database: Database, conn: Conn) -> None:
    ingest(conn, messages("private", 1, [asset_row()]))
    with database.connect("qe_api_read") as api:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            api.execute("SELECT payload FROM control.cdc_stage")
        assert api.execute("SELECT count(*) FROM control.cdc_transaction").fetchone() == (1,)


class KafkaRecord:
    def __init__(self, topic: str, payload: dict[str, Any]) -> None:
        import json

        self._topic = topic
        self._payload = json.dumps(payload).encode()

    def topic(self) -> str:
        return self._topic

    def value(self) -> bytes:
        return self._payload

    def error(self) -> None:
        return None


class CommitFailure:
    def __init__(self, database: Database) -> None:
        self.database = database
        self.commits = 0

    def subscribe(self, topics: list[str]) -> None:
        pass

    def poll(self, timeout: float) -> Message | None:
        return None

    def close(self) -> None:
        pass

    def commit(self, *, message: Message, asynchronous: bool) -> None:
        assert not asynchronous
        with self.database.connect() as independent:
            assert independent.execute(
                "SELECT portfolio_epoch FROM portfolio.tenant"
            ).fetchone() == (1,)
        self.commits += 1
        if self.commits == 1:
            raise RuntimeError("falha Kafka após commit plataforma")


def test_offset_failure_replays_after_durable_apply(database: Database, conn: Conn) -> None:
    batch = messages("offset-failure", 1, [asset_row()])
    ingest(conn, batch[:-1])
    record = KafkaRecord(*batch[-1])
    consumer = CommitFailure(database)
    with pytest.raises(RuntimeError, match="falha Kafka"):
        process_message(conn, consumer, record, now=NOW)
    assert process_message(conn, consumer, record, now=NOW) == 0
    assert consumer.commits == 2
    assert conn.execute("SELECT count(*) FROM portfolio.asset_revision").fetchone() == (1,)


def test_portfolio_epoch_dirties_only_events_in_scope(harness: Harness) -> None:
    h = harness
    h.clock.now = NOW
    row = asset_row()
    ingest(h.conn, messages("initial", 1, [row]))
    apply_pending(h.conn, now=h.clock())
    feed(
        h,
        feature(
            "us-old",
            updated=1_000,
            lon=0,
            lat=0,
            time=int((NOW - timedelta(days=1)).timestamp() * 1000),
        ),
    )
    publish(h)
    h.clock.advance(days=8)
    feed(h, feature("us-new", updated=1_000, lon=0, lat=0, time=int(h.clock().timestamp() * 1000)))
    publish(h)
    before = dict(
        h.conn.execute(
            """SELECT e.preferred_id, j.desired_generation FROM control.exposure_job j
           JOIN quake.earthquake e USING (earthquake_uid, stream_scope)"""
        ).fetchall()
    )
    ingest(h.conn, messages("changed", 2, [{**row, "row_version": 2, "lon": 20}]))
    assert apply_pending(h.conn, now=h.clock()) == 1
    after = dict(
        h.conn.execute(
            """SELECT e.preferred_id, j.desired_generation FROM control.exposure_job j
           JOIN quake.earthquake e USING (earthquake_uid, stream_scope)"""
        ).fetchall()
    )
    assert after["us-old"] == before["us-old"]
    assert after["us-new"] == before["us-new"] + 1


def test_f12_cdc_commit_invalidates_calculation_in_progress(
    database: Database, harness: Harness
) -> None:
    h = harness
    h.clock.now = NOW
    row = asset_row()
    ingest(h.conn, messages("initial", 1, [row]))
    apply_pending(h.conn, now=h.clock())
    feed(h, feature("us-cas", updated=1_000, lon=0, lat=0))
    jobs.mark(h.conn, h.clock())
    job = worker.claim(h.conn, h.clock())
    assert job is not None

    def changed() -> None:
        with database.connect() as other:
            ingest(other, messages("changed", 2, [{**row, "row_version": 2, "lon": 20}]))
            assert apply_pending(other, now=h.clock()) == 1

    assert worker.run(h.conn, h.blobs, job, h.clock, before_commit=changed) == "stale"
    assert h.count("exposure.fact") == h.count("serving.current_publication") == 0
    assert h.one("SELECT portfolio_epoch FROM portfolio.tenant") == 2
    assert publish(h)["failed"] == 0
    assert h.one("SELECT qualifying_count FROM exposure.calculation") == 0


def test_consumer_batches_offsets_only_after_each_topic_is_durable(
    database: Database, conn: Conn
) -> None:
    batch = messages(
        "batch", 1, [asset_row(asset_id=f"a-{i}", asset_interval_id=f"i-{i}") for i in range(20)]
    )
    records = [KafkaRecord(topic, payload) for topic, payload in batch]

    class BatchConsumer:
        def __init__(self) -> None:
            self.polled = 0
            self.committed: list[Message] = []
            self.closed = False

        def subscribe(self, topics: list[str]) -> None:
            assert set(topics) == {ASSET, CLOCK, END}

        def poll(self, timeout: float) -> Message | None:
            record = records[self.polled]
            self.polled += 1
            return record

        def commit(self, *, message: Message, asynchronous: bool) -> None:
            assert not asynchronous
            with database.connect() as independent:
                assert independent.execute(
                    "SELECT count(*) FROM portfolio.asset_revision"
                ).fetchone() == (20,)
                assert independent.execute(
                    "SELECT portfolio_epoch FROM portfolio.tenant"
                ).fetchone() == (1,)
            self.committed.append(message)

        def close(self) -> None:
            self.closed = True

    consumer = BatchConsumer()
    run_forever(
        conn,
        consumer=consumer,
        stopped=lambda: consumer.polled == len(records),
        clock=lambda: NOW,
        monitor=lambda _: None,
        monotonic=lambda: 0,
        batch_size=100,
    )
    assert consumer.closed
    assert consumer.committed == [records[19], records[20], records[21]]
