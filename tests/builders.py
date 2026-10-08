"""Construtores de dados sintéticos para os testes."""

from collections.abc import Callable, Iterable, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from quakeexposure.domain.canonical import iso_ms
from quakeexposure.domain.events import EventRevision, revision_from_feature
from quakeexposure.domain.exposure import InputVector, StreamScope
from quakeexposure.domain.money import Currency
from quakeexposure.domain.portfolio import AssetVersion
from quakeexposure.domain.rule import DEFAULT_RULE, Mode

T = datetime(2026, 10, 6, 16, 48, 22, tzinfo=UTC)  # instante de um evento
DAY = timedelta(days=1)


def grid_xml(
    lons: Sequence[float],
    lats: Sequence[float],
    *,
    fields: Sequence[str] = ("LON", "LAT", "MMI", "PGA"),
    mmi: Callable[[int, int], float] = lambda r, c: 10 * r + c,
    prolog: str = "",
    skip_last: bool = False,
) -> bytes:
    rows = []
    for r, lat in enumerate(lats):
        for c, lon in enumerate(lons):
            values = {"LON": lon, "LAT": lat, "MMI": mmi(r, c), "PGA": 0.5}
            rows.append(" ".join(f"{values.get(f, 1.0):.4f}" for f in fields))
    if skip_last:
        rows.pop()
    field_tags = "".join(
        f'<grid_field index="{i + 1}" name="{f}" units="x" />' for i, f in enumerate(fields)
    )
    return (
        f'<?xml version="1.0" encoding="UTF-8"?>{prolog}'
        '<shakemap_grid xmlns="http://earthquake.usgs.gov/eqcenter/shakemap">'
        f'<grid_specification lon_min="{lons[0]}" lat_min="{lats[-1]}" lon_max="{lons[-1]}" '
        f'lat_max="{lats[0]}" nlon="{len(lons)}" nlat="{len(lats)}" />'
        f"{field_tags}<grid_data>\n" + "\n".join(rows) + "\n</grid_data></shakemap_grid>"
    ).encode()


def feature(
    event_id: str,
    *,
    updated: int,
    ids: Iterable[str] = (),
    time: int = 1_791_305_302_347,
    lon: float = 154.9316,
    lat: float = 48.4437,
    depth: float | None = 29.0,
    mag: float | None = 5.8,
    event_type: str | None = "earthquake",
    status: str = "reviewed",
    types: str | None = ",origin,shakemap,",
) -> dict[str, Any]:
    """Feature GeoJSON no formato do feed do USGS."""
    all_ids = sorted({event_id, *ids})
    return {
        "type": "Feature",
        "id": event_id,
        "geometry": {"type": "Point", "coordinates": [lon, lat, depth]},
        "properties": {
            "time": time,
            "updated": updated,
            "mag": mag,
            "status": status,
            "type": event_type,
            "types": types,
            "ids": "," + ",".join(all_ids) + ",",
            "net": event_id[:2],
            "code": event_id[2:],
        },
    }


def revision(event_id: str, *, updated: int, **kwargs: Any) -> EventRevision:
    return revision_from_feature(feature(event_id, updated=updated, **kwargs))


def vector(**overrides: Any) -> InputVector:
    fields: dict[str, Any] = {
        "stream_scope": StreamScope.REPLAY,
        "earthquake_uid": UUID(int=1),
        "event_semantic_sha256": "a" * 64,
        "product_revision_id": "urn:usgs-product:us:shakemap:us6000u0hy:1791391956496",
        "tenant_id": "t1",
        "portfolio_epoch": 3,
        "portfolio_source_epoch": 1,
        "mode": Mode.SHAKEMAP_MMI,
        "privacy_epoch": 0,
        "rule_version": DEFAULT_RULE.version,
    }
    fields.update(overrides)
    return InputVector(**fields)


def asset(asset_id: str, lon: float, lat: float, value: int, currency: Currency) -> AssetVersion:
    return AssetVersion("t1", asset_id, asset_id, 1, 1, T, None, lon, lat, value, currency, False)


def change(**overrides: Any) -> dict[str, Any]:
    doc: dict[str, Any] = {
        "tenant_id": "t1",
        "asset_interval_id": "i1",
        "asset_id": "A1",
        "subject_token": "6f1c2b9e-1d2a-4c55-9a43-2f6f4b1d7e10",
        "row_version": 1,
        "valid_from": iso_ms(T - DAY),
        "valid_to": None,
        "lon": -153.6,
        "lat": 58.8,
        "insured_value_minor": str(2**53 + 1),
        "currency": "USD",
        "is_deleted": False,
        "updated_at": iso_ms(T),
        "portfolio_epoch": 7,
        "source_epoch": 1,
    }
    doc.update(overrides)
    return doc
