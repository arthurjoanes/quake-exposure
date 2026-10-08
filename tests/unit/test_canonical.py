import hashlib
from datetime import UTC, datetime, timedelta, timezone
from uuid import UUID

import pytest

from quakeexposure.domain.canonical import canonical_json, from_epoch_ms, iso_ms, sha256_hex


def test_canonical_form() -> None:
    value = {"b": [1, 2.5, None], "a": "ação", "c": {"z": True, "y": "x"}}
    assert canonical_json(value) == '{"a":"ação","b":[1,2.5,null],"c":{"y":"x","z":true}}'.encode()
    assert sha256_hex(value) == hashlib.sha256(canonical_json(value)).hexdigest()


def test_instants_in_utc_with_milliseconds() -> None:
    brt = timezone(timedelta(hours=-3))
    dt = datetime(2026, 10, 6, 13, 48, 22, 347_891, tzinfo=brt)
    assert iso_ms(dt) == "2026-10-06T16:48:22.347Z"
    assert canonical_json({"t": dt}) == b'{"t":"2026-10-06T16:48:22.347Z"}'
    assert canonical_json([UUID(int=1)]) == b'["00000000-0000-0000-0000-000000000001"]'


def test_epoch_ms_is_exact() -> None:
    assert iso_ms(from_epoch_ms(1_791_305_302_347)) == "2026-10-06T16:48:22.347Z"
    assert from_epoch_ms(-1) == datetime(1969, 12, 31, 23, 59, 59, 999_000, tzinfo=UTC)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), datetime(2026, 1, 1), object()])
def test_rejects_values_without_canonical_form(value: object) -> None:
    with pytest.raises((ValueError, TypeError)):
        canonical_json({"v": value})
