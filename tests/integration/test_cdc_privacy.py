"""Exclusão preserva a barreira transacional sem manter payloads pessoais pendentes."""

import json
from uuid import UUID

from quakeexposure.cdc import apply_pending, stage
from quakeexposure.cdc.privacy import redact_subject
from quakeexposure.ingestion.store import Conn

from .test_cdc import NOW, asset_row, ingest, messages


def test_erasure_redacts_pending_stage_and_old_replay_remains_idempotent(conn: Conn) -> None:
    row = asset_row()
    token = UUID(row["subject_token"])
    batch = messages("pending-before-erase", 1, [row])
    ingest(conn, batch[:1])
    before = conn.execute("SELECT payload_sha256 FROM control.cdc_stage").fetchone()
    conn.execute("INSERT INTO privacy.erase_block(subject_token) VALUES (%s)", (token,))
    assert redact_subject(conn, token) == 1
    stored = conn.execute("SELECT payload FROM control.cdc_stage").fetchone()
    assert stored is not None
    assert stored[0]["erased"] is True
    assert stored[0]["after"] == {"tenant_id": "cdc-test", "asset_interval_id": "a#1"}
    serialized = json.dumps(stored[0])
    assert str(token) not in serialized
    assert all(
        field not in serialized for field in ("insured_value_minor", "lon", "lat", "currency")
    )
    assert conn.execute("SELECT payload_sha256 FROM control.cdc_stage").fetchone() == before
    assert not stage(conn, *batch[0], now=NOW)
    ingest(conn, batch[1:])
    assert apply_pending(conn, now=NOW) == 1
    assert conn.execute("SELECT count(*) FROM portfolio.asset_revision").fetchone() == (0,)
    assert conn.execute("SELECT portfolio_epoch FROM portfolio.tenant").fetchone() == (1,)
    assert conn.execute("SELECT count(payload) FROM control.cdc_stage").fetchone() == (0,)
    ingest(conn, batch)
    assert apply_pending(conn, now=NOW) == 0


def test_late_blocked_payload_is_redacted_before_stage_commit(conn: Conn) -> None:
    blocked = asset_row()
    survivor = asset_row(asset_id="b", asset_interval_id="b#1")
    conn.execute(
        "INSERT INTO privacy.erase_block(subject_token) VALUES (%s)", (blocked["subject_token"],)
    )
    batch = messages("after-erase", 1, [blocked, survivor])
    ingest(conn, batch)
    raw = json.dumps(conn.execute("SELECT payload FROM control.cdc_stage").fetchall())
    assert blocked["subject_token"] not in raw
    assert survivor["subject_token"] in raw
    assert apply_pending(conn, now=NOW) == 1
    assert conn.execute("SELECT asset_id FROM portfolio.asset_revision").fetchall() == [("b",)]
    assert conn.execute("SELECT asset_id FROM portfolio.asset_interval").fetchall() == [("b",)]
    assert conn.execute("SELECT portfolio_epoch FROM portfolio.tenant").fetchone() == (1,)
