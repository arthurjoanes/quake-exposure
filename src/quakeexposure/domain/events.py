"""Revisões de evento do USGS: hashes, ordem e ciclo de vida."""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from functools import cached_property
from typing import Any

from quakeexposure.domain.canonical import from_epoch_ms, iso_ms, sha256_hex


class Lifecycle(StrEnum):
    ACTIVE = "ACTIVE"
    RETRACTED = "RETRACTED"


class Outcome(StrEnum):
    CURRENT = "CURRENT"  # virou o estado atual
    HISTORY = "HISTORY"  # mais antiga que a atual: só histórico, sem cálculo
    DUPLICATE = "DUPLICATE"  # mesma revision_id: sem efeito
    CONFLICT = "CONFLICT"  # mesmo source_updated_at, conteúdo diferente: quarentena
    ALIAS_CONFLICT = "ALIAS_CONFLICT"  # id em dois conjuntos atuais: quarentena


@dataclass(frozen=True)
class EventRevision:
    provider_event_id: str  # o `id` da feature, que é o id preferido
    source_updated_at: datetime
    lon: float
    lat: float
    depth_km: float | None
    time: datetime
    mag: float | None
    event_type: str | None
    status: str
    ids: frozenset[str]
    types: frozenset[str]

    @cached_property
    def semantic_sha256(self) -> str:
        return sha256_hex(
            {
                "lon": self.lon,
                "lat": self.lat,
                "time": iso_ms(self.time),
                "mag": self.mag,
                "event_type": self.event_type,
                "status": self.status,
            }
        )

    @cached_property
    def revision_id(self) -> str:
        return sha256_hex(
            [self.provider_event_id, iso_ms(self.source_updated_at), self.semantic_sha256]
        )

    @property
    def lifecycle(self) -> Lifecycle:
        return Lifecycle.RETRACTED if self.status == "deleted" else Lifecycle.ACTIVE


def revision_from_feature(feature: Mapping[str, Any]) -> EventRevision:
    """Feature GeoJSON do feed, do FDSN ou do detail (já validada pelo contrato de evento)."""
    props = feature["properties"]
    lon, lat, *rest = feature["geometry"]["coordinates"]
    depth = rest[0] if rest else None
    return EventRevision(
        provider_event_id=feature["id"],
        source_updated_at=from_epoch_ms(props["updated"]),
        lon=_number(lon),
        lat=_number(lat),
        depth_km=None if depth is None else _number(depth),
        time=from_epoch_ms(props["time"]),
        mag=None if props["mag"] is None else _number(props["mag"]),
        # evento deletado às vezes vem com type vazio e às vezes não, para o mesmo `updated`
        # (coleção do FDSN x consulta por eventid): ali o tipo não entra no hash
        event_type=None if props["status"] == "deleted" else props["type"] or None,
        status=props["status"],
        ids=_csv(props["ids"]) | {feature["id"]},
        types=_csv(props["types"]),
    )


def _number(value: float) -> float:
    # 5 e 5.0, -0.0 e 0.0 têm que dar o mesmo hash
    return float(value) + 0.0


def _csv(value: str | None) -> frozenset[str]:
    return frozenset(v for v in (value or "").split(",") if v)


@dataclass(frozen=True)
class EventHistory:
    """Revisões observadas de um evento, em ordem de `source_updated_at`."""

    revisions: tuple[EventRevision, ...] = ()

    @property
    def current(self) -> EventRevision | None:
        return self.revisions[-1] if self.revisions else None

    def apply(self, rev: EventRevision) -> tuple[EventHistory, Outcome]:
        same_time = next(
            (r for r in self.revisions if r.source_updated_at == rev.source_updated_at), None
        )
        if same_time is not None:
            if same_time.revision_id == rev.revision_id:
                return self, Outcome.DUPLICATE
            return self, Outcome.CONFLICT
        revisions = tuple(sorted((*self.revisions, rev), key=lambda r: r.source_updated_at))
        outcome = Outcome.CURRENT if revisions[-1] is rev else Outcome.HISTORY
        return EventHistory(revisions), outcome
