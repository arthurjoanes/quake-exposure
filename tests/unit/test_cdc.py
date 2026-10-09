"""Validação do envelope Debezium antes de abrir uma transação na plataforma."""

from datetime import UTC, datetime
from typing import Any, cast

import pytest

from quakeexposure.cdc.store import CdcError, decode, stage, timestamp, transaction_key
from quakeexposure.ingestion.store import Conn


def test_debezium_36_groups_changing_lsn_by_xid_and_commit_timestamp() -> None:
    row = {"transaction": {"id": "782:31008088"}, "source": {"ts_ms": 1791584835176}}
    end = {"id": "782:31008536", "ts_ms": 1791584835176}
    assert transaction_key(row, end=False) == transaction_key(end, end=True)
    assert transaction_key(end, end=True) == "782:1791584835176"
    assert transaction_key({**end, "ts_ms": 1791584835177}, end=True) != "782:1791584835176"


def test_decode_schema_wrapper_and_timestamp_preserve_microseconds() -> None:
    assert decode(b'{"schema":null,"payload":{"op":"c"}}') == {"op": "c"}
    at = datetime(2026, 10, 9, 12, 1, 2, 123456, tzinfo=UTC)
    assert timestamp("2026-10-09T09:01:02.123456-03:00") == at
    assert timestamp(1791547262123456) == at


@pytest.mark.parametrize("value", [None, True, "2026-10-09T12:00:00", "bad", float("nan")])
def test_invalid_timestamp_fails(value: Any) -> None:
    with pytest.raises(CdcError, match="timestamp"):
        timestamp(value)


@pytest.mark.parametrize(
    ("topic", "payload", "error"),
    [
        ("portfolio.public.asset_interval", {"op": "r"}, "op=r proibido"),
        ("portfolio.public.asset_interval", {"op": "c"}, "transaction.metadata"),
        ("portfolio.public.asset_interval", {"op": "truncate"}, "op CDC"),
        ("not-allowed", {}, "tópico"),
        ("portfolio.transaction", {"status": "OTHER"}, "BEGIN/END"),
        (
            "portfolio.transaction",
            {"status": "END", "id": "x", "event_count": 1, "data_collections": []},
            "soma das tabelas",
        ),
        (
            "portfolio.transaction",
            {
                "status": "END",
                "id": "x",
                "event_count": 1,
                "data_collections": [
                    {"data_collection": "privacy.subject", "event_count": 1},
                ],
            },
            "tabela inesperada",
        ),
    ],
)
def test_invalid_cdc_stops_before_database(topic: str, payload: dict[str, Any], error: str) -> None:
    with pytest.raises(CdcError, match=error):
        stage(cast(Conn, None), topic, payload)


def test_begin_ignored_without_database() -> None:
    assert not stage(cast(Conn, None), "portfolio.transaction", {"status": "BEGIN"})
