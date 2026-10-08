"""Regras do coletor que só aparecem com a fonte fora do comum: cache, falha longa, banco."""

import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest
from builders import DETAIL_URL, collection, feature, grid_xml

from quakeexposure.ingestion import projection
from quakeexposure.ingestion.collector import FEED_URL, MAX_REQUEST_ATTEMPTS
from quakeexposure.ingestion.http import sha256_b64
from quakeexposure.status import report

from .conftest import Database, Harness, ok

GRID = grid_xml([10.0, 10.5, 11.0], [1.0, 0.0])


def incremental(request: httpx.Request) -> bool:
    return request.url.path == "/fdsnws/event/1/query" and "eventid" not in request.url.params


def product(event_id: str, update: int, *, grid: bool = False, **extra: Any) -> dict[str, Any]:
    contents: dict[str, Any] = {}
    if grid:
        contents["download/grid.xml"] = {
            "url": f"https://earthquake.usgs.gov/product/shakemap/{event_id}/grid.xml",
            "length": len(GRID),
            "sha256": sha256_b64(GRID),
        }
    p = {
        "id": f"urn:usgs-product:{event_id[:2]}:shakemap:{event_id}:{update}",
        "type": "shakemap",
        "code": event_id,
        "source": event_id[:2],
        "updateTime": update,
        "status": "UPDATE",
        "preferredWeight": 100,
        "contents": contents,
    }
    p.update(extra)
    return p


def summary(event_id: str, **kwargs: Any) -> dict[str, Any]:
    f = feature(event_id, **kwargs)
    f["properties"]["detail"] = DETAIL_URL.format(event_id)
    return f


def detail(event_id: str, products: list[dict[str, Any]], **kwargs: Any) -> dict[str, Any]:
    f = feature(event_id, **kwargs)
    f["properties"]["products"] = {"shakemap": products}
    return f


def map_of(h: Harness, event_id: str) -> Any:
    return h.one(
        """SELECT m.product_revision_id FROM quake.event_map m
           JOIN quake.alias a USING (stream_scope, earthquake_uid) WHERE a.provider_id = %s""",
        event_id,
    )


def test_detail_older_than_the_revision_is_fetched_again(harness: Harness) -> None:
    h = harness
    h.usgs.on(incremental, ok(collection()))
    h.usgs.on(FEED_URL, ok(collection(summary("us1", updated=1_000, types=",origin,"))))
    h.collector.tick()
    h.usgs.on(FEED_URL, ok(collection(summary("us1", updated=2_000))))  # ganha ShakeMap
    cached = detail("us1", [], updated=1_000, types=",origin,")
    fresh = detail("us1", [product("us1", 1_900)], updated=2_000)
    h.usgs.on(DETAIL_URL.format("us1"), ok(cached), ok(fresh))
    h.clock.advance(seconds=60)
    h.collector.tick()
    assert (
        h.one("SELECT last_error FROM control.fetch_request") == "detail mais velho que a revisão"
    )
    h.clock.advance(seconds=60)
    h.collector.tick()
    assert map_of(h, "us1") == "urn:usgs-product:us:shakemap:us1:1900"
    assert h.count("control.fetch_request") == 0


def test_older_detail_does_not_replace_the_map_or_take_the_product(harness: Harness) -> None:
    h = harness
    h.usgs.on(incremental, ok(collection()))
    shared = product("ak1", 900)
    h.usgs.on(FEED_URL, ok(collection(summary("us1", updated=2_000))))
    h.usgs.on(DETAIL_URL.format("us1"), ok(detail("us1", [shared], updated=2_000)))
    h.collector.tick()
    owner = h.one("SELECT earthquake_uid FROM quake.shakemap_product")
    # detail mais velho de outro evento lista o mesmo produto; depois, o us1 volta com um velho
    h.usgs.on(FEED_URL, ok(collection(summary("nc1", updated=3_000))))
    h.usgs.on(DETAIL_URL.format("nc1"), ok(detail("nc1", [shared], updated=1_000)))
    h.clock.advance(seconds=60)
    h.collector.tick()
    assert h.one("SELECT earthquake_uid FROM quake.shakemap_product") == owner
    h.conn.execute(
        """INSERT INTO control.fetch_request (provider_event_id, url, reason, requested_at)
           VALUES ('us1', %s, 'watch', now())""",
        (DETAIL_URL.format("us1"),),
    )
    h.usgs.on(DETAIL_URL.format("us1"), ok(detail("us1", [product("us1", 500)], updated=1_500)))
    h.clock.advance(seconds=60)
    h.collector.tick()
    assert map_of(h, "us1") == "urn:usgs-product:ak:shakemap:ak1:900"


