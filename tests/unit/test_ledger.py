import json
import uuid
from pathlib import Path

import pytest

from quakeexposure.privacy.ledger import Ledger, LedgerEntry


def entry() -> LedgerEntry:
    return LedgerEntry(
        str(uuid.uuid4()), str(uuid.uuid4()), "tenant", "asset", 1, "2026-10-09T12:00:00+00:00"
    )


def test_ledger_is_idempotent_and_persists_only_restore_identifiers(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / "outside-db" / "erasure.jsonl")
    record = entry()
    assert ledger.append(record) == record
    assert ledger.append(record) == record
    assert Ledger(ledger.path).entries() == [record]
    content = json.loads(ledger.path.read_text("utf-8"))
    assert set(content) == {
        "request_id",
        "subject_token",
        "tenant_id",
        "asset_id",
        "source_epoch",
        "blocked_at",
    }
    assert len(ledger.path.read_text("utf-8").splitlines()) == 1


def test_ledger_rejects_truncation_and_conflicting_identity(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / "erasure.jsonl")
    record = entry()
    ledger.append(record)
    changed = LedgerEntry(
        record.request_id,
        record.subject_token,
        "other",
        record.asset_id,
        record.source_epoch,
        record.blocked_at,
    )
    with pytest.raises(ValueError, match="diverge"):
        ledger.append(changed)
    ledger.path.write_bytes(ledger.path.read_bytes().rstrip(b"\n"))
    with pytest.raises(ValueError, match="truncado"):
        ledger.entries()
