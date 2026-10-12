"""Consulta por área, comparação e operação com PostGIS e o papel somente leitura."""

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import pytest
from builders import feature
from fastapi.testclient import TestClient

from quakeexposure.api import create_app
from quakeexposure.domain.money import Currency
from quakeexposure.portfolio.store import load
from quakeexposure.privacy.erasure import block_platform
from quakeexposure.privacy.ledger import LedgerEntry

from .conftest import Database, Harness
from .test_exposure import asset, feed, publish


@pytest.fixture
def api(database: Database) -> Iterator[TestClient]:
    with TestClient(create_app(lambda: database.connect("qe_api_read"), static_dir=None)) as client:
        yield client


def publication(h: Harness, tenant: str = "t1") -> str:
    value = h.one(
        """SELECT calculation_id FROM serving.current_publication
           WHERE tenant_id = %s AND mode = 'RADIUS_TRIAGE'""",
        tenant,
    )
    assert isinstance(value, str)
    return value


def region_params(**overrides: Any) -> dict[str, Any]:
    return {"tenant": "t1", "west": -180, "south": -90, "east": 180, "north": 90,
            "zoom": 10, **overrides}  # fmt: skip


def test_region_crosses_antimeridian_and_preserves_exact_currency_totals(
    harness: Harness, api: TestClient
) -> None:
    h = harness
    large = 2**53 + 123
    load(h.conn, [
        asset("t1", "west", 179.5, 0, value=large),
        asset("t1", "east", -179.5, 0, value=17, currency=Currency.USD),
        asset("t1", "middle", 0, 0),
        asset("t1", "north", 179.8, 10),
        asset("t2", "other", 179.5, 0),
    ])  # fmt: skip
    feed(h, feature("us-region", updated=1000, lon=180, lat=0))
    assert publish(h)["failed"] == 0
    pid = publication(h)
    params = region_params(west=170, east=-170, south=-1, north=1)
    response = api.get(f"/api/publications/{pid}/region", params=params)
    assert response.status_code == 200
    body = response.json()
    assert body["representation"] == "points"
    assert [row["asset_id"] for row in body["assets"]] == ["east", "west"]
    assert body["total_count"] == body["eligible_count"] == body["qualifying_count"] == 2
    assert (
        body["insured_value_minor"]
        == body["qualifying_value_minor"]
        == {"BRL": str(large), "USD": "17"}
    )
    params.update(zoom=0, limit=1)
    cluster_response = api.get(f"/api/publications/{pid}/region", params=params)
    assert cluster_response.status_code == 200
    clustered = cluster_response.json()
    assert clustered["assets"] == [] and len(clustered["clusters"]) == 1
    cluster = clustered["clusters"][0]
    assert cluster["count"] == 2 and abs(cluster["lon"]) == 180 and cluster["lat"] == 0
    assert cluster["insured_value_minor"] == body["insured_value_minor"]
    assert cluster["qualifying_value_minor"] == body["qualifying_value_minor"]
    assert "other" not in response.text
    assert (
        api.get(f"/api/publications/{pid}/region", params=region_params(tenant="t2")).status_code
        == 404
    )


@pytest.mark.parametrize(
    ("west", "east"),
    [(-180, -160), (160, 180), (-180, 180), (-180, -180), (180, 180), (180, -180)],
)
def test_region_treats_both_dateline_longitudes_as_one_inclusive_boundary(
    harness: Harness, api: TestClient, west: float, east: float
) -> None:
    h = harness
    load(h.conn, [
        asset("t1", "negative", -180, 0, value=12),
        asset("t1", "positive", 180, 0, value=34),
        asset("t1", "outside", 0, 10, value=999),
    ])  # fmt: skip
    feed(h, feature("us-dateline-boundary", updated=1000, lon=180, lat=0))
    publish(h)
    params = region_params(west=west, east=east, south=-1, north=1)
    pid = publication(h)
    response = api.get(f"/api/publications/{pid}/region", params=params)
    assert response.status_code == 200
    body = response.json()
    assert [row["asset_id"] for row in body["assets"]] == ["negative", "positive"]
    assert body["total_count"] == body["eligible_count"] == body["qualifying_count"] == 2
    assert body["insured_value_minor"] == body["qualifying_value_minor"] == {"BRL": "46"}
    # Mais de uma célula disponível: as duas grafias da mesma longitude devem
    # cair juntas, inclusive no bbox mundial e nas áreas de largura zero.
    params.update(zoom=6, limit=9)
    aggregated = api.get(f"/api/publications/{pid}/region", params=params)
    assert aggregated.status_code == 200
    clusters = aggregated.json()["clusters"]
    assert len(clusters) == 1 and clusters[0]["count"] == 2
    assert abs(clusters[0]["lon"]) == 180
    assert clusters[0]["insured_value_minor"] == {"BRL": "46"}


