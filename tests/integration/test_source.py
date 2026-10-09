"""Funções de comando da origem real: permissões, serialização e privacidade."""

import os
import secrets
import shutil
import subprocess
import uuid
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import psycopg
import pytest

from quakeexposure.db import ROOT
from quakeexposure.portfolio.source import (
    block_subject,
    correct,
    erase_subject,
    execute_command,
    migrate_source,
    row_payload,
    seed,
)
from quakeexposure.portfolio.synthetic import generate


@dataclass(frozen=True)
class SourceDatabase:
    port: int
    secrets_dir: Path

    def connect(self, user: str = "qe_source_writer") -> psycopg.Connection[Any]:
        return psycopg.connect(
            host="127.0.0.1",
            port=self.port,
            dbname="qe_source",
            user=user,
            password=(self.secrets_dir / user).read_text("utf-8"),
            autocommit=True,
            options="-c TimeZone=UTC",
        )


@pytest.fixture(scope="module")
def source_database(tmp_path_factory: pytest.TempPathFactory) -> Iterator[SourceDatabase]:
    docker = shutil.which("docker")
    if docker is None or subprocess.run([docker, "info"], capture_output=True).returncode != 0:  # noqa: S603
        pytest.skip("Docker indisponível: a origem precisa de PostgreSQL real")
    project = f"qe-source-it-{uuid.uuid4().hex[:8]}"
    directory = tmp_path_factory.mktemp("source-db")
    secrets_dir = directory / "secrets"
    secrets_dir.mkdir()
    for name in ("source_postgres", "qe_source_owner", "qe_source_writer", "qe_debezium"):
        (secrets_dir / name).write_text(secrets.token_urlsafe(18), "utf-8")
    override = directory / "ports.yaml"
    override.write_text('services:\n  source-db:\n    ports: ["127.0.0.1::5432"]\n', "utf-8")
    env = {**os.environ, "QE_SECRETS_DIR": str(secrets_dir)}

    def compose(*args: str) -> str:
        cmd = [
            docker,
            "compose",
            "-p",
            project,
            "-f",
            str(ROOT / "compose.yaml"),
            "-f",
            str(override),
            *args,
        ]
        done = subprocess.run(cmd, env=env, capture_output=True, text=True, check=False)  # noqa: S603
        if done.returncode:
            raise RuntimeError(f"source compose {' '.join(args)}: {done.stderr[-2000:]}")
        return done.stdout

    try:
        compose("up", "-d", "--wait", "source-db")
        port = int(compose("port", "source-db", "5432").strip().rsplit(":", 1)[1])
        database = SourceDatabase(port, secrets_dir)
        with database.connect("qe_source_owner") as conn:
            assert migrate_source(conn) == ["0001_portfolio_commands.sql", "0002_seed_status.sql"]
            assert migrate_source(conn) == []
        yield database
    finally:
        compose("down", "-v", "--remove-orphans")


@pytest.fixture
def source_conn(source_database: SourceDatabase) -> Iterator[psycopg.Connection[Any]]:
    with source_database.connect("qe_source_owner") as conn:
        conn.execute("""TRUNCATE public.asset_interval, public.portfolio_clock,
            privacy.subject, privacy.erase_block, ops.command_dedup, ops.seed_run""")
    with source_database.connect() as conn:
        yield conn


def payload(*, interval: str = "a#1", asset: str = "a") -> dict[str, Any]:
    return {
        "asset_interval_id": interval,
        "asset_id": asset,
        "subject_token": str(uuid.uuid4()),
        "valid_from": "2025-01-01T00:00:00+00:00",
        "valid_to": None,
        "lon": 120.5,
        "lat": -8.3,
        "insured_value_minor": 9007199254740993,
        "currency": "BRL",
        "is_deleted": False,
    }


def test_source_command_reentry_and_correct_advance_once(
    source_conn: psycopg.Connection[Any],
) -> None:
    row = payload()
    command_id = uuid.uuid4()
    first = execute_command(source_conn, "tenant", [row], command_id=command_id)
    assert first.epoch == 1
    assert execute_command(source_conn, "tenant", [row], command_id=command_id) == first
    with pytest.raises(psycopg.errors.RaiseException, match="outro conteúdo"):
        execute_command(source_conn, "tenant", [{**row, "lon": 121}], command_id=command_id)
    correction_id = uuid.uuid4()
    changed = correct(source_conn, "tenant", "a#1", lon=121, command_id=correction_id)
    assert changed.epoch == 2
    assert correct(source_conn, "tenant", "a#1", lon=121, command_id=correction_id) == changed
    assert source_conn.execute(
        "SELECT row_version, lon, insured_value_minor FROM public.asset_interval"
    ).fetchone() == (2, 121, 9007199254740993)


