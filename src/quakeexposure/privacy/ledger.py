"""Ledger JSONL durável. Só identificadores necessários para impedir reintrodução."""

import json
import os
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from uuid import UUID

from quakeexposure.db import ROOT


@dataclass(frozen=True)
class LedgerEntry:
    request_id: str
    subject_token: str
    tenant_id: str
    asset_id: str
    source_epoch: int
    blocked_at: str

    def validate(self) -> None:
        UUID(self.request_id)
        UUID(self.subject_token)
        if not self.tenant_id or not self.asset_id or self.source_epoch < 1:
            raise ValueError("identidade inválida no ledger")
        if datetime.fromisoformat(self.blocked_at).tzinfo is None:
            raise ValueError("blocked_at sem timezone no ledger")


class Ledger:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or Path(
            os.environ.get("QE_ERASURE_LEDGER", str(ROOT / ".privacy" / "erasure.jsonl"))
        )

    def entries(self) -> list[LedgerEntry]:
        if not self.path.exists():
            return []
        raw = self.path.read_bytes()
        if raw and not raw.endswith(b"\n"):
            raise ValueError("ledger truncado: última linha não terminou")
        entries: dict[str, LedgerEntry] = {}
        for line in raw.splitlines():
            record = json.loads(line)
            entry = LedgerEntry(**record)
            entry.validate()
            if entry.subject_token in entries and entries[entry.subject_token] != entry:
                raise ValueError("ledger contém bloqueios divergentes para o mesmo titular")
            entries[entry.subject_token] = entry
        return list(entries.values())

    def append(self, entry: LedgerEntry) -> LedgerEntry:
        """Chamador serializa writers pelo advisory lock da plataforma."""
        entry.validate()
        for stored in self.entries():
            if stored.subject_token == entry.subject_token:
                if (stored.tenant_id, stored.asset_id, stored.source_epoch) != (
                    entry.tenant_id,
                    entry.asset_id,
                    entry.source_epoch,
                ):
                    raise ValueError("identidade do titular diverge do ledger")
                return stored
        self.path.parent.mkdir(parents=True, exist_ok=True)
        raw = (json.dumps(asdict(entry), sort_keys=True, separators=(",", ":")) + "\n").encode()
        descriptor = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            written = os.write(descriptor, raw)
            if written != len(raw):
                raise OSError("escrita incompleta no ledger")
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        if os.name != "nt":
            directory_fd = os.open(self.path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        return entry
