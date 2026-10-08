"""Carteira bitemporal: vigência `[valid_from, valid_to)` e epoch em que a versão ficou conhecida.

As-of: para um evento no instante T e a epoch K, vale a última versão de cada intervalo
conhecida até K, se estiver vigente em T. Nunca o cadastro de hoje.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from itertools import pairwise
from typing import Any

from quakeexposure.domain.money import Currency, check_minor, minor_from_json


class PortfolioError(ValueError):
    pass


@dataclass(frozen=True)
class AssetVersion:
    tenant_id: str
    asset_interval_id: str
    asset_id: str
    row_version: int
    known_epoch: int  # portfolio_epoch do comando que gravou esta versão
    valid_from: datetime
    valid_to: datetime | None  # None: sem fim
    lon: float
    lat: float
    insured_value_minor: int
    currency: Currency
    is_deleted: bool

    def valid_at(self, at: datetime) -> bool:
        return self.valid_from <= at and (self.valid_to is None or at < self.valid_to)


def assets_as_of(
    versions: Iterable[AssetVersion], *, tenant_id: str, at: datetime, epoch: int
) -> list[AssetVersion]:
    latest: dict[str, AssetVersion] = {}
    for v in versions:
        if v.tenant_id != tenant_id or v.known_epoch > epoch:
            continue
        seen = latest.get(v.asset_interval_id)
        if seen is None or v.row_version > seen.row_version:
            latest[v.asset_interval_id] = v
    live = sorted(
        (v for v in latest.values() if not v.is_deleted and v.valid_at(at)),
        key=lambda v: v.asset_id,
    )
    for a, b in pairwise(live):
        if a.asset_id == b.asset_id:
            raise PortfolioError(f"{tenant_id}/{a.asset_id}: dois intervalos vigentes em {at}")
    return live


def asset_version_from_change(change: Mapping[str, Any]) -> AssetVersion:
    """Uma mensagem `asset-interval-change` (já validada pelo contrato)."""
    valid_from = datetime.fromisoformat(change["valid_from"])
    valid_to = None if change["valid_to"] is None else datetime.fromisoformat(change["valid_to"])
    if valid_to is not None and valid_to <= valid_from:
        raise PortfolioError(f"{change['asset_interval_id']}: valid_to <= valid_from")
    return AssetVersion(
        tenant_id=change["tenant_id"],
        asset_interval_id=change["asset_interval_id"],
        asset_id=change["asset_id"],
        row_version=change["row_version"],
        known_epoch=change["portfolio_epoch"],
        valid_from=valid_from,
        valid_to=valid_to,
        lon=float(change["lon"]),
        lat=float(change["lat"]),
        insured_value_minor=check_minor(minor_from_json(change["insured_value_minor"])),
        currency=Currency(change["currency"]),
        is_deleted=change["is_deleted"],
    )
