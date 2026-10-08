"""Carteira fictícia reproduzível: 2 tenants, BRL e USD, a maior parte dentro dos grids de demo.

Os pontos ficam em volta dos epicentros dos eventos das fixtures (Flores, Kurilas e Alasca),
com uma parte fora de qualquer grid. Um pedaço dos ativos muda de lugar em 2026-09-25, entre
os eventos de demo, e entra numa segunda epoch: é o que faz o as-of aparecer.
"""

import random
import uuid
from dataclasses import dataclass, replace
from datetime import UTC, datetime

from quakeexposure.domain.money import Currency

VALID_FROM = datetime(2025, 1, 1, tzinfo=UTC)
MOVED_AT = datetime(2026, 9, 25, tzinfo=UTC)

# (nome, lon, lat, desvio em graus, peso): epicentros das fixtures e uma área sem grid
CLUSTERS = [
    ("flores", 120.5793, -8.307, 0.35, 0.40),  # us7000tiur, MMI até 6,7
    ("kurilas", 154.9316, 48.4437, 0.6, 0.25),  # us6000u0hy
    ("alasca", -153.609, 58.802, 1.2, 0.25),  # aka2026tuxgky
    ("fora", -46.63, -23.55, 2.0, 0.10),  # São Paulo: fora de todo grid
]
TENANTS = [
    ("seguradora-a", "Seguradora A", Currency.BRL, 0.8),  # 80% em BRL
    ("operadora-b", "Operadora B", Currency.USD, 0.85),
]


@dataclass(frozen=True)
class SeedRow:
    tenant_id: str
    asset_interval_id: str
    row_version: int
    asset_id: str
    subject_token: uuid.UUID
    known_epoch: int
    valid_from: datetime
    valid_to: datetime | None
    lon: float
    lat: float
    insured_value_minor: int
    currency: Currency
    is_deleted: bool


def generate(assets: int = 10_000, seed: int = 42, moved_share: float = 0.05) -> list[SeedRow]:
    """Linhas de asset_revision. Epoch 1: carga inicial; epoch 2: mudanças de lugar."""
    rng = random.Random(seed)  # noqa: S311 (dado fictício reproduzível, não segredo)
    rows: list[SeedRow] = []
    per_tenant = assets // len(TENANTS)
    for tenant_id, _, main_currency, main_share in TENANTS:
        for n in range(per_tenant):
            asset_id = f"{tenant_id[:3]}-{n:05d}"
            token = uuid.UUID(int=rng.getrandbits(128), version=4)
            currency = main_currency if rng.random() < main_share else _other(main_currency)
            value = int(rng.lognormvariate(16.0, 1.2))  # unidade menor: centavos
            lon, lat = _point(rng)
            moved = rng.random() < moved_share
            first = SeedRow(
                tenant_id, f"{asset_id}#1", 1, asset_id, token, 1, VALID_FROM, None, lon, lat,
                value, currency, False,
            )  # fmt: skip
            if not moved:
                rows.append(first)
                continue
            # epoch 2: o intervalo 1 termina em MOVED_AT e começa o 2, noutro ponto
            closed = replace(first, row_version=2, known_epoch=2, valid_to=MOVED_AT)
            new_lon, new_lat = _point(rng)
            second = SeedRow(
                tenant_id, f"{asset_id}#2", 1, asset_id, token, 2, MOVED_AT, None, new_lon,
                new_lat, value, currency, False,
            )  # fmt: skip
            rows += [first, closed, second]
    return rows


def _other(currency: Currency) -> Currency:
    return Currency.USD if currency is Currency.BRL else Currency.BRL


def _point(rng: random.Random) -> tuple[float, float]:
    _, lon, lat, spread, _ = rng.choices(CLUSTERS, weights=[c[4] for c in CLUSTERS])[0]
    x = max(-180.0, min(180.0, rng.gauss(lon, spread)))
    y = max(-89.0, min(89.0, rng.gauss(lat, spread)))
    return round(x, 5), round(y, 5)
