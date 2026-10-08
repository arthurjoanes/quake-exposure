"""Quarentena: uma linha por item, sem payload, e sem reprocessar o que não muda."""

import json

import httpx
from builders import collection, feature

from quakeexposure.ingestion.collector import FEED_URL

from .conftest import Harness, ok


def incremental(request: httpx.Request) -> bool:
    return request.url.path == "/fdsnws/event/1/query" and "eventid" not in request.url.params


def feed_with_news(h: Harness, *fixed: dict[str, object], minutes: int) -> None:
    """Como o all_day real: o que está em conflito fica, e a cada minuto chega outro evento."""
    for _ in range(minutes):
        minute = int(h.clock().timestamp()) // 60
        news = feature(f"nc{minute}", updated=minute, types="")
        h.usgs.on(FEED_URL, ok(collection(*fixed, news)))
        h.clock.advance(seconds=60)
        h.collector.tick()


def test_conflict_stays_one_row_while_the_feed_keeps_changing(harness: Harness) -> None:
    h = harness
    h.usgs.on(incremental, ok(collection()))
    h.usgs.on(FEED_URL, ok(collection(feature("us1", updated=1_000, mag=5.0, types=""))))
    h.collector.tick()
    feed_with_news(h, feature("us1", updated=1_000, mag=6.1, types=""), minutes=4)
    assert h.count("control.quarantine", "reason = 'CONFLICT'") == 1
    assert h.count("quake.event_revision", "provider_event_id = 'us1'") == 1
    assert h.count("quake.event_revision", "provider_event_id LIKE 'nc%%'") == 4


def test_alias_conflict_is_reconsulted_once_while_the_feed_keeps_changing(
    harness: Harness,
) -> None:
    h = harness
    h.usgs.on(incremental, ok(collection()))
    h.usgs.on(FEED_URL, ok(collection(feature("us1", updated=1_000, ids=["x1"], types=""))))
    h.collector.tick()
    feed_with_news(h, feature("ak2", updated=2_000, ids=["x1"], types=""), minutes=4)
    assert h.count("control.quarantine", "reason LIKE 'ALIAS_CONFLICT%%'") == 1
    reconsulted = sorted(
        r.url.params["eventid"] for r in h.usgs.requests if "eventid" in r.url.params
    )
    assert reconsulted == ["ak2", "us1", "x1"]  # a revisão, o id disputado e o dono dele


def test_alias_conflict_resolved_later_in_the_same_response(harness: Harness) -> None:
    h = harness
    h.usgs.on(incremental, ok(collection()))
    h.usgs.on(FEED_URL, ok(collection(feature("us1", updated=1_000, ids=["x1"], types=""))))
    h.collector.tick()
    claim = feature("ak2", updated=2_000, ids=["x1"], types="")
    release = feature("us1", updated=3_000, types="")  # us1 solta o x1
    h.usgs.on(FEED_URL, ok(collection(claim, release)))  # o pedido vem antes da soltura
    h.clock.advance(seconds=60)
    h.collector.tick()
    assert h.one("SELECT earthquake_uid IS NOT NULL FROM quake.alias WHERE provider_id = 'x1'")
    assert (
        h.one(
            """SELECT e.preferred_id FROM quake.alias a JOIN quake.earthquake e
           USING (stream_scope, earthquake_uid) WHERE a.provider_id = 'x1'"""
        )
        == "ak2"
    )


def test_alias_conflict_resolved_by_a_later_capture(harness: Harness) -> None:
    h = harness
    h.usgs.on(incremental, ok(collection()))
    h.usgs.on(FEED_URL, ok(collection(feature("us1", updated=1_000, ids=["x1"], types=""))))
    h.collector.tick()
    claim = feature("ak2", updated=2_000, ids=["x1"], types="")
    feed_with_news(h, claim, minutes=1)
    assert h.count("quake.event_revision", "provider_event_id = 'ak2'") == 0

    h.usgs.on(incremental, ok(collection(feature("us1", updated=3_000, types=""))))
    h.clock.advance(minutes=11)
    h.collector.tick()  # o incremental solta o x1; o feed seguinte traz o ak2 de novo
    feed_with_news(h, claim, minutes=1)
    assert h.count("quake.event_revision", "provider_event_id = 'ak2'") == 1


