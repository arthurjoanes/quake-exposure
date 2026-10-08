"""Cálculo e publicação com PostGIS real: worker, CAS, escopo, fusão, mapa, raio e a API."""

import json
import uuid
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from builders import collection, feature, grid_xml
from fastapi.testclient import TestClient
from geographiclib.geodesic import Geodesic
from jsonschema import Draft202012Validator

from quakeexposure.api import create_app
from quakeexposure.domain.money import Currency
from quakeexposure.exposure import jobs, worker
from quakeexposure.exposure.explain import diff, publications
from quakeexposure.ingestion.collector import FEED_URL
from quakeexposure.ingestion.projection import project_pending
from quakeexposure.ingestion.replay import capture_fault, replay
from quakeexposure.portfolio.store import load
from quakeexposure.portfolio.synthetic import VALID_FROM, SeedRow, generate

from .conftest import Database, Harness, ok

ROOT = Path(__file__).parents[2]
RESULT = Draft202012Validator(
    json.loads((ROOT / "contracts" / "exposure-result.schema.json").read_text("utf-8"))
)
FLORES = (120.5793, -8.307)


def incremental(request: Any) -> bool:
    return request.url.path == "/fdsnws/event/1/query" and "eventid" not in request.url.params


def asset(
    tenant: str, asset_id: str, lon: float, lat: float, value: int = 100_00,
    currency: Currency = Currency.BRL, **kwargs: Any,
) -> SeedRow:  # fmt: skip
    fields: dict[str, Any] = {
        "tenant_id": tenant, "asset_interval_id": f"{asset_id}#1", "row_version": 1,
        "asset_id": asset_id, "subject_token": uuid.uuid4(), "known_epoch": 1,
        "valid_from": VALID_FROM, "valid_to": None, "lon": lon, "lat": lat,
        "insured_value_minor": value, "currency": currency, "is_deleted": False,
    }  # fmt: skip
    fields.update(kwargs)
    return SeedRow(**fields)


@pytest.fixture
def api(database: Database) -> Iterator[TestClient]:
    app = create_app(connector=lambda: database.connect("qe_api_read"), static_dir=None)
    with TestClient(app) as client:
        yield client


def publish(h: Harness) -> dict[str, int]:
    return worker.run_pending(h.conn, h.blobs, h.clock)


def current(h: Harness, scope: str = "LIVE") -> list[tuple[Any, ...]]:
    return h.conn.execute(
        """SELECT p.tenant_id, a.provider_id, p.mode, p.calculation_id
           FROM serving.current_publication p
           JOIN quake.earthquake e USING (stream_scope, earthquake_uid)
           JOIN quake.alias a ON a.stream_scope = e.stream_scope
                              AND a.earthquake_uid = e.earthquake_uid
                              AND a.provider_id = e.preferred_id
           WHERE p.stream_scope = %s ORDER BY 1, 2, 3""",
        (scope,),
    ).fetchall()


def feed(h: Harness, *features: dict[str, Any]) -> None:
    h.usgs.on(incremental, ok(collection()))
    h.usgs.on(FEED_URL, ok(collection(*features)))
    h.collector.tick()
    h.clock.advance(seconds=60)


def test_replay_publishes_both_modes_and_totals_match_the_facts(harness: Harness) -> None:
    h = harness
    load(h.conn, generate(assets=600, seed=7))
    replay(h.conn, h.blobs, h.clock())
    done = publish(h)
    assert done["failed"] == 0 and done["published"] > 0
    flores = [row for row in current(h, "REPLAY") if row[1] == "us7000tiur"]
    assert {(t, m) for t, _, m, _ in flores} == {
        (t, m) for t in ("operadora-b", "seguradora-a") for m in ("RADIUS_TRIAGE", "SHAKEMAP_MMI")
    }
    for *_, calc in flores:
        status, eligible, qualifying = h.conn.execute(
            """SELECT status, eligible_count, qualifying_count FROM exposure.calculation
               WHERE calculation_id = %s""",
            (calc,),
        ).fetchone()  # type: ignore[misc]
        assert status == "COMPLETE" and qualifying > 0
        facts = h.conn.execute(
            """SELECT count(*) FILTER (WHERE eligible), count(*) FILTER (WHERE qualifying)
               FROM exposure.fact WHERE calculation_id = %s""",
            (calc,),
        ).fetchone()
        assert facts == (eligible, qualifying)
        mismatch = h.conn.execute(
            """SELECT t.currency FROM exposure.calculation_total t
               WHERE t.calculation_id = %s AND t.qualifying_value_minor <> (
                   SELECT coalesce(sum(f.insured_value_minor), 0) FROM exposure.fact f
                   WHERE f.calculation_id = t.calculation_id AND f.qualifying
                     AND f.currency = t.currency)""",
            (calc,),
        ).fetchall()
        assert mismatch == []
    # sem ShakeMap no evento (detail não capturado, sem 'shakemap' em types): NO_MAP
    assert "SHAKEMAP_MMI" in {m for _, _, m, _ in current(h, "REPLAY")}


