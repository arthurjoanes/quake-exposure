"""Vetor de entrada, fatos por ativo e resultado de exposição de um evento para um tenant."""

from collections.abc import Collection, Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from functools import cached_property
from typing import Any
from uuid import UUID

from quakeexposure.domain.canonical import iso_ms, sha256_hex
from quakeexposure.domain.grid import Grid
from quakeexposure.domain.money import Currency, minor_to_json, totals_by_currency
from quakeexposure.domain.portfolio import AssetVersion
from quakeexposure.domain.rule import Mode, Rule


class StreamScope(StrEnum):
    LIVE = "LIVE"
    REPLAY = "REPLAY"  # captura real, com o horário original
    FAULT = "FAULT"  # mutação de teste, sempre rotulada


class ResultStatus(StrEnum):
    COMPLETE = "COMPLETE"
    NO_MAP = "NO_MAP"
    UNSUPPORTED_GRID = "UNSUPPORTED_GRID"
    RETRACTED = "RETRACTED"
    REDACTED_BY_ERASURE = "REDACTED_BY_ERASURE"


LIMITATIONS = {
    Mode.RADIUS_TRIAGE: ("RADIUS_IS_PROXIMITY_NOT_INTENSITY",),
    Mode.SHAKEMAP_MMI: ("MMI_FROM_NEAREST_GRID_NODE", "TRIAGE_RULE_NOT_DAMAGE_MODEL"),
}


@dataclass(frozen=True)
class InputVector:
    stream_scope: StreamScope
    earthquake_uid: UUID
    event_semantic_sha256: str
    product_revision_id: str | None  # None vira "NONE"; o modo RADIUS não depende do mapa
    tenant_id: str
    portfolio_epoch: int
    portfolio_source_epoch: int
    mode: Mode
    privacy_epoch: int
    rule_version: str

    def __post_init__(self) -> None:
        if self.mode == Mode.RADIUS_TRIAGE and self.product_revision_id is not None:
            raise ValueError("RADIUS_TRIAGE não usa ShakeMap: product_revision_id deve ser None")

    def to_json(self) -> dict[str, Any]:
        return {
            "stream_scope": self.stream_scope,
            "earthquake_uid": str(self.earthquake_uid),
            "event_semantic_sha256": self.event_semantic_sha256,
            "product_revision_id": self.product_revision_id or "NONE",
            "tenant_id": self.tenant_id,
            "portfolio_epoch": self.portfolio_epoch,
            "portfolio_source_epoch": self.portfolio_source_epoch,
            "mode": self.mode,
            "privacy_epoch": self.privacy_epoch,
            "rule_version": self.rule_version,
        }

    @cached_property
    def calculation_id(self) -> str:
        return sha256_hex(self.to_json())


@dataclass(frozen=True)
class AssetFact:
    asset_id: str
    currency: Currency
    insured_value_minor: int
    eligible: bool
    qualifying: bool
    mmi: float | None = None  # só no modo SHAKEMAP_MMI


@dataclass(frozen=True)
class ExposureResult:
    vector: InputVector
    status: ResultStatus
    eligible_count: int | None
    qualifying_count: int | None
    qualifying_value_minor: Mapping[Currency, int] | None
    calculated_at: datetime
    limitations: tuple[str, ...]

    def to_json(self, published_at: datetime | None = None) -> dict[str, Any]:
        values = self.qualifying_value_minor
        return {
            "calculation_id": self.vector.calculation_id,
            "input_vector": self.vector.to_json(),
            "status": self.status,
            "eligible_count": self.eligible_count,
            "qualifying_count": self.qualifying_count,
            "qualifying_value_minor": (
                None if values is None else {c: minor_to_json(v) for c, v in values.items()}
            ),
            "calculated_at": iso_ms(self.calculated_at),
            "published_at": None if published_at is None else iso_ms(published_at),
            "limitations": list(self.limitations),
        }


def mmi_facts(assets: Iterable[AssetVersion], grid: Grid, rule: Rule) -> list[AssetFact]:
    """Elegível: vigente e dentro da extensão do grid. Classificado: MMI >= limiar."""
    facts = []
    for a in assets:
        mmi = grid.mmi_at(a.lon, a.lat)
        facts.append(
            AssetFact(
                a.asset_id,
                a.currency,
                a.insured_value_minor,
                eligible=mmi is not None,
                qualifying=mmi is not None and rule.mmi_qualifies(mmi),
                mmi=mmi,
            )
        )
    return facts


def radius_facts(assets: Iterable[AssetVersion], within: Collection[str]) -> list[AssetFact]:
    """`within`: ids dos ativos que o ST_DWithin (geography, raio da regra) devolveu."""
    return [
        AssetFact(a.asset_id, a.currency, a.insured_value_minor, True, a.asset_id in within)
        for a in assets
    ]


def summarize(
    vector: InputVector, facts: Iterable[AssetFact], calculated_at: datetime
) -> ExposureResult:
    facts = list(facts)
    qualifying = [f for f in facts if f.qualifying]
    return ExposureResult(
        vector,
        ResultStatus.COMPLETE,
        eligible_count=sum(f.eligible for f in facts),
        qualifying_count=len(qualifying),
        qualifying_value_minor=totals_by_currency(
            (f.currency, f.insured_value_minor) for f in qualifying
        ),
        calculated_at=calculated_at,
        limitations=LIMITATIONS[vector.mode],
    )


def without_values(
    vector: InputVector, status: ResultStatus, calculated_at: datetime
) -> ExposureResult:
    """NO_MAP, UNSUPPORTED_GRID, RETRACTED e REDACTED_BY_ERASURE: contagens e valores nulos."""
    if status == ResultStatus.COMPLETE:
        raise ValueError("COMPLETE sai de summarize()")
    return ExposureResult(
        vector, status, None, None, None, calculated_at, (*LIMITATIONS[vector.mode], status)
    )
