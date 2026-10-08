"""Migrações SQL numeradas (`sql/platform/NNNN_nome.sql`), todas numa transação com lock."""

import hashlib
import logging
from pathlib import Path

import psycopg

from quakeexposure.db import ROOT

log = logging.getLogger(__name__)
PLATFORM = ROOT / "sql" / "platform"


class MigrationError(Exception):
    pass


def migrate(conn: psycopg.Connection, directory: Path = PLATFORM) -> list[str]:
    """Aplica as migrações que faltam. Migração já aplicada e depois editada é erro."""
    applied_now = []
    with conn.transaction():
        conn.execute("SELECT pg_advisory_xact_lock(hashtext('quake-exposure:migrate'))")
        conn.execute("CREATE SCHEMA IF NOT EXISTS control")
        conn.execute(
            """CREATE TABLE IF NOT EXISTS control.schema_migration (
                   version int PRIMARY KEY,
                   name text NOT NULL,
                   sha256 text NOT NULL,
                   applied_at timestamptz NOT NULL DEFAULT now())"""
        )
        done = {
            v: sha
            for v, sha in conn.execute("SELECT version, sha256 FROM control.schema_migration")
        }
        for path in sorted(directory.glob("[0-9][0-9][0-9][0-9]_*.sql")):
            version = int(path.name[:4])
            sql = path.read_bytes()
            sha = hashlib.sha256(sql).hexdigest()
            if version in done:
                if done[version] != sha:
                    raise MigrationError(f"{path.name} mudou depois de aplicada")
                continue
            with conn.transaction():
                conn.execute(sql)
                conn.execute(
                    """INSERT INTO control.schema_migration (version, name, sha256)
                       VALUES (%s, %s, %s)""",
                    (version, path.name, sha),
                )
            log.info("migração aplicada: %s", path.name)
            applied_now.append(path.name)
    return applied_now
