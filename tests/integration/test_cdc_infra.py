"""BEGIN/END reais e parada do Connect; não usa a rede do USGS."""

import uuid
from datetime import UTC, datetime
from typing import Any

import psycopg
import pytest

from quakeexposure.cdc import apply_pending, stage
from quakeexposure.cdc.monitor import refresh
from quakeexposure.portfolio.source import execute_command
from quakeexposure.status import report

from .cdc_support import CdcStack


def _row() -> dict[str, Any]:
    return {
        "asset_interval_id": "asset#1",
        "asset_id": "asset",
        "subject_token": str(uuid.uuid4()),
        "contact": "asset@example.invalid",
        "valid_from": "2020-01-01T00:00:00Z",
        "valid_to": None,
        "lon": 120.0,
        "lat": -8.0,
        "insured_value_minor": 2**53 + 1,
        "currency": "USD",
        "is_deleted": False,
    }


def test_debezium_metadata_ignores_transactions_outside_publication(cdc_stack: CdcStack) -> None:
    tenant = f"smoke-{uuid.uuid4().hex}"
    outside_xid = ""

    def commands() -> None:
        nonlocal outside_xid
        with cdc_stack.source("qe_source_owner") as source, source.transaction():
            row = source.execute("SELECT pg_current_xact_id()::text").fetchone()
            assert row is not None
            outside_xid = row[0]
            source.execute(
                "INSERT INTO ops.command_dedup VALUES (%s, 'smoke', '{}'::jsonb)",
                (uuid.uuid4(),),
            )
        with cdc_stack.source() as source:
            execute_command(source, tenant, [_row()])

    records = cdc_stack.capture(commands)
    metadata = [p for t, p in records if t == "portfolio.transaction"]
    assert [p["status"] for p in metadata] == ["BEGIN", "END"]
    assert all(p["id"].split(":")[0] != outside_xid for p in metadata)
    end = metadata[-1]
    assert end["event_count"] == 2
    assert {c["data_collection"]: c["event_count"] for c in end["data_collections"]} == {
        "public.asset_interval": 1,
        "public.portfolio_clock": 1,
    }
    data = [p for t, p in records if t != "portfolio.transaction"]
    assert sorted(p["transaction"]["total_order"] for p in data) == [1, 2]
    asset = next(p["after"] for p in data if p["source"]["table"] == "asset_interval")
    assert asset["insured_value_minor"] == 2**53 + 1
    assert datetime.fromisoformat(asset["valid_from"]) == datetime(2020, 1, 1, tzinfo=UTC)


def test_f23_stopped_connect_reports_unavailable_without_partial_apply(
    cdc_stack: CdcStack, conn: psycopg.Connection[Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    tenant = f"f23-{uuid.uuid4().hex}"

    def command() -> None:
        with cdc_stack.source() as source:
            execute_command(source, tenant, [_row()])

    records = cdc_stack.capture(command)
    withheld = next(r for r in records if r[0] == "portfolio.public.asset_interval")
    for topic, payload in records:
        if (topic, payload) != withheld:
            stage(conn, topic, payload)
    assert apply_pending(conn) == 0
    monkeypatch.setenv("QE_CONNECT_URL", cdc_stack.connect_url)
    monkeypatch.setenv("QE_SOURCE_DB_HOST", "127.0.0.1")
    monkeypatch.setenv("QE_SOURCE_DB_PORT", str(cdc_stack.source_port))
    monkeypatch.setenv("QE_SOURCE_DB_USER", "qe_source_writer")
    monkeypatch.setenv(
        "QE_SOURCE_DB_PASSWORD_FILE", str(cdc_stack.database.secrets_dir / "qe_source_writer")
    )
    refresh(conn)
    assert report(conn, datetime.now(UTC))["cdc"]["connector_state"] == "RUNNING"
    try:
        cdc_stack.compose("stop", "connect")
        refresh(conn)
        cdc = report(conn, datetime.now(UTC))["cdc"]
        assert cdc["connector_state"] == "UNAVAILABLE"
        assert cdc["pending_transactions"] == 1
        assert cdc["stale"]
        assert apply_pending(conn) == 0
        assert (
            conn.execute(
                "SELECT portfolio_epoch FROM portfolio.tenant WHERE tenant_id=%s", (tenant,)
            ).fetchone()
            is None
        )
    finally:
        cdc_stack.compose("start", "connect")
        cdc_stack.wait_running()
    stage(conn, *withheld)
    assert apply_pending(conn) == 1
    refresh(conn)
    cdc = report(conn, datetime.now(UTC))["cdc"]
    assert cdc["connector_state"] == "RUNNING"
    assert cdc["pending_transactions"] == 0