def test_given_up_request_comes_back_with_a_new_revision(harness: Harness) -> None:
    h = harness
    h.usgs.on(incremental, ok(collection()))
    h.usgs.on(FEED_URL, ok(collection(summary("us1", updated=1_000, types=""))))
    h.collector.tick()
    h.conn.execute(
        """INSERT INTO control.fetch_request (provider_event_id, url, reason, requested_at,
               attempts) VALUES ('us1', %s, 'updated', now(), %s)""",
        (DETAIL_URL.format("us1"), MAX_REQUEST_ATTEMPTS),
    )
    h.usgs.on(FEED_URL, ok(collection(summary("us1", updated=2_000))))
    h.usgs.on(DETAIL_URL.format("us1"), ok(detail("us1", [product("us1", 1_900)], updated=2_000)))
    h.clock.advance(seconds=60)
    h.collector.tick()
    assert map_of(h, "us1") == "urn:usgs-product:us:shakemap:us1:1900"


def test_same_invalid_product_twice_is_one_quarantine_row(harness: Harness) -> None:
    h = harness
    h.usgs.on(incremental, ok(collection()))
    bad = product("us1", 1_000)
    del bad["preferredWeight"]
    h.usgs.on(DETAIL_URL.format("us1"), ok(detail("us1", [bad], updated=1_000)))
    for minute in range(2):
        h.usgs.on(FEED_URL, ok(collection(summary("us1", updated=1_000 + minute))))
        h.clock.advance(seconds=60)
        h.collector.tick()
    assert h.count("control.capture", "kind = 'detail'") == 2
    assert h.count("control.quarantine", "reason LIKE 'contract: %%'") == 1


def test_failing_grids_do_not_starve_the_others(harness: Harness) -> None:
    h = harness
    h.usgs.on(incremental, ok(collection()))
    events = {"sa": 4, "sb": 3, "sc": 2, "old": 1}  # dias antes do relógio
    day = 86_400_000
    now_ms = int(h.clock().timestamp() * 1000)
    times = {e: now_ms - d * day for e, d in events.items()}
    summaries = [summary(e, updated=1_000 + n, time=times[e]) for n, e in enumerate(events)]
    h.usgs.on(FEED_URL, ok(collection(*summaries)))
    for n, e in enumerate(events):
        found = detail(e, [product(e, 900, grid=True)], updated=1_000 + n, time=times[e])
        h.usgs.on(DETAIL_URL.format(e), ok(found))
        grid_url = f"https://earthquake.usgs.gov/product/shakemap/{e}/grid.xml"
        h.usgs.on(grid_url, ok(GRID) if e == "old" else (503, b"", {}))
    for _ in range(8):
        h.collector.tick()
        h.clock.advance(seconds=60)
    assert h.one("SELECT status FROM quake.shakemap_grid") == "OK"
    failing = [u for u in h.usgs.urls() if "grid.xml" in u and "/old/" not in u]
    assert len(failing) == 3 * MAX_REQUEST_ATTEMPTS * 4  # 4 GETs (com retry) por tentativa
    assert report(h.conn, h.clock())["grids_failed"] == 3


