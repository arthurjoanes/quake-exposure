"""Tamanho da grade usado na resposta por área."""

import pytest

from quakeexposure.api.regional import Bounds


@pytest.mark.parametrize("limit", [1, 2, 9, 31, 2000])
@pytest.mark.parametrize("zoom", [0, 4, 10, 22])
@pytest.mark.parametrize(
    "bounds",
    [Bounds(-180, -90, 180, 90), Bounds(170, -30, -170, 30), Bounds(0, 0, 0, 0)],
)
def test_region_grid_never_exceeds_response_limit(bounds: Bounds, zoom: int, limit: int) -> None:
    columns, rows = bounds.grid(zoom, limit)
    assert columns > 0 and rows > 0
    assert columns * rows <= limit


def test_region_grid_uses_short_antimeridian_span() -> None:
    assert Bounds(170, -5, -170, 5).width == 20
    assert Bounds(-180, -90, 180, 90).width == 360
