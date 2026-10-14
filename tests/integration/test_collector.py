import copy
import gzip
import json
from collections.abc import Callable
from datetime import date, datetime, timedelta
from typing import Any

import httpx
import pytest
from builders import collection, feature, summary_of

from quakeexposure.ingestion.collector import FEED_URL, fdsn_time
from quakeexposure.ingestion.http import FetchError, sha256_b64
from quakeexposure.ingestion.projection import FDSN_QUERY_URL, project_pending
from quakeexposure.ingestion.replay import capture_fault
from quakeexposure.status import format_report, report

from .conftest import Harness, ok

TIUR_GRID = (
    "https://earthquake.usgs.gov/product/shakemap/us7000tiur/us/1790041565495/download/grid.xml"
)
TIUR_PRODUCT = "urn:usgs-product:us:shakemap:us7000tiur:1790041565495"


def incremental(request: httpx.Request) -> bool:
    return request.url.path == "/fdsnws/event/1/query" and "eventid" not in request.url.params


def eventid(event_id: str) -> Callable[[httpx.Request], bool]:
    return lambda r: r.url.params.get("eventid") == event_id


def quiet(h: Harness) -> None:
    """Feed e incremental vazios, para o tick só fazer o resto."""
    h.usgs.on(FEED_URL, ok(collection()))
    h.usgs.on(incremental, ok(collection()))


def tiur_feature(usgs_json: Callable[[str], Any]) -> dict[str, Any]:
    fdsn = usgs_json("fdsn_shakemap_minmmi6")
    return dict(next(f for f in fdsn["features"] if f["id"] == "us7000tiur"))


def test_f01_same_feed_twice_adds_nothing(
    harness: Harness, usgs_fixture: Callable[[str], bytes]
) -> None:
    h = harness
    h.usgs.on(FEED_URL, ok(usgs_fixture("feed_all_day")))
    h.collector.poll_feed(h.clock())
    revisions = h.count("quake.event_revision")
    assert revisions == 240
    assert h.count("control.capture") == 1
    assert h.count("quake.earthquake", "merged_into IS NULL") == 240

    h.clock.advance(seconds=60)
    h.collector.poll_feed(h.clock())
    assert h.count("quake.event_revision") == revisions
    assert h.count("control.capture") == 1  # sem revisão nova, sem blob: só a linha de log
    assert h.one("SELECT last_new_revisions FROM control.source_status WHERE source = 'feed'") == 0


def test_projection_quarantines_blob_with_wrong_capture_hash(harness: Harness) -> None:
    h = harness
    capture_id = capture_fault(
        h.conn,
        h.blobs,
        parent_sha256="0" * 64,
        kind="feed",
        url="fault://integrity",
        fetched_at=h.clock(),
        body=json.dumps(collection(feature("us-original", updated=1000))).encode(),
    )
    rel = h.one("SELECT blob_path FROM control.capture WHERE capture_id = %s", capture_id)
    suspect = gzip.compress(
        json.dumps(collection(feature("us-replaced", updated=1000))).encode(), mtime=0
    )
    (h.blobs.root / rel).write_bytes(suspect)
    result = project_pending(h.conn, h.blobs, h.clock())
    assert result.quarantined == 1
    assert h.count("quake.event_revision") == 0
    assert h.one("SELECT projection_status FROM control.capture") == "QUARANTINED"
    assert "BlobIntegrityError" in h.one("SELECT reason FROM control.quarantine")
    assert (h.blobs.root / rel).read_bytes() == suspect


def test_detail_map_and_grid_for_event_in_scope(
    harness: Harness, usgs_json: Callable[[str], Any], usgs_fixture: Callable[[str], bytes]
) -> None:
    h = harness
    quiet(h)
    h.usgs.on(incremental, ok(collection(tiur_feature(usgs_json))))
    h.usgs.on(eventid("us7000tiur"), ok(usgs_fixture("detail_us7000tiur")))
    h.usgs.on(TIUR_GRID, ok(usgs_fixture("grid_us7000tiur"), **{"content-encoding": "gzip"}))

    h.collector.tick()  # evento de 2026-09-21; relógio em 2026-09-22: dentro dos 7 dias

    assert h.one("SELECT product_revision_id FROM quake.event_map") == TIUR_PRODUCT
    assert h.one("SELECT status FROM quake.shakemap_grid") == "OK"
    assert h.one("SELECT nlon * nlat FROM quake.shakemap_grid") == 201 * 202
    assert h.count("control.fetch_request") == 0
    assert h.count("control.capture", "kind = 'grid'") == 1
    assert h.count("control.quarantine") == 0


