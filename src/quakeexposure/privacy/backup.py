"""Dumps das duas bases e restauração isolada com reaplicação do ledger atual."""

import hashlib
import json
import os
import shutil
import subprocess
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import psycopg
from psycopg.conninfo import make_conninfo

from quakeexposure.ingestion.store import Conn
from quakeexposure.privacy.erasure import replay_ledger
from quakeexposure.privacy.ledger import Ledger

RESTORE_DB = "qe_restore"
RESTORE_MARKER = "quake-exposure restore sandbox"


def _pg(tool: str, conn: Conn, args: list[str], *, database: str | None = None) -> None:
    executable = shutil.which(tool)
    if executable is None:
        raise RuntimeError(f"{tool} ausente; execute pelo serviço maintenance do Compose")
    env = {
        **os.environ,
        "PGHOST": conn.info.host,
        "PGPORT": str(conn.info.port),
        "PGUSER": conn.info.user,
        "PGPASSWORD": conn.info.password,
        "PGDATABASE": database or conn.info.dbname,
    }
    done = subprocess.run(  # noqa: S603
        [executable, *args],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    if done.returncode:
        raise RuntimeError(f"{tool} falhou: {done.stderr[-2000:]}")


def _sha(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def backup(platform: Conn, source: Conn, directory: Path) -> Path:
    """Cada base tem seu snapshot; a retomada da origem segue o runbook de CDC."""
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / (datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8])
    target.mkdir()
    files: dict[str, str] = {}
    for name, conn in (("platform", platform), ("source", source)):
        path = target / f"{name}.dump"
        _pg("pg_dump", conn, ["--format=custom", "--file", str(path), "--no-password"])
        files[path.name] = _sha(path)
    (target / "manifest.json").write_text(
        json.dumps({"created_at": datetime.now(UTC).isoformat(), "sha256": files}, indent=2),
        encoding="utf-8",
    )
    return target


def restore_verify(admin: Conn, directory: Path, ledger: Ledger) -> dict[str, Any]:
    """Só substitui qe_restore se o banco tiver a marca deste verificador."""
    manifest = json.loads((directory / "manifest.json").read_text("utf-8"))
    for name in ("platform.dump", "source.dump"):
        if _sha(directory / name) != manifest["sha256"][name]:
            raise ValueError(f"hash do backup diverge: {name}")
    # Ausência não é ledger vazio: o arquivo deve existir antes de restaurar.
    if not ledger.path.is_file():
        raise ValueError("ledger ausente: restore exige o arquivo atual de bloqueios")
    admin.execute("SELECT pg_advisory_lock(hashtext('quake-exposure:restore-verify'))")
    try:
        admin.execute("SELECT pg_advisory_lock(hashtext('quake-exposure:erase-ledger'))")
        entries = ledger.entries()
        existing = admin.execute(
            """SELECT shobj_description(oid, 'pg_database') FROM pg_database
               WHERE datname = 'qe_restore'"""
        ).fetchone()
        if existing is not None:
            if existing[0] != RESTORE_MARKER:
                raise ValueError("qe_restore já existe e não pertence ao verificador")
            admin.execute("DROP DATABASE qe_restore WITH (FORCE)")
        admin.execute("CREATE DATABASE qe_restore TEMPLATE template0")
        admin.execute("COMMENT ON DATABASE qe_restore IS 'quake-exposure restore sandbox'")
        admin.execute("REVOKE CONNECT ON DATABASE qe_restore FROM PUBLIC, qe_api_read")
        _pg(
            "pg_restore",
            admin,
            [
                "--dbname",
                RESTORE_DB,
                "--exit-on-error",
                "--no-owner",
                "--no-acl",
                "--no-password",
                str(directory / "platform.dump"),
            ],
            database=RESTORE_DB,
        )
        params = {
            **admin.info.get_parameters(),
            "password": admin.info.password,
            "dbname": RESTORE_DB,
        }
        with psycopg.connect(make_conninfo(**params), autocommit=True) as restored:
            replayed = replay_ledger(restored, ledger)
            for entry in entries:
                revision = restored.execute(
                    "SELECT count(*) FROM portfolio.asset_revision WHERE subject_token = %s",
                    (uuid.UUID(entry.subject_token),),
                ).fetchone()
                intervals = restored.execute(
                    """SELECT count(*) FROM portfolio.asset_interval
                       WHERE tenant_id=%s AND asset_id=%s""",
                    (entry.tenant_id, entry.asset_id),
                ).fetchone()
                facts = restored.execute(
                    """SELECT count(*) FROM exposure.fact f JOIN exposure.calculation c
                         USING (calculation_id) WHERE c.tenant_id=%s AND f.asset_id=%s""",
                    (entry.tenant_id, entry.asset_id),
                ).fetchone()
                stage = restored.execute(
                    """SELECT count(*) FROM control.cdc_stage
                       WHERE payload::text LIKE %s""",
                    (f"%{entry.subject_token}%",),
                ).fetchone()
                if (revision, intervals, facts, stage) != ((0,), (0,), (0,), (0,)):
                    raise AssertionError("titular reapareceu no banco restaurado")
            api_allowed = restored.execute(
                "SELECT has_database_privilege('qe_api_read', 'qe_restore', 'CONNECT')"
            ).fetchone()
            if api_allowed != (False,):
                raise AssertionError("a API consegue conectar ao banco de restauração")
        result = {
            "database": RESTORE_DB,
            "verified_at": datetime.now(UTC).isoformat(),
            "ledger_subjects": len(entries),
            "reapplied": replayed,
            "subjects_absent": True,
            "api_cannot_connect": True,
            "source_restore": "Restauração da origem exige repetir qe erase para o ledger.",
        }
        (directory / "restore-verification.json").write_text(
            json.dumps(result, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        return result
    finally:
        admin.execute("SELECT pg_advisory_unlock(hashtext('quake-exposure:erase-ledger'))")
        admin.execute("SELECT pg_advisory_unlock(hashtext('quake-exposure:restore-verify'))")
