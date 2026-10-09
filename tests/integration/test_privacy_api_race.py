"""Intercalação entre o guard de privacidade e a leitura dos fatos da API."""

import time
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from uuid import UUID, uuid4

import pytest
from builders import feature
from fastapi.testclient import TestClient

import quakeexposure.api as api_module
from quakeexposure.ingestion.store import Conn
from quakeexposure.portfolio.store import load
from quakeexposure.privacy.erasure import block_platform
from quakeexposure.privacy.ledger import LedgerEntry

from .conftest import Database, Harness
from .test_exposure import asset, feed, publish


def test_block_waits_for_api_read_then_next_request_hides_old_assets(
    harness: Harness, database: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    h = harness
    row = asset("race", "private-asset", 0, 0)
    load(h.conn, [row])
    feed(h, feature("us-privacy-race", updated=1_000, lon=0, lat=0))
    publish(h)
    publication = h.one("SELECT calculation_id FROM serving.current_publication")
    assert publication is not None
    entry = LedgerEntry(
        str(uuid4()), str(row.subject_token), "race", "private-asset", 1, h.clock().isoformat()
    )
    original = api_module._own
    guarded = Event()
    release_reader = Event()
    blocker_started = Event()
    blocker_pid: list[int] = []

    def race(conn: Conn, publication_id: str, tenant: str) -> tuple[str, UUID, str, str]:
        owned = original(conn, publication_id, tenant)
        if not guarded.is_set():
            guarded.set()
            assert release_reader.wait(15)
        return owned

    def block() -> None:
        with database.connect() as other:
            blocker_pid.append(other.info.backend_pid)
            blocker_started.set()
            block_platform(other, entry)

    monkeypatch.setattr(api_module, "_own", race)
    app = api_module.create_app(connector=lambda: database.connect("qe_api_read"), static_dir=None)
    with TestClient(app) as api, ThreadPoolExecutor(max_workers=2) as pool:
        reader = pool.submit(
            api.get, f"/api/publications/{publication}/assets", params={"tenant": "race"}
        )
        assert guarded.wait(15)
        blocker = pool.submit(block)
        assert blocker_started.wait(15)
        try:
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                waiting = h.conn.execute(
                    "SELECT count(*) FROM pg_locks WHERE pid = %s AND NOT granted",
                    (blocker_pid[0],),
                ).fetchone()
                if waiting == (1,):
                    break
                assert not blocker.done(), "bloqueio ultrapassou uma leitura da API em andamento"
                Event().wait(0.01)
            else:
                pytest.fail("exclusão não chegou ao lock compartilhado com a API")
            assert not blocker.done()
        finally:
            release_reader.set()
        response = reader.result(timeout=15)
        blocker.result(timeout=15)
        assert response.status_code == 200
        assert "private-asset" in response.text  # resposta se serializou antes do bloqueio
        after = api.get(f"/api/publications/{publication}/assets", params={"tenant": "race"})
    assert after.status_code == 409
    assert "private-asset" not in after.text