def test_event_out_of_scope_gets_no_grid_unless_watched(
    harness: Harness, usgs_json: Callable[[str], Any], usgs_fixture: Callable[[str], bytes]
) -> None:
    h = harness
    h.clock.advance(days=30)
    quiet(h)
    h.usgs.on(incremental, ok(collection(tiur_feature(usgs_json))))
    h.usgs.on(eventid("us7000tiur"), ok(usgs_fixture("detail_us7000tiur")))
    h.usgs.on(TIUR_GRID, ok(usgs_fixture("grid_us7000tiur")))
    h.collector.tick()
    assert h.one("SELECT product_revision_id FROM quake.event_map") == TIUR_PRODUCT
    assert h.count("quake.shakemap_grid") == 0

    h.conn.execute("INSERT INTO control.watchlist (provider_event_id) VALUES ('us7000tiur')")
    h.collector.tick()
    assert h.one("SELECT status FROM quake.shakemap_grid") == "OK"


def test_f05_preferred_map_is_not_the_newest(
    harness: Harness, usgs_json: Callable[[str], Any], usgs_fixture: Callable[[str], bytes]
) -> None:
    h = harness
    quiet(h)
    detail = usgs_json("detail_aka2026tuxgky")
    h.usgs.on(FEED_URL, ok(collection(summary_of(detail))))
    h.usgs.on("https://earthquake.usgs.gov/earthquakes/feed/v1.0/detail/aka2026tuxgky",
              ok(usgs_fixture("detail_aka2026tuxgky")))  # fmt: skip
    h.clock.advance(days=30)  # fora do escopo: o teste não depende do grid ak, fora do Git
    h.collector.tick()
    assert h.one("SELECT product_revision_id FROM quake.event_map") == (
        "urn:usgs-product:ak:shakemap:a2026tuxgky:1791312073169"
    )
    assert h.count("quake.shakemap_product") == 2


def test_map_that_disappears_becomes_no_map(
    harness: Harness, usgs_json: Callable[[str], Any]
) -> None:
    h = harness
    quiet(h)
    h.clock.advance(days=30)
    detail = usgs_json("detail_us7000tiur")
    h.usgs.on(incremental, ok(collection(tiur_feature(usgs_json))))
    h.usgs.on(eventid("us7000tiur"), ok(detail))
    h.collector.tick()
    assert h.one("SELECT product_revision_id FROM quake.event_map") == TIUR_PRODUCT

    gone = copy.deepcopy(detail)
    gone["properties"]["updated"] += 1000
    gone["properties"]["types"] = ",origin,phase-data,"
    del gone["properties"]["products"]["shakemap"]
    newer = tiur_feature(usgs_json)
    newer["properties"] = {**newer["properties"], "updated": gone["properties"]["updated"]}
    newer["properties"]["types"] = gone["properties"]["types"]
    h.usgs.on(incremental, ok(collection(newer)))
    h.usgs.on(eventid("us7000tiur"), ok(gone))
    h.clock.advance(minutes=11)
    h.collector.tick()
    assert h.count("quake.event_map", "product_revision_id IS NULL") == 1


def test_f03_deleted_event_in_incremental_is_retracted(
    harness: Harness, usgs_json: Callable[[str], Any]
) -> None:
    h = harness
    quiet(h)
    event = summary_of(usgs_json("detail_us6000u0hy"), types=",origin,")
    h.usgs.on(FEED_URL, ok(collection(event)))
    h.collector.tick()
    assert h.one("SELECT lifecycle FROM quake.earthquake") == "ACTIVE"

    deleted = copy.deepcopy(event)
    deleted["properties"].update(status="deleted", type="", mag=None)
    deleted["properties"]["updated"] += 60_000
    h.usgs.on(incremental, ok(collection(deleted)))
    h.clock.advance(minutes=11)
    h.collector.tick()
    assert h.one("SELECT lifecycle FROM quake.earthquake") == "RETRACTED"
    assert h.count("quake.event_revision") == 2


