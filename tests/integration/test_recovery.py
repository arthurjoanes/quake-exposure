"""Situações em que algo dá errado no meio do caminho e o coletor precisa se recuperar."""

import json
from collections.abc import Callable
from typing import Any

import httpx
import psycopg
from builders import DETAIL_URL, collection, feature

from quakeexposure.ingestion.collector import FEED_URL

from .conftest import Database, Harness, ok


def incremental(request: httpx.Request) -> bool:
    return request.url.path == "/fdsnws/event/1/query" and "eventid" not in request.url.params


def eventid(event_id: str) -> Callable[[httpx.Request], bool]:
    return lambda r: r.url.params.get("eventid") == event_id


def product(event_id: str, update: int, weight: int = 100) -> dict[str, Any]:
    return {
        "id": f"urn:usgs-product:{event_id[:2]}:shakemap:{event_id}:{update}",
        "type": "shakemap",
        "code": event_id,
        "source": event_id[:2],
        "updateTime": update,
        "status": "UPDATE",
        "preferredWeight": weight,
        "contents": {},
    }


def summary(event_id: str, **kwargs: Any) -> dict[str, Any]:
    f = feature(event_id, **kwargs)
    f["properties"]["detail"] = DETAIL_URL.format(event_id)
    return f


def detail(event_id: str, products: list[dict[str, Any]], **kwargs: Any) -> dict[str, Any]:
    f = feature(event_id, **kwargs)
    f["properties"]["products"] = {"shakemap": products}
    return f


def map_of(h: Harness, event_id: str) -> str | None:
    value = h.one(
        """SELECT m.product_revision_id FROM quake.event_map m
           JOIN quake.alias a USING (stream_scope, earthquake_uid) WHERE a.provider_id = %s""",
        event_id,
    )
    return None if value is None else str(value)


def claim_x1_from_us1(h: Harness) -> dict[str, Any]:
    """us1 tem x1; ak2 aparece com ShakeMap e o detail dele já reivindica x1."""
    h.usgs.on(incremental, ok(collection()))
    h.usgs.on(FEED_URL, ok(collection(feature("us1", updated=1_000, ids=["x1"], types=""))))
    h.collector.tick()
    claim = detail("ak2", [product("ak2", 2_500)], updated=3_000, ids=["x1"])
    h.usgs.on(FEED_URL, ok(collection(summary("ak2", updated=2_000))))
    h.usgs.on(DETAIL_URL.format("ak2"), ok(claim))
    h.clock.advance(seconds=60)
    h.collector.tick()  # feed -> detail do ak2 -> ALIAS_CONFLICT -> reconsultas pedidas
    assert h.count("control.alias_conflict") == 1
    return claim


def test_reconsult_that_releases_the_id_lets_the_claim_in(harness: Harness) -> None:
    h = harness
    claim = claim_x1_from_us1(h)
    order = [r[0] for r in h.conn.execute(
        "SELECT provider_event_id FROM control.fetch_request ORDER BY requested_at, 1"
    )]  # fmt: skip
    assert order == ["us1", "x1", "ak2"]  # quem tem o id, o id e, por último, a revisão
    # o USGS já soltou o x1 do us1; x1 e ak2 devolvem o mesmo documento do ak2
    h.usgs.on(eventid("us1"), ok(feature("us1", updated=4_000, types="")))
    h.usgs.on(eventid("x1"), ok(claim))
    h.usgs.on(eventid("ak2"), ok(claim))
    h.clock.advance(seconds=60)
    h.collector.tick()
    assert map_of(h, "ak2") == "urn:usgs-product:ak:shakemap:ak2:2500"
    assert map_of(h, "x1") == map_of(h, "ak2")
    assert h.count("control.alias_conflict") == 0


def test_owner_releasing_later_triggers_a_new_reconsult(harness: Harness) -> None:
    h = harness
    claim = claim_x1_from_us1(h)
    # na reconsulta o USGS ainda não mudou nada
    h.usgs.on(eventid("us1"), ok(feature("us1", updated=1_000, ids=["x1"], types="")))
    h.usgs.on(eventid("x1"), ok(claim))
    h.usgs.on(eventid("ak2"), ok(claim))
    h.clock.advance(seconds=60)
    h.collector.tick()
    assert h.count("control.alias_conflict") == 1 and map_of(h, "ak2") is None

    # dias depois, o incremental traz o us1 soltando o x1: o ak2 parado é reconsultado
    h.usgs.on(incremental, ok(collection(feature("us1", updated=5_000, types=""))))
    h.clock.advance(minutes=11)
    h.collector.tick()
    h.clock.advance(seconds=60)
    h.collector.tick()
    assert map_of(h, "ak2") == "urn:usgs-product:ak:shakemap:ak2:2500"
    assert h.count("control.alias_conflict") == 0


def test_same_detail_bytes_are_projected_again(harness: Harness) -> None:
    h = harness
    claim = claim_x1_from_us1(h)
    for event_id in ("us1", "x1", "ak2"):
        h.usgs.on(eventid(event_id), (404, b"", {}))
    h.clock.advance(seconds=60)
    h.collector.tick()  # reconsultas não ajudam; o conflito continua
    # o feed resolve: us1 solta o x1 e o ak2 volta; o detail pedido de novo tem os mesmos bytes
    h.usgs.on(FEED_URL, ok(collection(feature("us1", updated=4_000, types=""),
                                      summary("ak2", updated=3_000, ids=["x1"]))))  # fmt: skip
    h.usgs.on(DETAIL_URL.format("ak2"), ok(claim))
    for _ in range(2):
        h.clock.advance(seconds=60)
        h.collector.tick()
    assert map_of(h, "ak2") == "urn:usgs-product:ak:shakemap:ak2:2500"


