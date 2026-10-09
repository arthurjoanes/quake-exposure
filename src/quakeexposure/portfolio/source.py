"""Comandos da carteira na origem; a plataforma recebe as mudanças pelo CDC."""

import hashlib
import os
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from quakeexposure.db import ROOT
from quakeexposure.migrate import MigrationError
from quakeexposure.portfolio.synthetic import SeedRow, generate

SOURCE = ROOT / "sql" / "source"


@dataclass(frozen=True)
class CommandResult:
    command_id: uuid.UUID
    tenant_id: str
    epoch: int
    source_epoch: int
    rows: int


@dataclass(frozen=True)
class SeedResult:
    assets: int
    rows: int
    commands: int
    epochs: dict[str, int]


def connect_source(
    user: str | None = None, password_file: str | None = None
) -> psycopg.Connection[Any]:
    """Conexão autocommit; a senha vem de arquivo, como na plataforma."""
    password_path = password_file or os.environ.get("QE_SOURCE_DB_PASSWORD_FILE")
    return psycopg.connect(
        host=os.environ.get("QE_SOURCE_DB_HOST", "127.0.0.1"),
        port=int(os.environ.get("QE_SOURCE_DB_PORT", "5432")),
        dbname=os.environ.get("QE_SOURCE_DB_NAME", "qe_source"),
        user=user or os.environ.get("QE_SOURCE_DB_USER", "qe_source_writer"),
        password=Path(password_path).read_text("utf-8").strip() if password_path else None,
        autocommit=True,
        options="-c TimeZone=UTC",
        application_name="quake-exposure-source",
    )


def migrate_source(conn: psycopg.Connection[Any], directory: Path = SOURCE) -> list[str]:
    """Migrações imutáveis da origem, fora da publicação de CDC."""
    applied: list[str] = []
    with conn.transaction():
        conn.execute("SELECT pg_advisory_xact_lock(hashtext('quake-exposure:source-migrate'))")
        conn.execute("CREATE SCHEMA IF NOT EXISTS ops")
        conn.execute(
            """CREATE TABLE IF NOT EXISTS ops.schema_migration (
                version int PRIMARY KEY, name text NOT NULL, sha256 text NOT NULL,
                applied_at timestamptz NOT NULL DEFAULT now())"""
        )
        done = dict(conn.execute("SELECT version, sha256 FROM ops.schema_migration").fetchall())
        for path in sorted(directory.glob("[0-9][0-9][0-9][0-9]_*.sql")):
            version = int(path.name[:4])
            sql = path.read_bytes()
            sha = hashlib.sha256(sql).hexdigest()
            if version in done:
                if done[version] != sha:
                    raise MigrationError(f"{path.name} mudou depois de aplicada")
                continue
            conn.execute(sql)
            conn.execute(
                "INSERT INTO ops.schema_migration (version, name, sha256) VALUES (%s, %s, %s)",
                (version, path.name, sha),
            )
            applied.append(path.name)
    return applied


def _result(value: dict[str, Any]) -> CommandResult:
    return CommandResult(
        uuid.UUID(value["command_id"]), value["tenant_id"], int(value["epoch"]),
        int(value["source_epoch"]), int(value["rows"]),
    )  # fmt: skip


def execute_command(
    conn: psycopg.Connection[Any],
    tenant_id: str,
    rows: Sequence[dict[str, Any]],
    *,
    command_id: uuid.UUID | None = None,
    source_epoch: int = 1,
    retroactive: bool = False,
) -> CommandResult:
    """Altera intervalos e avança uma epoch. Repetir command_id/conteúdo não altera nada."""
    command_id = command_id or uuid.uuid4()
    with conn.transaction():
        row = conn.execute(
            "SELECT ops.portfolio_command(%s, %s, %s, %s, %s)",
            (command_id, tenant_id, Jsonb(list(rows)), source_epoch, retroactive),
        ).fetchone()
        assert row is not None
        return _result(row[0])


def row_payload(row: SeedRow) -> dict[str, Any]:
    """JSON da função da origem; row_version e epoch são atribuídas no banco."""
    return {
        "asset_interval_id": row.asset_interval_id,
        "asset_id": row.asset_id,
        "subject_token": str(row.subject_token),
        "valid_from": row.valid_from.isoformat(),
        "valid_to": row.valid_to.isoformat() if row.valid_to else None,
        "lon": row.lon,
        "lat": row.lat,
        "insured_value_minor": row.insured_value_minor,
        "currency": row.currency.value,
        "is_deleted": row.is_deleted,
    }


