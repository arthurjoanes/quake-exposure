import random
from datetime import datetime, timedelta

from builders import DAY, T
from hypothesis import given
from hypothesis import strategies as st

from quakeexposure.domain.money import Currency
from quakeexposure.domain.portfolio import AssetVersion, assets_as_of

ASSETS = ["A1", "A2", "A3"]
Snapshots = dict[int, dict[str, AssetVersion]]


@st.composite
def portfolio_log(draw: st.DrawFn) -> tuple[list[AssetVersion], Snapshots]:
    """Comandos epoch a epoch. O oráculo é a foto da origem depois de cada epoch.

    Cada comando reescreve os 3 intervalos de um ativo, cortando a linha do tempo em
    [-100 d, c1), [c1, c2) e [c2, sem fim): a foto nunca tem sobreposição.
    """
    log: list[AssetVersion] = []
    state: dict[str, AssetVersion] = {}
    snapshots: Snapshots = {0: {}}
    for epoch in range(1, draw(st.integers(1, 6)) + 1):
        for asset in draw(st.lists(st.sampled_from(ASSETS), min_size=1, unique=True)):
            cuts = draw(st.lists(st.integers(-60, 60), min_size=2, max_size=2, unique=True))
            c1, c2 = sorted(cuts)
            starts = [T - 100 * DAY, T + c1 * DAY, T + c2 * DAY]
            ends: list[datetime | None] = [T + c1 * DAY, T + c2 * DAY, None]
            for k in range(3):
                interval = f"{asset}#{k}"
                previous = state.get(interval)
                version = AssetVersion(
                    tenant_id="t1",
                    asset_interval_id=interval,
                    asset_id=asset,
                    row_version=previous.row_version + 1 if previous else 1,
                    known_epoch=epoch,
                    valid_from=starts[k],
                    valid_to=ends[k],
                    lon=draw(st.integers(-180, 180)) + 0.0,
                    lat=0.0,
                    insured_value_minor=draw(st.integers(0, 10**6)),
                    currency=Currency.BRL,
                    is_deleted=draw(st.booleans()),
                )
                log.append(version)
                state[interval] = version
        snapshots[epoch] = dict(state)
    return log, snapshots


@given(
    portfolio_log(),
    st.integers(-120, 120),
    st.sampled_from([0, 1]),  # 0: o instante cai exatamente numa borda de intervalo
    st.randoms(use_true_random=False),
)
def test_f13_as_of_matches_replayed_source(
    case: tuple[list[AssetVersion], Snapshots], days: int, hours: int, rng: random.Random
) -> None:
    log, snapshots = case
    at = T + timedelta(days=days, hours=hours)
    rng.shuffle(log)  # a ordem do log não importa
    for epoch, snapshot in snapshots.items():
        expected = sorted(
            (
                v
                for v in snapshot.values()
                if not v.is_deleted
                and v.valid_from <= at
                and (v.valid_to is None or at < v.valid_to)
            ),
            key=lambda v: v.asset_id,
        )
        assert assets_as_of(log, tenant_id="t1", at=at, epoch=epoch) == expected
