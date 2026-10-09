"""F19: dumps reais antes da exclusão, restore isolado e ledger posterior ao dump."""

import subprocess
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from builders import feature

from quakeexposure.db import ROOT
from quakeexposure.ingestion.store import Conn
from quakeexposure.portfolio.source import execute_command
from quakeexposure.privacy import backup as backups
from quakeexposure.privacy.erasure import erase
from quakeexposure.privacy.ledger import Ledger

from .cdc_support import CdcStack
from .conftest import PROJECT, Harness
from .test_cdc import asset_row
from .test_cdc_real import apply_batch
from .test_exposure import feed, publish


def test_f19_restore_old_dump_reapplies_new_ledger_and_denies_api(
    cdc_stack: CdcStack,
    harness: Harness,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Transporte dos binários PG pelo Docker; pg_dump/pg_restore são os mesmos reais.
    def pg(tool: str, conn: Conn, args: list[str], *, database: str | None = None) -> None:
        source = conn.info.dbname == "qe_source"
        service = "source-db" if source else "platform-db"
        command = [
            "docker",
            "compose",
            "-p",
            PROJECT,
            "-f",
            str(ROOT / "compose.yaml"),
            "-f",
            str(ROOT / "compose.test.yaml"),
            "exec",
            "-T",
            service,
            tool,
            "--username=postgres",
        ]
        if tool == "pg_dump":
            path = Path(args[args.index("--file") + 1])
            command += ["--format=custom", "--dbname", conn.info.dbname]
            result = subprocess.run(command, env=cdc_stack.env, capture_output=True)  # noqa: S603
            assert result.returncode == 0, result.stderr.decode()
            path.write_bytes(result.stdout)
        else:
            command += args[:-1]
            result = subprocess.run(  # noqa: S603
                command,
                env=cdc_stack.env,
                input=Path(args[-1]).read_bytes(),
                capture_output=True,
            )
            assert result.returncode == 0, result.stderr.decode()

    monkeypatch.setattr(backups, "_pg", pg)
    h = harness
    tenant = f"restore-{uuid4().hex}"
    row = asset_row()
    token = UUID(row["subject_token"])
    ledger = Ledger(tmp_path / "ledger.jsonl")
    with cdc_stack.source() as source, cdc_stack.database.connect("postgres") as admin:
        records = cdc_stack.capture(lambda: execute_command(source, tenant, [row]))
        assert apply_batch(h.conn, records) == 1
        feed(h, feature("us-restore", updated=1_000, lon=0, lat=0))
        assert publish(h)["failed"] == 0
        with cdc_stack.source("qe_source_owner") as source_owner:
            directory = backups.backup(admin, source_owner, tmp_path / "backups")
        assert erase(h.conn, source, token, ledger=ledger)["status"] == "CLOSED"
        verified = backups.restore_verify(admin, directory, ledger)
        assert verified["subjects_absent"] and verified["api_cannot_connect"]
        assert verified["reapplied"]["revisions"] == 1
        assert verified["reapplied"]["facts"] > 0
        # Repetir substitui apenas o sandbox marcado, sem tocar qe.
        assert backups.restore_verify(admin, directory, ledger)["subjects_absent"]
        assert h.count("portfolio.asset_revision") == 0
        with (directory / "platform.dump").open("ab") as stream:
            stream.write(b"corruption")
        with pytest.raises(ValueError, match="hash"):
            backups.restore_verify(admin, directory, ledger)