def test_feature_that_breaks_projection_does_not_take_the_rest_down(harness: Harness) -> None:
    h = harness
    h.usgs.on(incremental, ok(collection()))
    poison = feature("us\u0000x", updated=1_000, types="")  # passa no contrato, o banco recusa
    for n in range(3):
        h.usgs.on(FEED_URL, ok(collection(poison, feature(f"us{n}", updated=2_000 + n, types=""))))
        h.clock.advance(seconds=60)
        h.collector.tick()
    assert h.count("quake.event_revision") == 3
    reasons = [r for (r,) in h.conn.execute("SELECT reason FROM control.quarantine")]
    assert len(reasons) == 1
    assert reasons[0].startswith("projection: DataError") and "\x00" not in reasons[0]
    assert h.count("control.capture", "projection_status = 'QUARANTINED'") == 0

    h.usgs.on(FEED_URL, ok(collection(poison, feature("us2", updated=2_002, types=""))))
    h.clock.advance(seconds=60)
    h.collector.tick()
    assert h.count("control.capture") == 3  # nada novo além da feature já em quarentena


def test_server_side_error_in_one_feature_keeps_the_others(harness: Harness) -> None:
    import hashlib

    h = harness
    h.usgs.on(incremental, ok(collection()))
    # id grande e sem padrão: passa no contrato, o índice do banco recusa (erro no servidor)
    huge = "us" + "".join(hashlib.sha256(str(i).encode()).hexdigest() for i in range(140))
    h.usgs.on(FEED_URL, ok(collection(feature(huge, updated=1_000, types=""),
                                      feature("us1", updated=2_000, types=""))))  # fmt: skip
    h.collector.tick()
    assert h.count("quake.event_revision") == 1
    reason = h.one("SELECT reason FROM control.quarantine")
    assert reason.startswith("projection: ProgramLimitExceeded 54000")
    assert huge not in reason


def test_feature_collection_without_a_features_list_is_a_failure(harness: Harness) -> None:
    h = harness
    h.usgs.on(FEED_URL, ok({"type": "FeatureCollection", "metadata": {}, "features": None}))
    h.usgs.on(incremental, ok({"type": "FeatureCollection", "metadata": {"status": 503}}))
    h.collector.tick()
    rows = h.conn.execute(
        "SELECT source, last_ok_at, watermark, last_error FROM control.source_status ORDER BY 1"
    ).fetchall()
    assert [(s, ok_at, wm) for s, ok_at, wm, _ in rows] == [
        ("feed", None, None),
        ("incremental", None, None),
    ]
    assert all("FeatureCollection" in err for *_, err in rows)


def test_quarantine_reason_carries_no_response_body(harness: Harness) -> None:
    h = harness
    h.usgs.on(FEED_URL, ok(collection()))
    h.usgs.on(incremental, ok(collection()))
    body = json.dumps({"id": "us9", "properties": {"place": "LUGAR DO CORPO"}}).encode()
    h.usgs.on("https://earthquake.usgs.gov/earthquakes/feed/v1.0/detail/us9", (403, body, {}))
    h.conn.execute(
        """INSERT INTO control.fetch_request (provider_event_id, url, reason, requested_at)
           VALUES ('us9', 'https://earthquake.usgs.gov/earthquakes/feed/v1.0/detail/us9.geojson',
                   'updated', now())"""
    )
    h.collector.tick()
    reason = h.one("SELECT reason FROM control.quarantine")
    assert reason.endswith("HTTP 403") and "LUGAR" not in reason