def test_source_writer_only_functions_and_publication_has_two_tables(
    source_conn: psycopg.Connection[Any],
    source_database: SourceDatabase,
) -> None:
    execute_command(source_conn, "tenant", [payload()])
    for statement in (
        "UPDATE public.portfolio_clock SET epoch = 20",
        "DELETE FROM public.asset_interval",
        "INSERT INTO public.asset_interval (tenant_id) VALUES ('hack')",
        "SELECT * FROM privacy.subject",
        "SELECT * FROM privacy.erase_block",
        "SELECT * FROM ops.command_dedup",
        "CREATE FUNCTION public.hijack() RETURNS int LANGUAGE sql AS 'SELECT 1'",
    ):
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            source_conn.execute(statement)
    with source_database.connect("qe_debezium") as conn:
        assert conn.execute("SELECT count(*) FROM public.asset_interval").fetchone() == (1,)
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("SELECT * FROM privacy.subject")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("SELECT privacy.block_subject(%s)", (uuid.uuid4(),))
    with source_database.connect("qe_source_owner") as conn:
        assert conn.execute(
            """SELECT schemaname, tablename FROM pg_publication_tables
               WHERE pubname = 'portfolio_publication' ORDER BY tablename"""
        ).fetchall() == [("public", "asset_interval"), ("public", "portfolio_clock")]
        assert conn.execute(
            "SELECT bool_and(contact LIKE '%@example.invalid') FROM privacy.subject"
        ).fetchone() == (True,)
    assert source_conn.execute("SELECT * FROM ops.cdc_slot_status()").fetchall() == []


def test_source_overlap_rolls_back_whole_command_and_clock(
    source_conn: psycopg.Connection[Any],
    source_database: SourceDatabase,
) -> None:
    row = payload()
    overlap = {**row, "asset_interval_id": "a#2"}
    with pytest.raises(psycopg.errors.ExclusionViolation):
        execute_command(source_conn, "tenant", [row, overlap], retroactive=True)
    assert source_conn.execute("SELECT count(*) FROM public.portfolio_clock").fetchone() == (0,)
    assert source_conn.execute("SELECT count(*) FROM public.asset_interval").fetchone() == (0,)
    with source_database.connect("qe_source_owner") as conn:
        assert conn.execute("SELECT count(*) FROM privacy.subject").fetchone() == (0,)
        assert conn.execute("SELECT count(*) FROM ops.command_dedup").fetchone() == (0,)


def test_source_retroactive_split_accepts_new_before_closed_in_one_command(
    source_conn: psycopg.Connection[Any],
) -> None:
    row = payload()
    execute_command(source_conn, "tenant", [row])
    closed = {**row, "valid_to": "2026-09-25T00:00:00+00:00"}
    opened = {**row, "asset_interval_id": "a#2", "valid_from": closed["valid_to"], "lon": 130}
    with pytest.raises(psycopg.errors.RaiseException, match="correção"):
        execute_command(source_conn, "tenant", [opened, closed])
    result = execute_command(source_conn, "tenant", [opened, closed], retroactive=True)
    assert (result.epoch, result.rows) == (2, 2)
    assert source_conn.execute(
        "SELECT asset_interval_id, row_version FROM public.asset_interval ORDER BY 1"
    ).fetchall() == [("a#1", 2), ("a#2", 1)]


def test_source_business_delete_remains_a_row_and_source_epoch_cannot_change(
    source_conn: psycopg.Connection[Any],
) -> None:
    row = payload()
    execute_command(source_conn, "tenant", [row], source_epoch=7)
    with pytest.raises(psycopg.errors.RaiseException, match="source_epoch"):
        execute_command(source_conn, "tenant", [row], source_epoch=8, retroactive=True)
    result = correct(source_conn, "tenant", "a#1", is_deleted=True)
    assert (result.source_epoch, result.epoch) == (7, 2)
    assert source_conn.execute(
        "SELECT is_deleted, row_version FROM public.asset_interval"
    ).fetchone() == (True, 2)


