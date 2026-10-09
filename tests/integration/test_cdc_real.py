"""Gates M4: mensagens obtidas de PostgreSQL → Debezium → Kafka reais."""

from datetime import timedelta
from uuid import uuid4

from builders import feature

from quakeexposure.cdc import apply_pending, stage
from quakeexposure.domain.portfolio import assets_as_of
from quakeexposure.ingestion.store import Conn
from quakeexposure.portfolio.source import execute_command
from quakeexposure.portfolio.store import versions_known

from .cdc_support import CdcStack, Record
from .conftest import Harness
from .test_cdc import ASSET, END, NOW, asset_row
from .test_exposure import current, feed, publish


def apply_batch(conn: Conn, records: list[Record]) -> int:
    for topic, payload in records:
        stage(conn, topic, payload, now=NOW)
    return apply_pending(conn, now=NOW)


def test_f01_real_debezium_redelivery_preserves_epoch_facts_and_publication(
    cdc_stack: CdcStack, harness: Harness
) -> None:
    h = harness
    h.clock.now = NOW
    tenant = f"f01-{uuid4().hex}"
    with cdc_stack.source() as source:
        records = cdc_stack.capture(lambda: execute_command(source, tenant, [asset_row()]))
    assert apply_batch(h.conn, records) == 1
    feed(h, feature("us-cdc-f01", updated=1_000, lon=0, lat=0))
    assert publish(h)["failed"] == 0
    first = current(h)
    assert len(first) == 1
    counts = (h.count("portfolio.asset_revision"), h.count("exposure.fact"))
    assert apply_batch(h.conn, records) == 0
    assert publish(h)["published"] == 0
    assert current(h) == first
    assert (h.count("portfolio.asset_revision"), h.count("exposure.fact")) == counts == (1, 1)
    assert h.one("SELECT portfolio_epoch FROM portfolio.tenant") == 1


def test_f09_real_debezium_end_before_data_and_one_held_row(
    cdc_stack: CdcStack, conn: Conn
) -> None:
    tenant = f"f09-{uuid4().hex}"
    with cdc_stack.source() as source:
        records = cdc_stack.capture(
            lambda: execute_command(
                source,
                tenant,
                [
                    asset_row(),
                    asset_row(asset_id="b", asset_interval_id="b#1"),
                ],
            )
        )
    ends = [record for record in records if record[0] == END and record[1]["status"] == "END"]
    data = [record for record in records if record[0] != END]
    withheld = next(record for record in data if record[0] == ASSET)
    assert apply_batch(conn, ends) == 0
    assert apply_batch(conn, [record for record in data if record is not withheld]) == 0
    assert conn.execute("SELECT count(*) FROM portfolio.tenant").fetchone() == (0,)
    assert conn.execute("SELECT count(*) FROM portfolio.asset_revision").fetchone() == (0,)
    assert apply_batch(conn, [withheld]) == 1
    assert conn.execute("SELECT portfolio_epoch FROM portfolio.tenant").fetchone() == (1,)
    assert conn.execute("SELECT count(*) FROM portfolio.asset_revision").fetchone() == (2,)


def test_f13_real_debezium_move_and_retroactive_correction_use_event_time_and_epoch(
    cdc_stack: CdcStack, harness: Harness
) -> None:
    h = harness
    h.clock.now = NOW
    tenant = f"f13-{uuid4().hex}"
    event_at = NOW - timedelta(days=2)
    moved_at = NOW - timedelta(days=1)
    original = asset_row()
    closed = {**original, "valid_to": moved_at.isoformat()}
    moved = {**original, "asset_interval_id": "a#2", "valid_from": moved_at.isoformat(), "lon": 20}
    with cdc_stack.source() as source:
        initial = cdc_stack.capture(lambda: execute_command(source, tenant, [original]))
        movement = cdc_stack.capture(
            lambda: execute_command(source, tenant, [closed, moved], retroactive=True)
        )
        corrected = cdc_stack.capture(
            lambda: execute_command(source, tenant, [{**closed, "lon": 10}], retroactive=True)
        )
    assert apply_batch(h.conn, initial) == 1
    feed(
        h, feature("us-cdc-f13", updated=1_000, lon=0, lat=0, time=int(event_at.timestamp() * 1000))
    )
    assert publish(h)["failed"] == 0
    assert h.one("SELECT qualifying_count FROM exposure.calculation") == 1
    assert apply_batch(h.conn, movement) == 1
    assert publish(h)["failed"] == 0
    assert (
        h.one("""SELECT c.qualifying_count FROM exposure.calculation c
                    JOIN serving.current_publication p USING(calculation_id)""")
        == 1
    )
    assert apply_batch(h.conn, corrected) == 1
    # CDC dirties the job before a worker gets a chance to mark or calculate.
    assert (
        h.one("""SELECT desired_generation > published_generation
                    FROM control.exposure_job""")
        is True
    )
    assert publish(h)["failed"] == 0
    assert (
        h.one("""SELECT c.qualifying_count FROM exposure.calculation c
                    JOIN serving.current_publication p USING(calculation_id)""")
        == 0
    )
    assert (
        h.one("""SELECT f.lon FROM exposure.fact f
                    JOIN serving.current_publication p USING(calculation_id)""")
        == 10
    )
    versions = versions_known(h.conn, tenant, 3)
    for epoch, expected_lon in ((1, 0), (2, 0), (3, 10)):
        at_event = assets_as_of(versions, tenant_id=tenant, at=event_at, epoch=epoch)
        assert [(a.asset_id, a.lon) for a in at_event] == [("a", expected_lon)]
    today = assets_as_of(versions, tenant_id=tenant, at=NOW, epoch=3)
    assert [(a.asset_id, a.lon) for a in today] == [("a", 20)]