def test_lock_timeout_keeps_the_capture_pending_and_the_watermark(
    harness: Harness, database: Database
) -> None:
    h = harness
    h.usgs.on(FEED_URL, ok(collection()))
    h.usgs.on(incremental, ok(collection(feature("us1", updated=1_000, types=""))))
    h.conn.execute("SET statement_timeout = '1s'")
    # outra conexão segura o lock da projeção, como um backfill projetando ao mesmo tempo
    with database.connect() as other, other.transaction():
        other.execute("SELECT pg_advisory_xact_lock(hashtext('quake-exposure:projection'))")
        h.collector.poll_incremental(h.clock())
    status = h.conn.execute(
        "SELECT watermark, last_error FROM control.source_status WHERE source = 'incremental'"
    ).fetchone()
    assert status is not None and status[0] is None and "statement timeout" in status[1]
    assert h.count("control.capture", "projected_at IS NULL") == 1
    assert h.count("control.quarantine") == 0

    h.clock.advance(minutes=10)
    h.collector.tick()
    assert h.count("quake.event_revision") == 1
    assert h.one("SELECT watermark FROM control.source_status WHERE source = 'incremental'")


def test_product_that_the_database_refuses_does_not_hold_the_revision(harness: Harness) -> None:
    h = harness
    h.usgs.on(incremental, ok(collection()))
    h.usgs.on(FEED_URL, ok(collection(summary("us1", updated=1_000))))
    bad = product("us1", 1_500, weight=2**31)  # passa no contrato, a coluna int recusa
    h.usgs.on(DETAIL_URL.format("us1"), ok(detail("us1", [bad], updated=2_000, status="deleted")))
    h.collector.tick()
    assert h.one("SELECT lifecycle FROM quake.earthquake") == "RETRACTED"
    assert h.one("SELECT reason FROM control.quarantine").startswith(
        "products: NumericValueOutOfRange 22003"
    )


def test_useless_detail_response_is_fetched_again(harness: Harness) -> None:
    h = harness
    h.usgs.on(incremental, ok(collection()))
    h.usgs.on(FEED_URL, ok(collection(summary("us1", updated=1_000))))
    good = detail("us1", [product("us1", 1_500)], updated=1_000)
    h.usgs.on(DETAIL_URL.format("us1"), (200, b"<html>maintenance</html>", {}), ok(good))
    h.collector.tick()
    assert h.one("SELECT attempts FROM control.fetch_request") == 1
    assert h.count("control.capture", "kind = 'detail'") == 0
    h.clock.advance(seconds=60)
    h.collector.tick()
    assert map_of(h, "us1") == "urn:usgs-product:us:shakemap:us1:1500"
    assert h.count("control.fetch_request") == 0


def test_feature_without_canonical_form_is_quarantined_and_the_rest_goes_in(
    harness: Harness,
) -> None:
    h = harness
    h.usgs.on(incremental, ok(collection()))
    nan = feature("us8", updated=1_000, types="")
    nan["geometry"]["coordinates"][0] = float("nan")  # o json.loads aceita, o hash não
    far = feature("us9", updated=10**16, types="")  # ano fora do datetime
    body = json.dumps(collection(nan, far, feature("us1", updated=2_000, types=""))).encode()
    h.usgs.on(FEED_URL, (200, body, {}))
    h.collector.tick()
    assert h.count("quake.event_revision") == 1
    reasons = sorted(r for (r,) in h.conn.execute("SELECT reason FROM control.quarantine"))
    assert reasons == ["contract: properties/updated: maximum 253402300799999",
                       "contract: sem forma canônica"]  # fmt: skip
    h.clock.advance(seconds=60)
    h.collector.tick()
    assert h.count("control.quarantine") == 2 and h.count("control.capture") == 1


def test_merge_moves_the_chosen_product_to_the_surviving_uid(harness: Harness) -> None:
    h = harness
    h.usgs.on(incremental, ok(collection()))
    ak = detail("ak1", [product("ak1", 900, weight=227)], updated=1_000)
    us = feature("us1", updated=1_500, types="")  # dois eventos separados antes da fusão
    h.usgs.on(FEED_URL, ok(collection(summary("ak1", updated=1_000), us)))
    h.usgs.on(DETAIL_URL.format("ak1"), ok(ak))
    h.collector.tick()
    assert h.count("quake.earthquake", "merged_into IS NULL") == 2
    merged = detail(
        "us1", [product("ak1", 900, weight=227), product("us1", 950, weight=82)],
        updated=3_000, ids=["ak1"],
    )  # fmt: skip
    h.usgs.on(FEED_URL, ok(collection(summary("us1", updated=3_000, ids=["ak1"]))))
    h.usgs.on(DETAIL_URL.format("us1"), ok(merged))
    h.clock.advance(seconds=60)
    h.collector.tick()
    assert h.count("quake.earthquake", "merged_into IS NOT NULL") == 1
    active = h.one("SELECT earthquake_uid FROM quake.earthquake WHERE merged_into IS NULL")
    assert map_of(h, "us1") == "urn:usgs-product:ak:shakemap:ak1:900"
    assert (
        h.one(
            "SELECT earthquake_uid FROM quake.shakemap_product WHERE product_revision_id = %s",
            "urn:usgs-product:ak:shakemap:ak1:900",
        )
        == active
    )


def test_roles_cannot_reach_the_conflict_table_beyond_their_grants(database: Database) -> None:
    with database.connect("qe_api_read") as api:
        assert api.execute("SELECT count(*) FROM control.alias_conflict").fetchone() == (0,)
        try:
            api.execute("DELETE FROM control.alias_conflict")
        except psycopg.errors.ReadOnlySqlTransaction:
            pass
        else:
            raise AssertionError("qe_api_read conseguiu escrever")