def test_f03_detail_409_is_reconsulted_with_includedeleted(
    harness: Harness, usgs_json: Callable[[str], Any]
) -> None:
    h = harness
    quiet(h)
    detail = usgs_json("detail_us6000u0hy")
    h.usgs.on(FEED_URL, ok(collection(summary_of(detail))))
    h.usgs.on("https://earthquake.usgs.gov/earthquakes/feed/v1.0/detail/", (409, b"deleted", {}))
    deleted = {k: v for k, v in detail.items()}
    deleted["properties"] = {
        **{k: v for k, v in detail["properties"].items() if k != "products"},
        "status": "deleted",
        "updated": detail["properties"]["updated"] + 60_000,
    }
    h.usgs.on(lambda r: r.url.params.get("includedeleted") == "true" and "eventid" in r.url.params,
              ok(deleted))  # fmt: skip

    h.collector.tick()  # feed -> pedido de detail -> 409 -> pedido de reconsulta
    assert h.one("SELECT reason FROM control.fetch_request") == "deleted"
    h.clock.advance(seconds=60)
    h.collector.tick()
    assert h.one("SELECT lifecycle FROM quake.earthquake") == "RETRACTED"
    assert h.count("control.fetch_request") == 0
    assert any("includedeleted=true" in u and "eventid=us6000u0hy" in u for u in h.usgs.urls())


def test_f04_two_events_that_start_sharing_ids_become_one(harness: Harness) -> None:
    h = harness
    quiet(h)
    a = feature("ak1", updated=1_000, types=",origin,")
    b = feature("us1", updated=2_000, types=",origin,")
    h.usgs.on(FEED_URL, ok(collection(a, b)))
    h.collector.tick()
    assert h.count("quake.earthquake", "merged_into IS NULL") == 2

    merged = feature("us1", updated=3_000, ids=["ak1"], types=",origin,")
    h.usgs.on(FEED_URL, ok(collection(merged)))
    h.clock.advance(seconds=60)
    h.collector.tick()
    assert h.count("quake.earthquake", "merged_into IS NULL") == 1
    assert h.count("quake.earthquake", "merged_into IS NOT NULL") == 1
    # fica o uid que já tinha o id preferido da revisão (us1), com os dois ids
    assert h.one("SELECT preferred_id FROM quake.earthquake WHERE merged_into IS NULL") == "us1"
    alias = h.conn.execute(
        """SELECT a.provider_id FROM quake.alias a JOIN quake.earthquake e
           USING (stream_scope, earthquake_uid) WHERE e.merged_into IS NULL ORDER BY 1"""
    ).fetchall()
    assert [a for (a,) in alias] == ["ak1", "us1"]
    assert h.one("SELECT kind FROM quake.event_alias_revision") == "MERGE"


def test_f06_same_version_with_other_content_goes_to_quarantine(harness: Harness) -> None:
    h = harness
    quiet(h)
    h.usgs.on(FEED_URL, ok(collection(feature("us1", updated=1_000, mag=5.0, types=""))))
    h.collector.tick()
    h.usgs.on(FEED_URL, ok(collection(feature("us1", updated=1_000, mag=6.1, types=""))))
    h.clock.advance(seconds=60)
    h.collector.tick()
    assert h.count("quake.event_revision") == 1
    assert h.one("SELECT reason FROM control.quarantine") == "CONFLICT"

    # a mesma revisão em conflito no feed seguinte não vira captura nem quarentena de novo
    for _ in range(3):
        h.clock.advance(seconds=60)
        h.collector.tick()
    assert h.count("control.capture") == 2
    assert h.count("control.quarantine") == 1