def test_lone_surrogate_in_a_feature_is_quarantined(harness: Harness) -> None:
    h = harness
    h.usgs.on(incremental, ok(collection()))
    bad = feature("us9", updated=1_000, mag="x")  # type: ignore[arg-type]
    bad["properties"]["place"] = "\udc80"
    body = json.dumps(collection(bad, feature("us1", updated=1_000, types=""))).encode()
    h.usgs.on(FEED_URL, (200, body, {}))
    h.collector.tick()
    assert h.count("quake.event_revision") == 1
    assert h.count("control.quarantine") == 1
    assert h.one("SELECT last_error FROM control.source_status WHERE source = 'feed'") is None


@pytest.fixture
def failing_us2(database: Database) -> Any:
    """O banco recusa a revisão do us2 com disco cheio (53100) até a fixture terminar."""
    with database.connect("qe_owner") as owner:
        owner.execute(
            """CREATE FUNCTION quake.test_disk_full() RETURNS trigger LANGUAGE plpgsql AS $$
               BEGIN
                 IF NEW.provider_event_id = 'us2' THEN
                   RAISE EXCEPTION 'could not extend file' USING ERRCODE = '53100';
                 END IF;
                 RETURN NEW;
               END $$"""
        )
        owner.execute(
            """CREATE TRIGGER test_disk_full BEFORE INSERT ON quake.event_revision
               FOR EACH ROW EXECUTE FUNCTION quake.test_disk_full()"""
        )
        try:
            yield lambda: owner.execute("DROP TRIGGER test_disk_full ON quake.event_revision")
        finally:
            owner.execute("DROP TRIGGER IF EXISTS test_disk_full ON quake.event_revision")
            owner.execute("DROP FUNCTION quake.test_disk_full()")


def test_disk_full_leaves_the_capture_pending(
    harness: Harness, failing_us2: Callable[[], None]
) -> None:
    h = harness
    h.usgs.on(incremental, ok(collection()))
    h.usgs.on(FEED_URL, ok(collection(feature("us1", updated=1_000, types=""),
                                      feature("us2", updated=1_000, types=""))))  # fmt: skip
    h.collector.tick()
    assert h.count("control.capture", "projected_at IS NULL") == 1
    assert h.count("control.quarantine") == 0
    assert h.one("SELECT last_ok_at FROM control.source_status WHERE source = 'feed'") is None
    failing_us2()  # espaço liberado
    h.clock.advance(seconds=60)
    h.collector.tick()
    assert h.count("quake.event_revision") == 2 and h.count("control.quarantine") == 0


def test_row_lock_inside_a_feature_leaves_the_capture_pending(
    harness: Harness, database: Database
) -> None:
    h = harness
    h.usgs.on(incremental, ok(collection()))
    h.usgs.on(FEED_URL, ok(collection(feature("us1", updated=1_000, types=""))))
    h.collector.tick()
    h.usgs.on(FEED_URL, ok(collection(feature("us1", updated=2_000, types=""))))
    h.conn.execute("SET statement_timeout = '1s'")
    with database.connect() as other, other.transaction():
        other.execute("SELECT 1 FROM quake.earthquake FOR UPDATE")
        h.clock.advance(seconds=60)
        h.collector.tick()
    assert h.count("control.capture", "projected_at IS NULL") == 1
    assert h.count("control.quarantine") == 0
    h.clock.advance(seconds=60)
    h.collector.tick()
    assert h.count("quake.event_revision") == 2


def test_capture_that_was_not_applied_is_not_a_success(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    h = harness
    h.usgs.on(FEED_URL, ok(collection()))
    h.usgs.on(incremental, ok(collection(feature("us1", updated=1_000, types=""))))

    def broken(*args: Any) -> Any:
        raise RuntimeError("bug")

    monkeypatch.setattr(projection, "_project", broken)
    h.collector.tick()
    row = h.conn.execute(
        "SELECT watermark, last_ok_at, last_error FROM control.source_status WHERE source = %s",
        ("incremental",),
    ).fetchone()
    assert row is not None and row[0] is None and row[1] is None and "falhou" in row[2]