def test_source_erasure_blocks_old_commands_and_purges_all_intervals_once(
    source_conn: psycopg.Connection[Any],
    source_database: SourceDatabase,
) -> None:
    row = payload()
    token = uuid.UUID(row["subject_token"])
    execute_command(source_conn, "tenant", [row])
    assert block_subject(source_conn, token) == {"tenant_id": "tenant", "asset_id": "a"}
    assert block_subject(source_conn, token) == {"tenant_id": "tenant", "asset_id": "a"}
    assert source_conn.execute("SELECT epoch FROM public.portfolio_clock").fetchone() == (1,)
    with pytest.raises(psycopg.errors.RaiseException, match="bloqueado"):
        correct(source_conn, "tenant", "a#1", lon=121)
    command_id = uuid.uuid4()
    result = erase_subject(source_conn, token, command_id)
    assert (result.epoch, result.rows) == (2, 1)
    assert erase_subject(source_conn, token, command_id) == result
    with pytest.raises(psycopg.errors.RaiseException, match="bloqueado"):
        execute_command(source_conn, "tenant", [row])
    assert source_conn.execute("SELECT count(*) FROM public.asset_interval").fetchone() == (0,)
    with source_database.connect("qe_source_owner") as conn:
        assert conn.execute("SELECT count(*) FROM privacy.subject").fetchone() == (0,)
        assert conn.execute("SELECT count(*) FROM privacy.erase_block").fetchone() == (1,)


def test_source_subject_identity_is_one_random_token_per_asset(
    source_conn: psycopg.Connection[Any],
) -> None:
    row = payload()
    execute_command(source_conn, "tenant", [row])
    with pytest.raises(psycopg.errors.RaiseException, match="outro ativo"):
        execute_command(
            source_conn, "tenant", [{**row, "asset_interval_id": "b#1", "asset_id": "b"}]
        )
    with pytest.raises(psycopg.errors.RaiseException, match="imutável"):
        execute_command(
            source_conn, "tenant", [{**row, "subject_token": str(uuid.uuid4())}], retroactive=True
        )


def test_source_concurrent_commands_serialize_tenant_epoch(
    source_conn: psycopg.Connection[Any],
    source_database: SourceDatabase,
) -> None:
    execute_command(source_conn, "tenant", [payload(), payload(interval="b#1", asset="b")])

    def change(interval: str) -> int:
        with source_database.connect() as conn:
            return correct(conn, "tenant", interval, lon=121).epoch

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(change, ["a#1", "b#1"])) == [2, 3]
    assert source_conn.execute("SELECT epoch FROM public.portfolio_clock").fetchone() == (3,)
    assert source_conn.execute(
        "SELECT row_version FROM public.asset_interval ORDER BY asset_interval_id"
    ).fetchall() == [(2,), (2,)]


def test_source_seed_batches_preserve_movements_and_randomize_personal_tokens(
    source_conn: psycopg.Connection[Any],
) -> None:
    generated = generate(assets=100, seed=42)
    result = seed(source_conn, count=100, seed=42, batch_size=6)
    assert result.assets == 100
    assert result.rows == len(generated)
    assert result.commands == sum(result.epochs.values())
    assert source_conn.execute("SELECT * FROM ops.seed_status()").fetchone() == (100, 42, True)
    expected = {(r.tenant_id, r.asset_interval_id): row_payload(r) for r in generated}
    actual = source_conn.execute(
        """SELECT tenant_id, asset_interval_id, subject_token, lon, lat, insured_value_minor
           FROM public.asset_interval"""
    ).fetchall()
    assert len(actual) == len(expected)
    for tenant_id, interval, token, lon, lat, value in actual:
        wanted = expected[tenant_id, interval]
        assert (lon, lat, value) == (wanted["lon"], wanted["lat"], wanted["insured_value_minor"])
        assert str(token) != wanted["subject_token"]
    assert source_conn.execute(
        "SELECT count(DISTINCT subject_token) FROM public.asset_interval"
    ).fetchone() == (100,)
    with pytest.raises(ValueError, match="já tem carteira"):
        seed(source_conn, count=100)


def test_source_seed_interruption_never_marks_partial_load_complete(
    source_conn: psycopg.Connection[Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from quakeexposure.portfolio import source

    calls = 0

    def interrupted(*args: Any, **kwargs: Any) -> source.CommandResult:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("interrupção entre lotes")
        return execute_command(*args, **kwargs)

    monkeypatch.setattr(source, "execute_command", interrupted)
    with pytest.raises(RuntimeError, match="interrupção"):
        seed(source_conn, count=100, batch_size=6)
    assert source_conn.execute("SELECT * FROM ops.seed_status()").fetchone() == (100, 42, False)
    assert source_conn.execute("SELECT count(*) FROM public.asset_interval").fetchone() == (6,)
    with pytest.raises(ValueError, match="já tem carteira"):
        seed(source_conn, count=100, batch_size=6)
