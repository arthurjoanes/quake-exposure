from datetime import datetime
from typing import Any

import pytest
from builders import DAY, T, change

from quakeexposure.domain.canonical import iso_ms
from quakeexposure.domain.money import Currency
from quakeexposure.domain.portfolio import (
    AssetVersion,
    PortfolioError,
    asset_version_from_change,
    assets_as_of,
)


def version(
    interval: str,
    row_version: int,
    epoch: int,
    valid_from: datetime,
    valid_to: datetime | None,
    lon: float,
    *,
    asset: str = "A1",
    tenant: str = "t1",
    deleted: bool = False,
) -> AssetVersion:
    return AssetVersion(
        tenant, interval, asset, row_version, epoch, valid_from, valid_to, lon, 0.0, 100,
        Currency.BRL, deleted,
    )  # fmt: skip


def lons(versions: list[AssetVersion], at: datetime, epoch: int) -> list[float]:
    return [v.lon for v in assets_as_of(versions, tenant_id="t1", at=at, epoch=epoch)]


def test_f13_move_after_event_and_retroactive_correction() -> None:
    start = T - 300 * DAY
    log = [
        # epoch 1: ativo no ponto 10
        version("i1", 1, 1, start, None, 10.0),
        # epoch 2, depois do evento: muda para o ponto 20 a partir de T + 1 dia
        version("i1", 2, 2, start, T + DAY, 10.0),
        version("i2", 1, 2, T + DAY, None, 20.0),
        # epoch 3: correção retroativa, estava no ponto 30 desde T - 10 dias
        version("i1", 3, 3, start, T - 10 * DAY, 10.0),
        version("i3", 1, 3, T - 10 * DAY, T + DAY, 30.0),
    ]
    assert lons(log, T, 1) == [10.0]
    assert lons(log, T, 2) == [10.0]  # a mudança não volta para antes do evento
    assert lons(log, T, 3) == [30.0]  # o que se sabia na epoch 3 sobre o instante T
    assert lons(log, T + 2 * DAY, 3) == [20.0]  # cadastro de hoje, só para comparar
    assert lons(log, T, 0) == []


def test_soft_delete_tenant_and_interval_bounds() -> None:
    log = [
        version("i1", 1, 1, T, T + DAY, 1.0),
        version("i2", 1, 1, T - DAY, T, 2.0, asset="A2"),  # termina em T: fora
        version("i3", 1, 1, T - DAY, None, 3.0, asset="A3"),
        version("i3", 2, 2, T - DAY, None, 3.0, asset="A3", deleted=True),
        version("i4", 1, 1, T - DAY, None, 4.0, asset="A4", tenant="t2"),
    ]
    assert lons(log, T, 1) == [1.0, 3.0]
    assert lons(log, T, 2) == [1.0]


def test_two_live_intervals_for_one_asset_is_an_error() -> None:
    log = [version("i1", 1, 1, T - DAY, None, 1.0), version("i2", 1, 1, T, None, 2.0)]
    with pytest.raises(PortfolioError, match="dois intervalos"):
        assets_as_of(log, tenant_id="t1", at=T, epoch=1)


def test_change_to_version() -> None:
    v = asset_version_from_change(change())
    assert (v.known_epoch, v.insured_value_minor, v.currency) == (7, 2**53 + 1, Currency.USD)
    assert v.valid_from == T - DAY and v.valid_to is None
    assert not hasattr(v, "subject_token")


@pytest.mark.parametrize(
    "overrides",
    [{"valid_to": iso_ms(T - DAY)}, {"insured_value_minor": "12.5"}, {"currency": "EUR"}],
)
def test_bad_change(overrides: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        asset_version_from_change(change(**overrides))