@pytest.mark.parametrize(
    "overrides",
    [
        {"west": 181},
        {"east": float("nan")},
        {"south": 20, "north": 19},
        {"zoom": 23},
        {"limit": 2001},
        {"limit": 0},
    ],
)
def test_region_rejects_invalid_bounds_and_response_limits(
    api: TestClient, overrides: dict[str, Any]
) -> None:
    response = api.get("/api/publications/unknown/region", params=region_params(**overrides))
    assert response.status_code == 422


def test_region_keeps_all_points_in_bounded_clusters_at_high_zoom(
    harness: Harness, api: TestClient
) -> None:
    h = harness
    load(h.conn, [asset("t1", f"a{i:03}", -170 + i * 3, -30 + i, value=i) for i in range(60)])
    feed(h, feature("us-many", updated=1000, lon=0, lat=0))
    publish(h)
    response = api.get(f"/api/publications/{publication(h)}/region", params=region_params(limit=7))
    body = response.json()
    assert response.status_code == 200 and body["representation"] == "clusters"
    assert 1 <= len(body["clusters"]) <= 7
    assert sum(cluster["count"] for cluster in body["clusters"]) == body["total_count"] == 60
    assert sum(int(cluster["insured_value_minor"]["BRL"]) for cluster in body["clusters"]) == 1770


def test_comparison_reports_positions_classification_and_paginated_changes(
    harness: Harness, api: TestClient
) -> None:
    h = harness
    large = 2**53 + 1
    rows = [asset("t1", "a1", 0, 0, value=large), asset("t1", "a2", 2, 0)]
    load(h.conn, rows)
    feed(h, feature("us-compare", updated=1000, lon=0, lat=0))
    publish(h)
    before = publication(h)
    first = api.get(f"/api/publications/{before}/comparison", params={"tenant": "t1"}).json()
    assert (
        first["previous_available"] is False and first["unavailable_reason"] == "first_publication"
    )
    assert first["before"] is None and first["changes"] == []
    # Correção retroativa da localização conhecida pela carteira.
    h.conn.execute(
        """INSERT INTO portfolio.asset_revision
               SELECT tenant_id, asset_interval_id, 2, asset_id, subject_token, 2, source_epoch,
                      valid_from, valid_to, 2, lat, insured_value_minor, currency, is_deleted,
                      updated_at FROM portfolio.asset_revision WHERE asset_id = 'a1'"""
    )
    h.conn.execute("UPDATE portfolio.tenant SET portfolio_epoch = 2 WHERE tenant_id = 't1'")
    feed(h, feature("us-compare", updated=2000, lon=2, lat=0))
    publish(h)
    after = publication(h)
    response = api.get(f"/api/publications/{after}/comparison", params={"tenant": "t1", "limit": 1})
    assert response.status_code == 200
    body = response.json()
    assert body["previous_publication_id"] == before and body["previous_available"] is True
    assert body["causes"] == ["revisão do evento", "epoch da carteira"]
    assert body["total_changes"] == 2 and len(body["changes"]) == 1
    moved = body["changes"][0]
    assert moved["asset_id"] == "a1" and moved["change"] == "changed"
    assert moved["before"]["lon"] == 0 and moved["after"]["lon"] == 2
    assert moved["before"]["insured_value_minor"] == str(large)
    assert body["before"]["result"]["input_vector"]["portfolio_epoch"] == 1
    assert body["after"]["result"]["input_vector"]["portfolio_epoch"] == 2
    assert body["before"]["result"]["qualifying_value_minor"] == {"BRL": str(large), "USD": "0"}
    second = api.get(
        f"/api/publications/{after}/comparison", params={"tenant": "t1", "limit": 1, "offset": 1}
    ).json()
    assert second["total_changes"] == 2 and second["changes"][0]["asset_id"] == "a2"
    assert second["changes"][0]["before"]["qualifying"] is False
    assert second["changes"][0]["after"]["qualifying"] is True
    assert (
        api.get(f"/api/publications/{after}/comparison", params={"tenant": "other"}).status_code
        == 404
    )
    assert api.get(f"/api/publications/{after}/comparison").status_code == 422


