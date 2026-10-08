"""JSON canônico para os hashes do domínio.

Chaves ordenadas, separadores compactos, UTF-8, instantes em ISO 8601 UTC com milissegundos.
"""

import hashlib
import json
from datetime import UTC, datetime, timedelta
from uuid import UUID

EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


def iso_ms(dt: datetime) -> str:
    if dt.tzinfo is None:
        raise ValueError(f"instante sem fuso: {dt}")
    return dt.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def from_epoch_ms(ms: int) -> datetime:
    # timedelta em vez de fromtimestamp(ms / 1000): sem passar por float
    return EPOCH + timedelta(milliseconds=ms)


def canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
        default=_encode,
    ).encode("utf-8")


def sha256_hex(value: object) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


def _encode(value: object) -> str:
    if isinstance(value, datetime):
        return iso_ms(value)
    if isinstance(value, UUID):
        return str(value)
    raise TypeError(f"sem forma canônica: {type(value).__name__}")
