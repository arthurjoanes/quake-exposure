"""Exclusão real durante cálculo, retomada após falha e reaplicação do ledger."""

from datetime import timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from builders import feature
from fastapi.testclient import TestClient

from quakeexposure.api import create_app
from quakeexposure.cdc import stage
from quakeexposure.exposure import jobs, worker
from quakeexposure.ingestion.store import Conn
from quakeexposure.portfolio.source import correct, execute_command
from quakeexposure.privacy.erasure import erase, replay_ledger
from quakeexposure.privacy.ledger import Ledger

from .cdc_support import CdcStack
from .conftest import Database, Harness
from .test_cdc import NOW, asset_row
from .test_cdc_real import apply_batch
from .test_exposure import current, feed, publish


def populate(cdc_stack: CdcStack, h: Harness) -> tuple[str, dict[str, Any], list[Any]]:
    h.clock.now = NOW
    tenant = f"erase-{uuid4().hex}"
    victim = asset_row()
    safe = asset_row(asset_id="safe", asset_interval_id="safe#1")
    with cdc_stack.source() as source:
        initial = cdc_stack.capture(lambda: execute_command(source, tenant, [victim, safe]))
    assert apply_batch(h.conn, initial) == 1
    feed(h, feature("us-erase", updated=1000, lon=0, lat=0))
    assert publish(h)["failed"] == 0
    assert h.count("exposure.fact") == 2
    return tenant, victim, initial


def test_f18_erase_during_calculation_and_old_real_cdc_never_restore_subject(
    cdc_stack: CdcStack,
    harness: Harness,
    database: Database,
    tmp_path: Path,
) -> None:
    h = harness
    tenant, victim, initial = populate(cdc_stack, h)
    token = UUID(victim["subject_token"])
    previous_id = current(h)[0][3]
    with cdc_stack.source() as source:
        held = cdc_stack.capture(lambda: correct(source, tenant, "a#1", lon=0.001))
        # A correção antiga já está durável no stage, mas ainda não foi aplicada.
        for topic, message in held:
            stage(h.conn, topic, message, now=h.clock())
        feed(h, feature("us-erase", updated=2000, lon=0.01, lat=0))
        jobs.mark(h.conn, h.clock())
        job = worker.claim(h.conn, h.clock())
        assert job is not None
        outcomes: list[str] = []

        def paused() -> None:
            with database.connect() as eraser:
                erase(eraser, source, token, ledger=Ledger(tmp_path / "ledger.jsonl"), now=h.clock)

        deletion = cdc_stack.capture(
            lambda: outcomes.append(worker.run(h.conn, h.blobs, job, h.clock, before_commit=paused))
        )
        assert outcomes == ["stale"]
        assert source.execute(
            "SELECT count(*) FROM public.asset_interval WHERE subject_token = %s", (token,)
        ).fetchone() == (0,)
    assert h.count("portfolio.asset_revision", "subject_token = %s", token) == 0
    assert h.count("exposure.fact", "asset_id = 'a'") == 0
    assert h.count("exposure.calculation_total") == 0
    assert (
        h.one("SELECT count(*) FROM control.cdc_stage WHERE payload::text LIKE %s", f"%{token}%")
        == 0
    )
    with TestClient(create_app(lambda: database.connect("qe_api_read"), static_dir=None)) as api:
        assert (
            api.get(
                f"/api/publications/{previous_id}/assets", params={"tenant": tenant}
            ).status_code
            == 409
        )
    for topic, message in initial:
        assert stage(h.conn, topic, message, now=h.clock()) is False
    assert h.one("SELECT portfolio_epoch FROM portfolio.tenant WHERE tenant_id = %s", tenant) == 1
    assert apply_batch(h.conn, held) == 1
    assert apply_batch(h.conn, deletion) == 1
    assert h.count("portfolio.asset_revision", "subject_token = %s", token) == 0
    assert h.count("portfolio.asset_interval", "asset_id = 'a'") == 0
    assert publish(h)["failed"] == 0
    assert h.count("exposure.fact", "asset_id = 'a'") == 0
    assert h.one("SELECT privacy_epoch FROM portfolio.tenant WHERE tenant_id = %s", tenant) == 1
    newest = current(h)[0][3]
    with TestClient(create_app(lambda: database.connect("qe_api_read"), static_dir=None)) as api:
        assets = api.get(f"/api/publications/{newest}/assets", params={"tenant": tenant})
        assert assets.status_code == 200
        assert [item["asset_id"] for item in assets.json()["assets"]] == ["safe"]