def test_new_comparison_never_reads_old_positions_after_privacy_epoch_changes(
    harness: Harness, api: TestClient
) -> None:
    h = harness
    erased = asset("t1", "erased-private", 0, 0)
    load(h.conn, [erased, asset("t1", "safe", 0.1, 0)])
    feed(h, feature("us-private", updated=1000, lon=0, lat=0))
    publish(h)
    old = publication(h)
    entry = LedgerEntry(
        str(uuid4()), str(erased.subject_token), "t1", "erased-private", 1, h.clock().isoformat()
    )
    block_platform(h.conn, entry)
    assert publish(h)["failed"] == 0
    newest = publication(h)
    assert newest != old
    response = api.get(f"/api/publications/{newest}/comparison", params={"tenant": "t1"})
    assert response.status_code == 200
    body = response.json()
    assert body["previous_publication_id"] is None and body["before"] is None
    assert body["previous_available"] is False and body["unavailable_reason"] == "privacy_epoch"
    assert body["causes"] == ["exclusão de titular"] and body["changes"] == []
    assert "erased-private" not in response.text
    assert (
        api.get(f"/api/publications/{old}/comparison", params={"tenant": "t1"}).status_code == 409
    )
    assert api.get(f"/api/publications/{old}/region", params=region_params()).status_code == 409


def test_comparison_marks_added_and_removed_assets_and_keeps_total_after_last_page(
    harness: Harness, api: TestClient
) -> None:
    h = harness
    load(h.conn, [asset("t1", "removed", 0, 0)])
    feed(h, feature("us-list-change", updated=1000, lon=0, lat=0))
    publish(h)
    h.conn.execute(
        """INSERT INTO portfolio.asset_revision
               SELECT tenant_id, asset_interval_id, 2, asset_id, subject_token, 2, source_epoch,
                      valid_from, valid_to, lon, lat, insured_value_minor, currency, true,
                      updated_at FROM portfolio.asset_revision WHERE asset_id = 'removed'"""
    )
    h.conn.execute(
        """INSERT INTO portfolio.asset_revision
               SELECT tenant_id, 'added#1', 1, 'added', subject_token, 2, source_epoch,
                      valid_from, valid_to, lon, lat, insured_value_minor, currency, false,
                      updated_at FROM portfolio.asset_revision
               WHERE asset_id = 'removed' AND row_version = 1"""
    )
    h.conn.execute("UPDATE portfolio.tenant SET portfolio_epoch = 2 WHERE tenant_id = 't1'")
    publish(h)
    pid = publication(h)
    body = api.get(f"/api/publications/{pid}/comparison", params={"tenant": "t1"}).json()
    assert body["total_changes"] == 2
    added, removed = body["changes"]
    assert added["asset_id"] == "added" and added["change"] == "added"
    assert added["before"] is None and added["after"]["lon"] == 0
    assert removed["asset_id"] == "removed" and removed["change"] == "removed"
    assert removed["before"]["lon"] == 0 and removed["after"] is None
    beyond = api.get(
        f"/api/publications/{pid}/comparison", params={"tenant": "t1", "offset": 99, "limit": 1}
    ).json()
    assert beyond["total_changes"] == 2 and beyond["changes"] == []


def test_operations_filters_jobs_and_publications_without_private_job_errors(
    harness: Harness, api: TestClient
) -> None:
    h = harness
    load(h.conn, [asset("t1", "a1", 0, 0), asset("t2", "foreign", 1, 0)])
    feed(h, feature("us-ops", updated=1000, lon=0, lat=0))
    publish(h)
    now = datetime.now(UTC)
    h.conn.execute(
        """UPDATE control.exposure_job SET desired_generation = desired_generation + 1,
               attempts = 5, lease_until = %s, last_error = 'private job details'
           WHERE tenant_id = 't1'""",
        (now - timedelta(days=1),),
    )
    h.conn.execute(
        """UPDATE control.exposure_job SET desired_generation = desired_generation + 1,
               attempts = 0 WHERE tenant_id = 't2'"""
    )
    response = api.get("/api/operations", params={"tenant": "t1"})
    assert response.status_code == 200
    body = response.json()
    assert body["tenant_id"] == "t1"
    assert body["epochs"] == {"portfolio": 1, "source": 1, "privacy": 0}
    assert body["jobs"] == {"pending": 0, "leased": 0, "failed": 1, "outdated": 1}
    assert len(body["publications"]) == 1 and body["last_publication_at"] is not None
    assert body["publications"][0]["calculation_duration_s"] is None
    assert body["publications"][0]["publication_delay_s"] == 0
    assert body["cdc"]["connector_state"] == "UNKNOWN"
    assert "private job details" not in response.text and "foreign" not in response.text
    assert api.get("/api/operations", params={"tenant": "missing"}).status_code == 404
    assert api.get("/api/operations").status_code == 422
