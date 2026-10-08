"""Regra de triagem. É do projeto, não é modelo de dano."""

from dataclasses import dataclass, field
from enum import StrEnum
from functools import cached_property

from quakeexposure.domain.canonical import sha256_hex


class Mode(StrEnum):
    RADIUS_TRIAGE = "RADIUS_TRIAGE"  # proximidade do epicentro, não intensidade
    SHAKEMAP_MMI = "SHAKEMAP_MMI"  # MMI do grid no ponto do ativo


@dataclass(frozen=True)
class Rule:
    """Registro imutável. A versão é o hash do conteúdo: mudou um valor, é outra versão."""

    radius_m: int = 100_000
    mmi_threshold: float = 6.0
    event_types: frozenset[str] = field(default=frozenset({"earthquake"}))
    product_selection: str = "max-preferredWeight-then-latest-updateTime"
    sampling: str = "nearest-grid-node"

    @cached_property
    def version(self) -> str:
        return sha256_hex(
            {
                "radius_m": self.radius_m,
                "mmi_threshold": float(self.mmi_threshold),
                "event_types": sorted(self.event_types),
                "product_selection": self.product_selection,
                "sampling": self.sampling,
            }
        )

    def event_eligible(self, event_type: str | None) -> bool:
        return event_type in self.event_types

    def mmi_qualifies(self, mmi: float) -> bool:
        return mmi >= self.mmi_threshold


DEFAULT_RULE = Rule()
