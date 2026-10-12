"""Snapshot REPLAY público: captura preservada, as-of e dinheiro por moeda."""

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from scripts.export_demo import fixture_snapshot, write_snapshot

AT = datetime(2026, 10, 11, 23, 0, tzinfo=UTC)
COMMIT = "a" * 40


@pytest.fixture(scope="module")
def exported() -> tuple[dict[str, Any], dict[str, Any]]:
    return fixture_snapshot(COMMIT, AT)


def test_captured_flores_result_matches_saved_seed_42_counts_and_values(
    exported: tuple[dict[str, Any], dict[str, Any]],
) -> None:
    snapshot, records = exported
    uid = next(e["earthquake_uid"] for e in snapshot["events"] if e["preferred_id"] == "us7000tiur")
    original = [
        record["current"]["result"]
        for record in records.values()
        if record["current"]["result"]["input_vector"]["earthquake_uid"] == uid
        and record["current"]["result"]["input_vector"]["tenant_id"] == "operadora-b"
        and record["current"]["result"]["input_vector"]["portfolio_epoch"] in (1, 2)
    ]
    assert len(original) == 2
    for result in original:
        assert result["eligible_count"] == 1972
        assert result["qualifying_count"] == 45
        assert result["qualifying_value_minor"] == {"BRL": "86434666", "USD": "514787670"}


def test_retroactive_correction_changes_one_fictional_asset_and_one_exact_currency_total(
    exported: tuple[dict[str, Any], dict[str, Any]],
) -> None:
    snapshot, records = exported
    scenario = snapshot["metadata"]["scenario"]
    assert scenario["changes"] == 1
    current = records[scenario["publication_id"]]
    previous = records[scenario["previous_publication_id"]]
    change = current["comparison"]["changes"][0]
    assert change["asset_id"] == "ope-00001"
    assert change["before"]["mmi"] == 4.0
    assert change["after"]["mmi"] == 6.7
    assert change["before"]["qualifying"] is False
    assert change["after"]["qualifying"] is True
    assert change["before"]["insured_value_minor"] == "1613163"
    assert change["after"]["insured_value_minor"] == "1613163"
    before, after = previous["current"]["result"], current["current"]["result"]
    assert after["qualifying_count"] == 46
    assert after["eligible_count"] == before["eligible_count"]
    assert after["qualifying_value_minor"] == {"BRL": "86434666", "USD": "516400833"}
    assert current["diff"]["left"] == []
    assert [asset["asset_id"] for asset in current["diff"]["entered"]] == ["ope-00001"]


def test_event_time_keeps_the_original_portfolio_move_out_of_the_flores_comparison(
    exported: tuple[dict[str, Any], dict[str, Any]],
) -> None:
    snapshot, records = exported
    uids = {e["preferred_id"]: e["earthquake_uid"] for e in snapshot["events"]}
    second_epochs = {
        record["current"]["result"]["input_vector"]["earthquake_uid"]: record
        for record in records.values()
        if record["current"]["result"]["input_vector"]["tenant_id"] == "operadora-b"
        and record["current"]["result"]["input_vector"]["portfolio_epoch"] == 2
    }
    assert second_epochs[uids["us7000tiur"]]["comparison"]["total_changes"] == 0
    assert second_epochs[uids["us6000u0hy"]]["comparison"]["total_changes"] == 271
    assert snapshot["metadata"]["pipeline_executed"] is False
    assert snapshot["metadata"]["available_modes"] == ["SHAKEMAP_MMI"]


def test_public_files_contain_only_replay_and_no_identity_contact_or_cdc_payload(
    exported: tuple[dict[str, Any], dict[str, Any]],
    tmp_path: Path,
) -> None:
    snapshot, records = exported
    # A escrita altera somente hashes do índice; usa cópia para isolar a fixture compartilhada.
    snapshot = json.loads(json.dumps(snapshot))
    write_snapshot(tmp_path, snapshot, records)
    for filename in tmp_path.glob("*.json"):
        raw = filename.read_bytes()
        assert b"\r" not in raw
        assert raw.endswith(b"\n")
        content = filename.read_text("utf-8")
        for private_key in ("subject_token", "contact", "cdc_stage", "payload", "password"):
            assert f'"{private_key}"' not in content
        assert '"LIVE"' not in content
        assert '"FAULT"' not in content
    for entry in snapshot["publications"].values():
        raw = (tmp_path / entry["file"]).read_bytes()
        assert hashlib.sha256(raw).hexdigest() == entry["sha256"]
        assert hashlib.sha256(raw.replace(b"\r\n", b"\n")).hexdigest() == entry["sha256"]
    assert len(snapshot["current"]) == 4
    assert len(records) == 12
