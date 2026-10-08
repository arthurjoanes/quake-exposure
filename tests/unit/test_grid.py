import random
import re
from collections.abc import Callable

import pytest
from builders import grid_xml
from conftest import MANIFEST

from quakeexposure.domain.grid import Grid, UnsupportedGrid, parse_grid

REAL_GRIDS = [e["name"] for e in MANIFEST if e["kind"] == "grid"]


def raw_rows(text: str) -> tuple[dict[str, str], list[dict[str, float]]]:
    """Oráculo: lê o grid com regex, sem passar pelo parser."""
    tag = re.search(r"<grid_specification[^>]*>", text)
    assert tag
    spec = dict(re.findall(r'(\w+)="([^"]*)"', tag[0]))
    names = {int(i) - 1: n for i, n in re.findall(r'<grid_field index="(\d+)" name="(\w+)"', text)}
    data = text.split("<grid_data>")[1].split("</grid_data>")[0]
    rows = [
        {
            names[i]: float(v)
            for i, v in enumerate(line.split())
            if names[i] in ("LON", "LAT", "MMI")
        }
        for line in data.splitlines()
        if line.strip()
    ]
    return spec, rows


@pytest.mark.parametrize("name", REAL_GRIDS)
def test_f14_reads_real_grid(name: str, usgs_fixture: Callable[[str], bytes]) -> None:
    data = usgs_fixture(name)
    grid = parse_grid(data)
    spec, rows = raw_rows(data.decode("utf-8"))

    assert (grid.nlon, grid.nlat) == (int(spec["nlon"]), int(spec["nlat"]))
    assert len(grid.mmi) == len(rows) == grid.nlon * grid.nlat
    assert grid.lon_min == float(spec["lon_min"]) and grid.lat_max == float(spec["lat_max"])

    # nó sorteado + deslocamento menor que meio passo: o valor tem que ser o daquela linha
    rng = random.Random(name)
    dlon = float(spec["nominal_lon_spacing"]) * 0.3
    dlat = float(spec["nominal_lat_spacing"]) * 0.3
    for row in [rows[0], rows[-1], *rng.sample(rows, 300)]:
        lon = row["LON"] + rng.uniform(-dlon, dlon)
        lat = row["LAT"] + rng.uniform(-dlat, dlat)
        inside = grid.lat_min <= lat <= grid.lat_max and grid.lon_min <= lon <= grid.lon_max
        assert grid.mmi_at(lon, lat) == (row["MMI"] if inside else None)


@pytest.mark.parametrize("name", REAL_GRIDS)
def test_f15_outside_extent_has_no_value(name: str, usgs_fixture: Callable[[str], bytes]) -> None:
    grid = parse_grid(usgs_fixture(name))
    mid_lon = (grid.lon_min + grid.lon_max) / 2
    mid_lat = (grid.lat_min + grid.lat_max) / 2
    assert grid.mmi_at(mid_lon, mid_lat) is not None
    for lon, lat in [
        (grid.lon_min - 0.001, mid_lat),
        (grid.lon_max + 0.001, mid_lat),
        (mid_lon, grid.lat_min - 0.001),
        (mid_lon, grid.lat_max + 0.001),
        (mid_lon + 180, mid_lat),
    ]:
        assert grid.mmi_at(lon, lat) is None


LONS = [10.0, 10.5, 11.0, 11.5]
LATS = [2.0, 1.0, 0.0]


def test_f14_columns_read_by_name() -> None:
    plain = parse_grid(grid_xml(LONS, LATS))
    permuted = parse_grid(grid_xml(LONS, LATS, fields=("PGA", "MMI", "LAT", "LON")))
    for lon, lat, expected in [(10.0, 2.0, 0), (11.4, 0.1, 23), (10.6, 1.4, 11), (11.5, 0.0, 23)]:
        assert plain.mmi_at(lon, lat) == permuted.mmi_at(lon, lat) == expected


@pytest.mark.parametrize("lons", [[179.0, 180.0, 181.0], [179.0, 180.0, -179.0]])
def test_f14_antimeridian(lons: list[float]) -> None:
    grid = parse_grid(grid_xml(lons, LATS))
    assert grid.mmi_at(179.9, 1.0) == 11
    assert grid.mmi_at(-179.9, 1.0) == 11
    assert grid.mmi_at(-179.1, 1.0) == 12
    assert grid.mmi_at(-178.9, 1.0) is None
    assert grid.mmi_at(178.9, 1.0) is None


@pytest.mark.parametrize(
    "data",
    [
        pytest.param(grid_xml(LONS, LATS, skip_last=True), id="missing-row"),
        pytest.param(grid_xml(LONS, list(reversed(LATS))), id="inverted-extent"),
        pytest.param(
            grid_xml(LONS, LATS).replace(b'lat_max="2.0"', b'lat_max="2.5"'), id="first-row-off"
        ),
        pytest.param(
            grid_xml(LONS, LATS).replace(b'lon_max="11.5"', b'lon_max="11.6"'), id="last-row-off"
        ),
        pytest.param(
            grid_xml(LONS, LATS).replace(b"\n</grid_data>", b" 9\n</grid_data>"), id="extra-column"
        ),
        pytest.param(grid_xml(LONS, LATS, mmi=lambda r, c: float("nan")), id="nan"),
        pytest.param(grid_xml(LONS, LATS, fields=("LON", "LAT", "PGA")), id="no-mmi"),
        pytest.param(grid_xml(LONS, LATS, fields=("LON", "LAT", "MMI", "MMI")), id="dup-field"),
        pytest.param(grid_xml([10.0], LATS), id="single-column"),
        pytest.param(
            grid_xml(LONS, LATS, prolog='<!DOCTYPE shakemap_grid [<!ENTITY a "b">]>'), id="entity"
        ),
        pytest.param(grid_xml(LONS, LATS, prolog="<!DOCTYPE shakemap_grid>"), id="dtd"),
        pytest.param(grid_xml([10.0, 10.0], LATS), id="zero-width"),
        pytest.param(b"<html>not a grid</html>", id="other-root"),
        pytest.param(b"not xml", id="not-xml"),
    ],
)
def test_unsupported_grid(data: bytes) -> None:
    with pytest.raises(UnsupportedGrid):
        parse_grid(data)


def test_point_limit() -> None:
    data = grid_xml(LONS, LATS)
    assert isinstance(parse_grid(data, max_points=12), Grid)
    with pytest.raises(UnsupportedGrid, match="pontos"):
        parse_grid(data, max_points=11)


@pytest.mark.parametrize(
    "lons",
    [
        # 11 colunas com passo 0,4902 cruzando 180°, e 0,2831 cruzando 0°
        [
            round(176.8447 + 0.4902 * i - (360 if 176.8447 + 0.4902 * i > 180 else 0), 4)
            for i in range(11)
        ],
        [round(-1.6305 + 0.28314 * i, 4) for i in range(11)],
    ],
)
def test_f15_asset_on_the_edge_is_inside(lons: list[float]) -> None:
    grid = parse_grid(grid_xml(lons, LATS))
    assert grid.mmi_at(lons[0], 1.0) == 10
    assert grid.mmi_at(lons[-1], 1.0) == 20
    assert grid.mmi_at(lons[-1], 0.0) == 30
    assert grid.mmi_at(lons[0], 2.0) == 0