def test_f18_blocked_phase_hides_old_api_and_worker_excludes_subject_before_purge(
    cdc_stack: CdcStack,
    harness: Harness,
    database: Database,
    tmp_path: Path,
) -> None:
    h = harness
    tenant, victim, _ = populate(cdc_stack, h)
    token = UUID(victim["subject_token"])
    ledger = Ledger(tmp_path / "ledger.jsonl")
    previous_id = current(h)[0][3]
    # Mesmo fora do escopo de 7 dias, os jobs publicados precisam voltar à fila.
    h.clock.advance(days=30)

    def interrupted(step: str) -> None:
        if step == "BLOCKED":
            raise RuntimeError("interrupção depois de BLOCKED")

    with cdc_stack.source() as source:
        with pytest.raises(RuntimeError, match="interrupção"):
            erase(h.conn, source, token, ledger=ledger, now=h.clock, after_step=interrupted)
        assert h.count("portfolio.asset_revision", "subject_token = %s", token) == 1
        assert h.count("control.exposure_job", "desired_generation > published_generation") > 0
        with TestClient(
            create_app(lambda: database.connect("qe_api_read"), static_dir=None)
        ) as api:
            assert (
                api.get(
                    f"/api/publications/{previous_id}/assets", params={"tenant": tenant}
                ).status_code
                == 409
            )
        assert publish(h)["failed"] == 0
        newest = current(h)[0][3]
        assert h.count("exposure.fact", "calculation_id = %s AND asset_id = 'a'", newest) == 0
        receipt = erase(h.conn, source, token, ledger=ledger, now=h.clock)
        assert receipt["status"] == "CLOSED"
        assert erase(h.conn, source, token, ledger=ledger, now=h.clock) == receipt
    assert len(ledger.entries()) == 1
    assert h.one("SELECT privacy_epoch FROM portfolio.tenant WHERE tenant_id = %s", tenant) == 1
    assert h.count("portfolio.asset_revision", "subject_token = %s", token) == 0
    assert h.count("exposure.fact", "asset_id = 'a'") == 0


@pytest.mark.parametrize("crash_at", ["PURGED", "CLOSED"])
def test_erasure_resumes_after_committed_phases_without_extra_epoch(
    cdc_stack: CdcStack,
    harness: Harness,
    tmp_path: Path,
    crash_at: str,
) -> None:
    h = harness
    tenant, victim, _ = populate(cdc_stack, h)
    token = UUID(victim["subject_token"])
    ledger = Ledger(tmp_path / "ledger.jsonl")

    def interrupted(step: str) -> None:
        if step == crash_at:
            raise RuntimeError("interrompido")

    with cdc_stack.source() as source:
        with pytest.raises(RuntimeError, match="interrompido"):
            erase(h.conn, source, token, ledger=ledger, now=h.clock, after_step=interrupted)
        receipt = erase(h.conn, source, token, ledger=ledger, now=h.clock)
        assert receipt["status"] == "CLOSED"
        assert source.execute(
            "SELECT epoch FROM public.portfolio_clock WHERE tenant_id = %s", (tenant,)
        ).fetchone() == (2,)
    assert h.one("SELECT privacy_epoch FROM portfolio.tenant WHERE tenant_id = %s", tenant) == 1
    assert h.count("exposure.fact", "asset_id = 'a'") == 0


def test_ledger_failure_blocks_source_but_never_claims_platform_completed(
    cdc_stack: CdcStack,
    harness: Harness,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    h = harness
    tenant, victim, _ = populate(cdc_stack, h)
    token = UUID(victim["subject_token"])
    ledger = Ledger(tmp_path / "ledger.jsonl")

    def broken_append(*args: Any) -> None:
        raise OSError("disco cheio")

    with cdc_stack.source() as source:
        with monkeypatch.context() as patch:
            patch.setattr(ledger, "append", broken_append)
            with pytest.raises(OSError, match="disco cheio"):
                erase(h.conn, source, token, ledger=ledger, now=h.clock)
        assert h.count("privacy.erasure_request", "subject_token = %s", token) == 0
        assert h.one("SELECT privacy_epoch FROM portfolio.tenant WHERE tenant_id = %s", tenant) == 0
        receipt = erase(h.conn, source, token, ledger=ledger, now=h.clock)
        assert receipt["status"] == "CLOSED"


def test_f19_ledger_reapplication_purges_restored_rows_and_is_idempotent(
    cdc_stack: CdcStack,
    harness: Harness,
    tmp_path: Path,
    database: Database,
) -> None:
    h = harness
    tenant, victim, _ = populate(cdc_stack, h)
    token = UUID(victim["subject_token"])
    ledger = Ledger(tmp_path / "ledger.jsonl")
    with cdc_stack.source() as source:
        erase(h.conn, source, token, ledger=ledger, now=h.clock)
    # Reconstitui a carteira pré-exclusão para verificar a reaplicação sem a origem viva.
    with database.connect("qe_owner") as owner:
        owner.execute("DELETE FROM privacy.erasure_request WHERE subject_token = %s", (token,))
        owner.execute("DELETE FROM privacy.erase_block WHERE subject_token = %s", (token,))
        owner.execute(
            "UPDATE portfolio.tenant SET privacy_epoch = 0 WHERE tenant_id = %s", (tenant,)
        )
    h.conn.execute(
        """INSERT INTO portfolio.asset_revision (tenant_id, asset_interval_id, row_version,
               asset_id, subject_token, known_epoch, source_epoch, valid_from, lon, lat,
               insured_value_minor, currency, is_deleted, updated_at)
           VALUES (%s, 'a#1', 1, 'a', %s, 1, 1, %s, 0, 0, 100, 'BRL', false, %s)""",
        (tenant, token, NOW - timedelta(days=365), NOW),
    )
    result = replay_ledger(h.conn, ledger)
    assert result["subjects"] == 1 and result["revisions"] == 1
    assert replay_ledger(h.conn, ledger) == {"subjects": 1, "revisions": 0, "facts": 0}
    assert h.count("portfolio.asset_revision", "subject_token = %s", token) == 0
    assert h.one("SELECT privacy_epoch FROM portfolio.tenant WHERE tenant_id = %s", tenant) == 1


def test_privacy_tables_are_not_readable_by_api(database: Database, conn: Conn) -> None:
    import psycopg

    with database.connect("qe_api_read") as api:
        for table in ("privacy.erasure_request", "privacy.erase_block", "control.cdc_stage"):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                api.execute(f"SELECT * FROM {table}")  # noqa: S608