def test_f07_grid_that_does_not_match_the_declared_is_rejected(
    harness: Harness, usgs_json: Callable[[str], Any], usgs_fixture: Callable[[str], bytes]
) -> None:
    h = harness
    quiet(h)
    h.usgs.on(incremental, ok(collection(tiur_feature(usgs_json))))
    h.usgs.on(eventid("us7000tiur"), ok(usgs_fixture("detail_us7000tiur")))
    h.usgs.on(TIUR_GRID, ok(usgs_fixture("grid_us6000u0hy")))  # outro arquivo
    h.collector.tick()
    assert h.one("SELECT status FROM quake.shakemap_grid") == "REJECTED"
    assert h.count("control.capture", "kind = 'grid'") == 0
    assert "não bate" in h.one("SELECT reason FROM control.quarantine")

    h.clock.advance(seconds=60)
    h.collector.tick()
    assert h.usgs.urls().count(TIUR_GRID) == 1  # recusado não é baixado de novo


def test_f08_retry_after_then_stale_source_keeps_events(
    harness: Harness, usgs_fixture: Callable[[str], bytes]
) -> None:
    h = harness
    busy = (503, b"", {"retry-after": "7"})
    h.usgs.on(incremental, ok(collection()))
    h.usgs.on(FEED_URL, busy, busy, ok(usgs_fixture("feed_all_day")))
    h.collector.tick()
    assert h.sleeps == [7.0, 7.0]
    events = h.count("quake.earthquake")
    assert events == 240

    h.usgs.on(FEED_URL, (503, b"", {}))
    for _ in range(6):
        h.clock.advance(seconds=60)
        h.collector.tick()
    r = report(h.conn, h.clock())
    assert r["sources"]["feed"]["stale"] is True
    assert "HTTP 503" in r["sources"]["feed"]["last_error"]
    assert "desatualizado" in format_report(r)
    assert h.count("quake.earthquake") == events  # fonte fora não some com terremoto


def test_f21_detail_url_outside_allowlist(harness: Harness) -> None:
    h = harness
    quiet(h)
    evil = feature("us1", updated=1_000)
    evil["properties"]["detail"] = "https://evil.example.com/us1.geojson"
    h.usgs.on(FEED_URL, ok(collection(evil)))
    h.collector.tick()
    # primeira barreira: o contrato do evento só aceita detail no host do USGS
    assert "contract: properties/detail" in h.one("SELECT reason FROM control.quarantine")
    assert h.count("control.fetch_request") == 0

    # segunda: a allowlist do cliente, para URL que chegue por outro caminho
    h.conn.execute(
        """INSERT INTO control.fetch_request (provider_event_id, url, reason, requested_at)
           VALUES ('us2', 'https://evil.example.com/us2.geojson', 'updated', now())"""
    )
    h.clock.advance(seconds=60)
    h.collector.tick()
    assert not any("evil.example.com" in u for u in h.usgs.urls())
    assert h.count("control.quarantine", "reason LIKE 'fetch not-allowed%%'") == 1
    assert h.count("control.fetch_request") == 0


def test_f21_grid_with_dtd_is_unsupported(
    harness: Harness, usgs_json: Callable[[str], Any], usgs_fixture: Callable[[str], bytes]
) -> None:
    h = harness
    quiet(h)
    grid = usgs_fixture("grid_us7000tiur").replace(
        b"?>", b'?><!DOCTYPE shakemap_grid [<!ENTITY x "y">]>', 1
    )
    detail = usgs_json("detail_us7000tiur")
    contents = detail["properties"]["products"]["shakemap"][0]["contents"]["download/grid.xml"]
    contents.update(length=len(grid), sha256=sha256_b64(grid))
    h.usgs.on(incremental, ok(collection(tiur_feature(usgs_json))))
    h.usgs.on(eventid("us7000tiur"), ok(detail))
    h.usgs.on(TIUR_GRID, ok(grid))
    h.collector.tick()
    assert h.one("SELECT status FROM quake.shakemap_grid") == "UNSUPPORTED_GRID"
    assert "DTD" in h.one("SELECT reason FROM quake.shakemap_grid")


def test_f21_gzip_bomb_in_feed(harness: Harness) -> None:
    h = harness
    h.usgs.on(incremental, ok(collection()))
    bomb = gzip.compress(b"0" * (10 * 1024 * 1024 + 1))
    h.usgs.on(FEED_URL, (200, bomb, {"content-encoding": "gzip"}))
    h.collector.tick()
    assert h.count("control.capture") == 0
    assert "decodificados" in h.one(
        "SELECT last_error FROM control.source_status WHERE source = 'feed'"
    )