def seed(
    conn: psycopg.Connection[Any],
    count: int = 10_000,
    seed: int = 42,
    *,
    batch_size: int = 500,
    source_epoch: int = 1,
) -> SeedResult:
    """Semeia dois tenants por comandos; recusa origem já carregada.

    Pontos e valores são reproduzíveis, mas os UUIDs pessoais são aleatórios a cada carga.
    Cada mudança fecha e abre os intervalos do ativo dentro do mesmo comando.
    """
    if count < 2 or count % 2:
        raise ValueError("count precisa ser par e pelo menos 2 (dois tenants)")
    if not 2 <= batch_size <= 2000:
        raise ValueError("batch_size deve estar entre 2 e 2000")
    rows = generate(assets=count, seed=seed)
    tenant_ids = sorted({r.tenant_id for r in rows})
    if conn.execute(
        "SELECT 1 FROM public.portfolio_clock WHERE tenant_id = ANY(%s) LIMIT 1", (tenant_ids,)
    ).fetchone():
        raise ValueError("a origem já tem carteira; seed exige tenants vazios")
    conn.execute("SELECT ops.seed_begin(%s, %s)", (count, seed))
    tokens: dict[tuple[str, str], uuid.UUID] = {}
    rows = [
        replace(r, subject_token=tokens.setdefault((r.tenant_id, r.asset_id), uuid.uuid4()))
        for r in rows
    ]
    changed = commands = 0
    epochs: dict[str, int] = {}
    for tenant_id in tenant_ids:
        initial = [r for r in rows if r.tenant_id == tenant_id and r.known_epoch == 1]
        amendments = [r for r in rows if r.tenant_id == tenant_id and r.known_epoch == 2]
        # Pares fechado/novo nunca ficam em comandos separados.
        for part, size, retroactive in (
            (initial, batch_size, False),
            (amendments, batch_size - batch_size % 2, True),
        ):
            for offset in range(0, len(part), size):
                result = execute_command(
                    conn,
                    tenant_id,
                    [row_payload(r) for r in part[offset : offset + size]],
                    source_epoch=source_epoch,
                    retroactive=retroactive,
                )
                changed += result.rows
                commands += 1
                epochs[tenant_id] = result.epoch
    conn.execute("SELECT ops.seed_finish(%s, %s, %s)", (count, seed, changed))
    return SeedResult(count, changed, commands, epochs)


def correct(
    conn: psycopg.Connection[Any],
    tenant_id: str,
    asset_interval_id: str,
    *,
    lon: float | None = None,
    lat: float | None = None,
    insured_value_minor: int | None = None,
    is_deleted: bool | None = None,
    command_id: uuid.UUID | None = None,
) -> CommandResult:
    """Correção retroativa explícita, serializada e idempotente na função da origem."""
    changes = {
        k: v
        for k, v in {
            "lon": lon,
            "lat": lat,
            "insured_value_minor": insured_value_minor,
            "is_deleted": is_deleted,
        }.items()
        if v is not None
    }
    if not changes:
        raise ValueError("informe pelo menos um campo da correção")
    with conn.transaction():
        row = conn.execute(
            "SELECT ops.correct_interval(%s, %s, %s, %s)",
            (command_id or uuid.uuid4(), tenant_id, asset_interval_id, Jsonb(changes)),
        ).fetchone()
        assert row is not None
        return _result(row[0])


def block_subject(conn: psycopg.Connection[Any], subject_token: uuid.UUID) -> dict[str, str]:
    with conn.transaction():
        row = conn.execute("SELECT privacy.block_subject(%s)", (subject_token,)).fetchone()
        assert row is not None
        return {str(key): str(value) for key, value in row[0].items()}


def erase_subject(
    conn: psycopg.Connection[Any], subject_token: uuid.UUID, command_id: uuid.UUID
) -> CommandResult:
    with conn.transaction():
        row = conn.execute(
            "SELECT privacy.erase_subject(%s, %s)", (command_id, subject_token)
        ).fetchone()
        assert row is not None
        return _result(row[0])
