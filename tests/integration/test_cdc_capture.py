"""A barreira de captura separa o WAL atrasado das transações pedidas pelo teste."""

import time
from threading import Event
from uuid import uuid4

import httpx
import pytest

from quakeexposure.ingestion.store import Conn
from quakeexposure.portfolio.source import execute_command

from .cdc_support import CdcStack
from .test_cdc import END, asset_row
from .test_cdc_real import apply_batch


def test_capture_excludes_old_transactions_delayed_in_connect(
    cdc_stack: CdcStack, conn: Conn
) -> None:
    endpoint = f"{cdc_stack.connect_url}/connectors/portfolio"
    old_tenant = f"backlog-{uuid4().hex}"
    wanted = {f"action-{uuid4().hex}" for _ in range(2)}
    try:
        httpx.put(f"{endpoint}/pause", timeout=10).raise_for_status()
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            response = httpx.get(f"{endpoint}/status", timeout=5)
            response.raise_for_status()
            status = response.json()
            if status["tasks"] and all(t["state"] == "PAUSED" for t in status["tasks"]):
                break
            Event().wait(0.05)
        else:
            pytest.fail("Connect não confirmou a pausa")
        # O comando anterior está no WAL, mas os offsets Kafka ainda não o conhecem.
        with cdc_stack.source() as source:
            execute_command(source, old_tenant, [asset_row()])

            def action() -> None:
                for tenant in sorted(wanted):
                    execute_command(source, tenant, [asset_row()])
                httpx.put(f"{endpoint}/resume", timeout=10).raise_for_status()
                cdc_stack.wait_running()

            records = cdc_stack.capture(action, transactions=2)
        data = [message for topic, message in records if topic != END]
        assert {message["after"]["tenant_id"] for message in data} == wanted
        assert len(data) == 4
        assert sum(topic == END and message["status"] == "END" for topic, message in records) == 2
        assert apply_batch(conn, records) == 2
        assert {row[0] for row in conn.execute("SELECT tenant_id FROM portfolio.tenant")} == wanted
    finally:
        httpx.put(f"{endpoint}/resume", timeout=10).raise_for_status()
        cdc_stack.wait_running()