def test_failed_incremental_does_not_advance_watermark(harness: Harness) -> None:
    h = harness
    h.usgs.on(FEED_URL, ok(collection()))
    h.usgs.on(incremental, (503, b"", {}))
    start = h.clock()
    h.collector.tick()
    assert h.one("SELECT watermark FROM control.source_status WHERE source = 'incremental'") is None

    h.usgs.on(incremental, ok(collection()))
    h.clock.advance(minutes=10)
    first_ok = h.clock()
    h.collector.tick()
    watermark = h.one("SELECT watermark FROM control.source_status WHERE source = 'incremental'")
    assert watermark == first_ok
    assert first_ok > start

    h.usgs.on(incremental, (503, b"", {}))
    h.clock.advance(minutes=10)
    h.collector.tick()
    assert (
        h.one("SELECT watermark FROM control.source_status WHERE source = 'incremental'")
        == watermark
    )

    h.usgs.on(incremental, ok(collection()))
    h.clock.advance(minutes=10)
    h.collector.tick()
    last = [r for r in h.usgs.requests if incremental(r)][-1]
    assert last.url.params["updatedafter"] == fdsn_time(watermark - timedelta(seconds=600))
    assert last.url.params["includedeleted"] == "true"


def test_incremental_splits_the_window_when_fdsn_refuses(harness: Harness) -> None:
    h = harness
    h.usgs.on(FEED_URL, ok(collection()))

    def too_many(request: httpx.Request) -> bool:
        if not incremental(request):
            return False
        start = datetime.fromisoformat(request.url.params["starttime"])
        end = datetime.fromisoformat(request.url.params["endtime"])
        return (end - start).days > 30

    h.usgs.on(incremental, ok(collection(feature("us1", updated=1_000))))
    h.usgs.on(too_many, (400, b"20001 matching events exceeds search limit of 20000.", {}))
    h.collector.tick()
    windows = [r for r in h.usgs.requests if incremental(r)]
    assert len(windows) > 1
    assert (
        h.one("SELECT last_error FROM control.source_status WHERE source = 'incremental'") is None
    )
    assert h.count("quake.event_revision") == 1  # a mesma revisão em várias janelas entra uma vez


def test_projection_error_goes_to_quarantine_and_pending_capture_resumes(
    harness: Harness,
) -> None:
    from quakeexposure.ingestion import store
    from quakeexposure.ingestion.projection import project_pending

    h = harness
    sha, path = h.blobs.put(b'{"type": "FeatureCollection"}')  # sem "features"
    with h.conn.transaction():
        store.store_capture(
            h.conn, scope="LIVE", kind="feed", url=FEED_URL, fetched_at=h.clock(), http_status=200,
            body_bytes=30, sha256=sha, blob_path=path,
        )  # fmt: skip
    good = json.dumps(collection(feature("us1", updated=1_000))).encode()
    sha2, path2 = h.blobs.put(good)
    with h.conn.transaction():  # gravada e não projetada: como se o processo tivesse caído
        store.store_capture(
            h.conn, scope="LIVE", kind="feed", url=FEED_URL, fetched_at=h.clock(), http_status=200,
            body_bytes=len(good), sha256=sha2, blob_path=path2,
        )  # fmt: skip
    projected = project_pending(h.conn, h.blobs, h.clock())
    assert projected.quarantined == 1 and projected.new_revisions == 1
    assert h.one("SELECT projection_status FROM control.capture WHERE sha256 = %s", sha) == (
        "QUARANTINED"
    )
    assert h.one("SELECT item_sha256 FROM control.quarantine") == sha
    assert h.count("control.capture", "projected_at IS NULL") == 0


