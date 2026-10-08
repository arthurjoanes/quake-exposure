"""F14: o nó escolhido pelo índice é o mais próximo pelo geographiclib (WGS84).

Grid sintético em latitude alta (71° a 72°), cruzando o antimeridiano (179° a 181°).
"""

from builders import grid_xml
from geographiclib.geodesic import Geodesic
from hypothesis import example, given, settings
from hypothesis import strategies as st

from quakeexposure.domain.grid import parse_grid

STEP = 0.05
LONS = [179.0 + STEP * i for i in range(41)]
LATS = [72.0 - STEP * j for j in range(21)]
GRID = parse_grid(
    grid_xml(
        [round(lon - 360 if lon > 180 else lon, 4) for lon in LONS],
        LATS,
        mmi=lambda r, c: 1000 * r + c,
    )
)
# no elipsoide a fronteira entre dois nós não fica exatamente no meio do passo
# (a convergência dos meridianos muda dentro da célula); a folga cobre essa diferença
TOLERANCE_M = 1.0


def distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    return float(Geodesic.WGS84.Inverse(lat1, lon1, lat2, lon2)["s12"])


def lon_gap(a: float, b: float) -> float:
    return abs((a - b + 180) % 360 - 180)


@settings(deadline=None)
@given(st.floats(0.0, 2.0), st.floats(71.0, 72.0))
@example(0.9, 71.5)  # 179,9
@example(1.1, 71.5)  # -179,9
@example(1.0, 71.0)  # 180, borda sul
def test_f14_nearest_node_matches_geodesic(offset: float, lat: float) -> None:
    lon = 179.0 + offset
    lon = lon - 360 if lon > 180 else lon
    value = GRID.mmi_at(lon, lat)
    assert value is not None
    row, col = divmod(int(value), 1000)
    chosen = distance_m(lat, lon, LATS[row], LONS[col])

    nearby = [
        (la, lo)
        for la in LATS
        for lo in LONS
        if abs(la - lat) <= 2 * STEP and lon_gap(lo, lon) <= 2 * STEP
    ]
    best = min(distance_m(lat, lon, la, lo) for la, lo in nearby)
    assert chosen <= best + TOLERANCE_M