def test_f01_same_capture_twice_keeps_the_publication(harness: Harness) -> None:
    h = harness
    load(h.conn, [asset("t1", "a1", *FLORES)])
    event = feature("us1", updated=1_000, lon=FLORES[0], lat=FLORES[1])
    feed(h, event)
    publish(h)
    before = current(h)
    assert len(before) == 1  # RADIUS; o MMI espera o detail
    feed(h, event)
    assert publish(h)["published"] == 0
    assert current(h) == before
    assert h.count("exposure.calculation") == 1


def test_f04_merge_leaves_one_publication_and_the_value_once(harness: Harness) -> None:
    h = harness
    load(h.conn, [asset("t1", "a1", *FLORES, value=5_000)])
    feed(h, feature("ak1", updated=1_000, lon=FLORES[0], lat=FLORES[1]),
         feature("us1", updated=1_500, lon=FLORES[0] + 0.01, lat=FLORES[1]))  # fmt: skip
    publish(h)
    assert len(current(h)) == 2  # dois eventos, o mesmo ativo exposto duas vezes
    feed(h, feature("us1", updated=3_000, ids=["ak1"], lon=FLORES[0], lat=FLORES[1]))
    publish(h)
    rows = current(h)
    assert [(t, pid, m) for t, pid, m, _ in rows] == [("t1", "us1", "RADIUS_TRIAGE")]
    value = h.one(
        """SELECT sum(t.qualifying_value_minor) FROM serving.current_publication p
           JOIN exposure.calculation_total t USING (calculation_id) WHERE t.currency = 'BRL'"""
    )
    assert value == 5_000
    assert h.count("serving.publication_log", "calculation_id IS NULL") == 1  # a retirada


def test_f05_publication_uses_the_preferred_map_and_no_map_has_null_counts(
    harness: Harness, usgs_fixture: Callable[[str], bytes]
) -> None:
    h = harness
    load(h.conn, [asset("t1", "a1", -153.609, 58.802)])
    detail = usgs_fixture("detail_aka2026tuxgky")
    doc = json.loads(detail)
    ak = "urn:usgs-product:ak:shakemap:a2026tuxgky:1791312073169"
    grid = grid_xml([-154.0, -153.5, -153.0], [59.0, 58.5], mmi=lambda r, c: 6.5)
    parent = "0" * 64
    capture_fault(h.conn, h.blobs, parent_sha256=parent, kind="detail", url="fault://d",
                  fetched_at=h.clock(), body=detail)  # fmt: skip
    capture_fault(h.conn, h.blobs, parent_sha256=parent, kind="grid", url="fault://g",
                  fetched_at=h.clock(), body=grid, product=ak)  # fmt: skip
    project_pending(h.conn, h.blobs, h.clock())
    publish(h)
    mmi = h.one(
        """SELECT c.product_revision_id FROM serving.current_publication p
           JOIN exposure.calculation c USING (calculation_id) WHERE p.mode = 'SHAKEMAP_MMI'"""
    )
    assert mmi == ak  # a ak (peso 227), não a us mais recente

    gone = dict(doc)
    gone["properties"] = {**doc["properties"], "updated": doc["properties"]["updated"] + 1000,
                          "products": {}}  # fmt: skip
    capture_fault(h.conn, h.blobs, parent_sha256=parent, kind="detail", url="fault://d2",
                  fetched_at=h.clock(), body=json.dumps(gone).encode())  # fmt: skip
    project_pending(h.conn, h.blobs, h.clock())
    publish(h)
    row = h.conn.execute(
        """SELECT c.status, c.eligible_count, c.qualifying_count,
                  (SELECT count(*) FROM exposure.calculation_total t
                   WHERE t.calculation_id = c.calculation_id)
           FROM serving.current_publication p JOIN exposure.calculation c USING (calculation_id)
           WHERE p.mode = 'SHAKEMAP_MMI'"""
    ).fetchone()
    assert row == ("NO_MAP", None, None, 0)


