import hashlib
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import psycopg
import pytest
from builders import collection

from quakeexposure.ingestion.collector import FEED_URL
from quakeexposure.ingestion.projection import project_pending
from quakeexposure.ingestion.replay import capture_fault, replay
from quakeexposure.migrate import PLATFORM, MigrationError, migrate

from .conftest import Database, Harness, ok

GRIDS_IN_GIT = {
    "urn:usgs-product:us:shakemap:us6000u0hy:1791391956496",
    "urn:usgs-product:us:shakemap:us7000tiur:1790041565495",
}


def test_migrations_are_idempotent_and_edits_are_refused(
    database: Database, tmp_path: Path
) -> None:
    with database.connect("qe_owner") as owner:
        assert migrate(owner) == []
        edited = tmp_path / "platform"
        edited.mkdir()
        sql = (PLATFORM / "0001_ingestion.sql").read_text("utf-8")
        (edited / "0001_ingestion.sql").write_text(sql + "\n-- editada\n", "utf-8")
        with pytest.raises(MigrationError, match="mudou"):
            migrate(owner, edited)


def test_roles(database: Database, conn: psycopg.Connection[Any]) -> None:
    assert conn.execute("SHOW statement_timeout").fetchone() == ("1min",)
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        conn.execute("CREATE TABLE quake.x (id int)")
    with database.connect("qe_api_read") as api:
        assert api.execute("SELECT count(*) FROM quake.earthquake").fetchone() == (0,)
        with pytest.raises(psycopg.errors.ReadOnlySqlTransaction):
            api.execute("INSERT INTO control.watchlist (provider_event_id) VALUES ('x')")


def test_f01_replay_twice_adds_nothing(harness: Harness) -> None:
    h = harness
    first = replay(h.conn, h.blobs, h.clock())
    counts = (h.count("control.capture"), h.count("quake.event_revision"))
    assert first.stored >= 7 and counts[1] > 240
    second = replay(h.conn, h.blobs, h.clock())
    assert (second.stored, second.already) == (0, first.stored)
    assert (h.count("control.capture"), h.count("quake.event_revision")) == counts

    assert h.count("quake.earthquake", "stream_scope <> 'REPLAY'") == 0
    grids = h.conn.execute(
        "SELECT product_revision_id FROM quake.shakemap_grid WHERE status = 'OK'"
    ).fetchall()
    assert {g for (g,) in grids} >= GRIDS_IN_GIT
    chosen = h.one(
        """SELECT m.product_revision_id FROM quake.event_map m
           JOIN quake.alias a USING (stream_scope, earthquake_uid)
           WHERE a.provider_id = 'aka2026tuxgky'"""
    )
    assert chosen == "urn:usgs-product:ak:shakemap:a2026tuxgky:1791312073169"
    # REPLAY guarda o horário original da captura, não o do replay
    assert h.one("SELECT max(fetched_at) FROM control.capture").isoformat().startswith("2026-10-08")


def test_live_replay_and_fault_never_mix(
    harness: Harness, usgs_fixture: Callable[[str], bytes]
) -> None:
    h = harness
    replay(h.conn, h.blobs, h.clock())
    original = usgs_fixture("feed_all_day")
    mutated = json.loads(original)["features"][0]
    event_id = mutated["id"]
    mutated["properties"]["mag"] = 7.9
    mutated["properties"]["updated"] += 1
    capture_fault(
        h.conn, h.blobs, parent_sha256=hashlib.sha256(original).hexdigest(), kind="feed",
        url=FEED_URL, fetched_at=h.clock(), body=json.dumps(collection(mutated)).encode(),
    )  # fmt: skip
    project_pending(h.conn, h.blobs, h.clock())
    # o LIVE lê o mesmo feed que já está no REPLAY: tem que gravar tudo de novo no seu escopo
    h.usgs.on(FEED_URL, ok(original))
    h.usgs.on(lambda r: r.url.path == "/fdsnws/event/1/query", ok(collection()))
    h.collector.tick()
    assert h.count("quake.event_revision", "stream_scope = 'LIVE'") == 240

    rows = h.conn.execute(
        "SELECT stream_scope, earthquake_uid FROM quake.alias WHERE provider_id = %s", (event_id,)
    ).fetchall()
    assert sorted(scope for scope, _ in rows) == ["FAULT", "LIVE", "REPLAY"]
    assert len({uid for _, uid in rows}) == 3  # cada escopo tem o seu evento
    assert h.one("SELECT mag FROM quake.event_revision WHERE stream_scope = 'FAULT'") == 7.9
    assert h.one("SELECT parent_sha256 FROM control.capture WHERE stream_scope = 'FAULT'") == (
        hashlib.sha256(original).hexdigest()
    )
