"""Backfill usa a mesma projeção e o mesmo CAS do coletor contínuo."""

from datetime import date

import httpx
import pytest
from builders import collection, feature
from fastapi.testclient import TestClient

from quakeexposure.api import create_app
from quakeexposure.exposure import jobs, worker
from quakeexposure.ingestion.collector import Collector
from quakeexposure.ingestion.http import FetchError
from quakeexposure.portfolio.store import load

from .conftest import Database, Harness, ok
from .test_collector import incremental
from .test_exposure import asset, feed, publish


def test_f12_backfill_revision_commits_while_calculation_is_paused(
    harness: Harness, database: Database
) -> None:
    h = harness
    load(h.conn, [asset("t1", "west", 0, 0), asset("t1", "east", 2, 0)])
    feed(h, feature("us-backfill", updated=1_000, lon=0, lat=0))
    jobs.mark(h.conn, h.clock())
    job = worker.claim(h.conn, h.clock())
    assert job is not None
    h.usgs.on(incremental, ok(collection(feature("us-backfill", updated=2_000, lon=2, lat=0))))

    def backfill_commits() -> None:
        with database.connect() as other:
            collector = Collector(other, h.collector.client, h.blobs, h.clock)
            assert collector.backfill(date(2026, 10, 6), date(2026, 10, 6)) == 1

    assert worker.run(h.conn, h.blobs, job, h.clock, before_commit=backfill_commits) == "stale"
    assert h.count("exposure.calculation") == h.count("exposure.fact") == 0
    assert h.count("serving.current_publication") == 0
    assert h.count("control.backfill_window") == 1
    assert publish(h)["failed"] == 0
    app = create_app(connector=lambda: database.connect("qe_api_read"), static_dir=None)
    with TestClient(app) as api:
        body = api.get(
            "/api/publications/current",
            params={
                "tenant": "t1",
                "earthquake_uid": str(job.earthquake_uid),
                "mode": job.mode,
            },
        ).json()
        assets = api.get(
            f"/api/publications/{body['publication_id']}/assets", params={"tenant": "t1"}
        ).json()["assets"]
    assert {row["asset_id"] for row in assets if row["qualifying"]} == {"east"}


def test_f27_failed_second_half_does_not_complete_backfill_day(harness: Harness) -> None:
    h = harness

    def whole(r: httpx.Request) -> bool:
        return (
            incremental(r)
            and r.url.params["starttime"].endswith("00:00:00")
            and r.url.params["endtime"].startswith("2026-09-02")
        )

    second = lambda r: incremental(r) and r.url.params["starttime"].endswith("12:00:00")  # noqa: E731
    h.usgs.on(incremental, ok(collection(feature("us-half-one", updated=1_000))))
    h.usgs.on(whole, (400, b"exceeds limit", {}))
    h.usgs.on(second, (503, b"unavailable", {}))
    with pytest.raises(FetchError):
        h.collector.backfill(date(2026, 9, 1), date(2026, 9, 1))
    assert h.count("control.backfill_window") == 0
    assert h.count("quake.event_revision") == 1
    h.usgs.on(second, ok(collection(feature("us-half-two", updated=2_000))))
    assert h.collector.backfill(date(2026, 9, 1), date(2026, 9, 1)) == 1
    assert h.count("control.backfill_window") == 1
    assert h.count("quake.event_revision") == 2
    before = len(h.usgs.requests)
    assert h.collector.backfill(date(2026, 9, 1), date(2026, 9, 1)) == 0
    assert len(h.usgs.requests) == before