def test_f11_worker_that_dies_before_commit_leaves_nothing(
    harness: Harness, api: TestClient
) -> None:
    h = harness
    load(h.conn, [asset("t1", "a1", *FLORES)])
    feed(h, feature("us1", updated=1_000, lon=FLORES[0], lat=FLORES[1]))
    jobs.mark(h.conn, h.clock())
    job = worker.claim(h.conn, h.clock())
    assert job is not None

    def die() -> None:
        raise RuntimeError("worker morto")

    with pytest.raises(RuntimeError):
        worker.run(h.conn, h.blobs, job, h.clock, before_commit=die)
    assert h.count("exposure.calculation") == 0 and h.count("exposure.fact") == 0
    assert h.count("serving.current_publication") == 0
    query = {"tenant": "t1", "earthquake_uid": str(job.earthquake_uid), "mode": job.mode}
    assert api.get("/api/publications/current", params=query).status_code == 404

    assert worker.claim(h.conn, h.clock()) is None  # a lease ainda vale
    h.clock.advance(minutes=3)
    assert publish(h)["published"] == 1  # lease vencida: outro worker termina
    assert api.get("/api/publications/current", params=query).status_code == 200


def test_f12_stale_calculation_is_refused_by_the_generation(
    harness: Harness, database: Database, api: TestClient
) -> None:
    h = harness
    load(h.conn, [asset("t1", "near", *FLORES), asset("t1", "east", FLORES[0] + 1.2, FLORES[1])])
    feed(h, feature("us1", updated=1_000, lon=FLORES[0], lat=FLORES[1]))
    jobs.mark(h.conn, h.clock())
    job = worker.claim(h.conn, h.clock())
    assert job is not None
    query = {"tenant": "t1", "earthquake_uid": str(job.earthquake_uid), "mode": job.mode}
    seen_during_pause: list[int] = []

    def new_revision_arrives() -> None:
        # outra conexão: o epicentro anda 1,2° para leste e a geração desejada sobe
        with database.connect() as other:
            moved = feature("us1", updated=2_000, lon=FLORES[0] + 1.2, lat=FLORES[1])
            other.execute("SELECT 1")  # mesma rotina do coletor, noutra transação
            from quakeexposure.ingestion.collector import Collector

            Collector(other, h.collector.client, h.blobs, h.clock)._take_collection(
                "feed", _fetched(collection(moved), h.clock())
            )
            jobs.mark(other, h.clock())
        seen_during_pause.append(api.get("/api/publications/current", params=query).status_code)

    assert worker.run(h.conn, h.blobs, job, h.clock, before_commit=new_revision_arrives) == "stale"
    assert seen_during_pause == [404]  # o cálculo pausado não aparece para ninguém
    assert h.count("serving.current_publication") == 0
    publish(h)
    body = api.get("/api/publications/current", params=query).json()
    assets = api.get(f"/api/publications/{body['publication_id']}/assets",
                     params={"tenant": "t1"}).json()["assets"]  # fmt: skip
    assert {a["asset_id"] for a in assets if a["qualifying"]} == {"east"}  # a revisão nova


def _fetched(doc: dict[str, Any], at: datetime) -> Any:
    from quakeexposure.ingestion.http import Fetched

    return Fetched(FEED_URL, 200, at, json.dumps(doc).encode())