def test_backfill_cursor_only_advances_for_finished_days(harness: Harness) -> None:
    h = harness
    day2 = lambda r: incremental(r) and r.url.params["starttime"].startswith("2026-09-02")  # noqa: E731
    h.usgs.on(incremental, ok(collection(feature("us1", updated=1_000))))
    h.usgs.on(day2, (503, b"", {}))
    with pytest.raises(FetchError):
        h.collector.backfill(date(2026, 9, 1), date(2026, 9, 3))
    assert [
        d.isoformat() for (d,) in h.conn.execute("SELECT day FROM control.backfill_window")
    ] == ["2026-09-01"]

    h.usgs.on(day2, ok(collection(feature("us2", updated=2_000))))
    before = len(h.usgs.requests)
    h.collector.backfill(date(2026, 9, 1), date(2026, 9, 3))
    days = [r.url.params["starttime"][:10] for r in h.usgs.requests[before:]]
    assert days == ["2026-09-02", "2026-09-03"]  # o dia 1 não é consultado de novo
    assert h.count("control.backfill_window") == 3


def test_alias_conflict_is_reconsulted_once_not_every_minute(harness: Harness) -> None:
    h = harness
    quiet(h)
    h.usgs.on(FEED_URL, ok(collection(feature("us1", updated=1_000, ids=["x1"], types=""))))
    h.collector.tick()
    claim = feature("ak2", updated=2_000, ids=["x1"], types="")  # x1 é de us1 e us1 não está aqui
    h.usgs.on(FEED_URL, ok(collection(claim)))
    for _ in range(4):
        h.clock.advance(seconds=60)
        h.collector.tick()
    assert h.count("control.capture", "kind = 'feed'") == 2
    assert h.count("control.quarantine", "reason LIKE 'ALIAS_CONFLICT%%'") == 1
    reconsults = [u for u in h.usgs.urls() if "eventid=" in u]
    assert sorted(reconsults) == sorted(
        f"{FDSN_QUERY_URL}?eventid={e}&includedeleted=true&format=geojson"
        for e in ("ak2", "us1", "x1")
    )


def test_incremental_too_large_is_split_and_watermark_advances(harness: Harness) -> None:
    h = harness
    h.usgs.on(FEED_URL, ok(collection()))
    padded = json.dumps(collection()).encode() + b" " * (10 * 1024 * 1024)

    def wide(request: httpx.Request) -> bool:
        if not incremental(request):
            return False
        start = datetime.fromisoformat(request.url.params["starttime"])
        end = datetime.fromisoformat(request.url.params["endtime"])
        return (end - start).days > 30

    h.usgs.on(incremental, ok(collection(feature("us1", updated=1_000))))
    h.usgs.on(wide, (200, padded, {"content-encoding": "gzip"}))
    h.collector.tick()
    assert len([r for r in h.usgs.requests if incremental(r)]) > 1
    assert h.one("SELECT watermark FROM control.source_status WHERE source = 'incremental'") == (
        h.clock()
    )
    assert h.count("quake.event_revision") == 1


def test_useless_200_does_not_count_as_success(harness: Harness) -> None:
    h = harness
    h.usgs.on(FEED_URL, (200, b"<html>maintenance</html>", {}))
    h.usgs.on(incremental, (200, b'{"error": "x"}', {}))
    h.collector.tick()
    for source in ("feed", "incremental"):
        row = h.conn.execute(
            "SELECT last_ok_at, watermark, last_error FROM control.source_status WHERE source = %s",
            (source,),
        ).fetchone()
        assert row is not None
        assert row[0] is None and row[1] is None
        assert "FeatureCollection" in row[2]
    assert h.count("control.capture") == 0
    assert h.count("control.quarantine") == 2

    h.clock.advance(seconds=60)
    h.collector.tick()
    assert h.count("control.quarantine") == 2  # a mesma resposta não é registrada de novo


def test_watch_add_fetches_the_event_and_its_grid_out_of_scope(
    harness: Harness, usgs_fixture: Callable[[str], bytes]
) -> None:
    from quakeexposure.ingestion import store
    from quakeexposure.ingestion.projection import reconsult_url

    h = harness
    h.clock.advance(days=30)
    quiet(h)
    h.usgs.on(eventid("us7000tiur"), ok(usgs_fixture("detail_us7000tiur")))
    h.usgs.on(TIUR_GRID, ok(usgs_fixture("grid_us7000tiur")))
    store.watch(h.conn, "us7000tiur", reconsult_url("us7000tiur"), h.clock())
    h.collector.tick()
    assert h.one("SELECT product_revision_id FROM quake.event_map") == TIUR_PRODUCT
    assert h.one("SELECT status FROM quake.shakemap_grid") == "OK"