# longe uns dos outros: o raio de um epicentro não alcança os ativos de outro
EPICENTERS = {
    "flores": FLORES,
    "antimeridiano-leste": (179.9, -16.0),
    "antimeridiano-oeste": (-179.9, 16.0),
    "artico": (15.0, 78.5),
}


def test_f14_radius_edge_matches_geographiclib(harness: Harness) -> None:
    h = harness
    rows, expected = [], set()
    for name, (lon, lat) in EPICENTERS.items():
        for azimuth in (0, 45, 90, 135, 180, 225, 270, 315):
            for distance in (99_999, 100_001):
                p = Geodesic.WGS84.Direct(lat, lon, azimuth, distance)
                asset_id = f"{name}-{azimuth}-{distance}"
                rows.append(asset("t1", asset_id, round(p["lon2"], 9), round(p["lat2"], 9)))
                back = Geodesic.WGS84.Inverse(lat, lon, rows[-1].lat, rows[-1].lon)["s12"]
                if back <= 100_000:
                    expected.add(asset_id)
    load(h.conn, rows)
    names = list(EPICENTERS)
    feed(h, *(feature(f"us{n}", updated=1_000 + n, lon=lon, lat=lat)
              for n, (lon, lat) in enumerate(EPICENTERS.values())))  # fmt: skip
    publish(h)
    by_event: dict[str, set[str]] = {name: set() for name in names}
    for event_id, asset_id in h.conn.execute(
        """SELECT e.preferred_id, f.asset_id FROM serving.current_publication p
           JOIN quake.earthquake e USING (stream_scope, earthquake_uid)
           JOIN exposure.fact f USING (calculation_id)
           WHERE p.mode = 'RADIUS_TRIAGE' AND f.qualifying"""
    ):
        by_event[names[int(event_id[2:])]].add(asset_id)
    # o PostGIS classifica exatamente os ativos que o geographiclib põe dentro do raio
    for name in names:
        assert by_event[name] == {a for a in expected if a.startswith(name + "-")}, name
    assert len(expected) == len(EPICENTERS) * 8
    assert all(a.endswith("-99999") for a in expected)


def test_f26_api_returns_exact_money_per_currency(harness: Harness, api: TestClient) -> None:
    h = harness
    big = 2**53 + 1
    load(h.conn, [
        asset("t1", "b1", *FLORES, value=big),
        asset("t1", "b2", FLORES[0] + 0.01, FLORES[1], value=big),
        asset("t1", "u1", FLORES[0], FLORES[1] + 0.01, value=2**62, currency=Currency.USD),
    ])  # fmt: skip
    feed(h, feature("us1", updated=1_000, lon=FLORES[0], lat=FLORES[1]))
    publish(h)
    uid = h.one("SELECT earthquake_uid FROM quake.earthquake")
    query = {"tenant": "t1", "earthquake_uid": str(uid), "mode": "RADIUS_TRIAGE"}
    body = api.get("/api/publications/current", params=query).json()
    assert body["result"]["qualifying_value_minor"] == {"BRL": str(2 * big), "USD": str(2**62)}
    assert RESULT.is_valid(body["result"])
    assets = api.get(f"/api/publications/{body['publication_id']}/assets", params={"tenant": "t1"})
    values = {a["asset_id"]: a["insured_value_minor"] for a in assets.json()["assets"]}
    assert values == {"b1": str(big), "b2": str(big), "u1": str(2**62)}


def test_api_is_coherent_and_never_shows_the_subject_token(
    harness: Harness, api: TestClient
) -> None:
    h = harness
    load(h.conn, generate(assets=200, seed=3))
    replay(h.conn, h.blobs, h.clock())
    publish(h)
    tokens = {
        str(r[0]) for r in h.conn.execute("SELECT subject_token FROM portfolio.asset_revision")
    }
    texts = [api.get("/api/tenants").text, api.get("/api/status").text]
    events = api.get("/api/events", params={"scope": "REPLAY"}).json()
    texts.append(json.dumps(events))
    for event in events[:5]:
        texts.append(api.get(f"/api/events/{event['earthquake_uid']}/revisions",
                             params={"scope": "REPLAY"}).text)  # fmt: skip
        for tenant in ("operadora-b", "seguradora-a"):
            for mode in ("RADIUS_TRIAGE", "SHAKEMAP_MMI"):
                params = {"tenant": tenant, "earthquake_uid": event["earthquake_uid"],
                          "mode": mode, "scope": "REPLAY"}  # fmt: skip
                r = api.get("/api/publications/current", params=params)
                if r.status_code != 200:
                    continue
                body = r.json()
                assert RESULT.is_valid(body["result"]), body["result"]
                pid = body["publication_id"]
                facts = api.get(f"/api/publications/{pid}/assets", params={"tenant": tenant}).json()
                texts += [
                    r.text,
                    json.dumps(facts),
                    api.get(f"/api/publications/{pid}/diff", params={"tenant": tenant}).text,
                ]
                # o resumo e os ativos vêm da mesma publicação e batem
                totals = body["result"]["qualifying_value_minor"] or {}
                for currency, total in totals.items():
                    summed = sum(int(a["insured_value_minor"]) for a in facts["assets"]
                                 if a["qualifying"] and a["currency"] == currency)  # fmt: skip
                    assert str(summed) == total
                # tenant errado não vê a publicação
                other = "seguradora-a" if tenant == "operadora-b" else "operadora-b"
                assert api.get(f"/api/publications/{pid}/assets",
                               params={"tenant": other}).status_code == 404  # fmt: skip
    joined = "\n".join(texts)
    assert "subject_token" not in joined
    assert not any(token in joined for token in tokens)


def test_as_of_uses_where_the_asset_was_at_the_event_time(harness: Harness) -> None:
    h = harness
    event_time = datetime(2026, 10, 6, 16, 48, 22, 347000, tzinfo=UTC)  # o do builder
    moved_at = event_time + timedelta(days=1)
    load(h.conn, [
        asset("t1", "a1", *FLORES, valid_to=moved_at),
        asset("t1", "a1", 0.0, 0.0, asset_interval_id="a1#2", valid_from=moved_at),
    ])  # fmt: skip
    feed(h, feature("us1", updated=1_000, lon=FLORES[0], lat=FLORES[1]))
    publish(h)
    assert h.one("SELECT qualifying_count FROM exposure.calculation") == 1


def test_explain_names_the_cause(harness: Harness) -> None:
    h = harness
    load(h.conn, [asset("t1", "a1", *FLORES)])
    feed(h, feature("us1", updated=1_000, lon=FLORES[0], lat=FLORES[1]))
    publish(h)
    feed(h, feature("us1", updated=2_000, lon=FLORES[0] + 2.0, lat=FLORES[1]))
    publish(h)
    uid = h.one("SELECT earthquake_uid FROM quake.earthquake")
    now, before = publications(h.conn, "t1", uid, "LIVE", "RADIUS_TRIAGE")
    assert now is not None and before is not None
    d = diff(h.conn, before, now)
    assert d["causes"] == ["revisão do evento"]
    assert [a["asset_id"] for a in d["left"]] == ["a1"] and d["entered"] == []


def test_retracted_event_publishes_retracted_with_null_counts(harness: Harness) -> None:
    h = harness
    load(h.conn, [asset("t1", "a1", *FLORES)])
    feed(h, feature("us1", updated=1_000, lon=FLORES[0], lat=FLORES[1]))
    publish(h)
    feed(h, feature("us1", updated=2_000, lon=FLORES[0], lat=FLORES[1], status="deleted"))
    publish(h)
    row = h.conn.execute(
        """SELECT c.status, c.eligible_count FROM serving.current_publication p
           JOIN exposure.calculation c USING (calculation_id) WHERE p.mode = 'RADIUS_TRIAGE'"""
    ).fetchone()
    assert row == ("RETRACTED", None)


def test_api_role_cannot_read_the_raw_portfolio(database: Database) -> None:
    import psycopg

    with database.connect("qe_api_read") as api_conn:
        assert api_conn.execute("SELECT count(*) FROM portfolio.tenant").fetchone() == (0,)
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            api_conn.execute("SELECT subject_token FROM portfolio.asset_revision")
