"""Leitura do grid.xml da ShakeMap e amostragem do MMI no ponto do ativo.

Colunas lidas pelo nome em `grid_field`. O passo vem da extensão, não do espaçamento nominal
(que vem arredondado). Fora da extensão não há valor: nada de extrapolar nem de MMI zero.
"""

import math
from array import array
from dataclasses import dataclass
from xml.etree.ElementTree import Element, ParseError

from defusedxml import DefusedXmlException
from defusedxml.ElementTree import fromstring

MAX_POINTS = 2_000_000
# os valores do grid vêm com 4 casas
COORD_TOLERANCE = 1e-4


class UnsupportedGrid(Exception):
    pass


@dataclass(frozen=True)
class Grid:
    lon_min: float
    lat_min: float
    lon_max: float  # sempre >= lon_min; acima de 180 quando cruza o antimeridiano
    lat_max: float
    nlon: int
    nlat: int
    mmi: array[float]  # linha a linha, a primeira em lat_max, lon crescente

    def mmi_at(self, lon: float, lat: float) -> float | None:
        """MMI do nó mais próximo; None fora da extensão."""
        x = self.lon_min + (lon - self.lon_min) % 360.0
        if not (self.lat_min <= lat <= self.lat_max and x <= self.lon_max):
            return None
        col = math.floor((x - self.lon_min) / self._lon_step + 0.5)
        row = math.floor((self.lat_max - lat) / self._lat_step + 0.5)
        return self.mmi[row * self.nlon + col]

    @property
    def _lon_step(self) -> float:
        return (self.lon_max - self.lon_min) / (self.nlon - 1)

    @property
    def _lat_step(self) -> float:
        return (self.lat_max - self.lat_min) / (self.nlat - 1)


def parse_grid(data: bytes, *, max_points: int = MAX_POINTS) -> Grid:
    try:
        root = fromstring(data, forbid_dtd=True)
    except (ParseError, DefusedXmlException) as exc:
        raise UnsupportedGrid(f"XML inválido: {exc!r}") from exc
    if _local(root) != "shakemap_grid":
        raise UnsupportedGrid(f"raiz inesperada: {root.tag}")

    spec = _only(root, "grid_specification")
    try:
        lon_min, lat_min, lon_max, lat_max = (
            float(spec.attrib[k]) for k in ("lon_min", "lat_min", "lon_max", "lat_max")
        )
        nlon, nlat = int(spec.attrib["nlon"]), int(spec.attrib["nlat"])
    except (KeyError, ValueError) as exc:
        raise UnsupportedGrid(f"grid_specification inválido: {exc!r}") from exc
    if not all(map(math.isfinite, (lon_min, lat_min, lon_max, lat_max))):
        raise UnsupportedGrid("extensão com valor não finito")
    if nlon < 2 or nlat < 2 or lat_max <= lat_min:
        raise UnsupportedGrid(f"extensão degenerada: nlon={nlon} nlat={nlat}")
    if nlon * nlat > max_points:
        raise UnsupportedGrid(f"{nlon * nlat} pontos, acima de {max_points}")
    if lon_max <= lon_min:
        lon_max += 360.0

    field_elements = [f for f in root if _local(f) == "grid_field"]
    fields = {f.attrib.get("name", ""): f.attrib.get("index", "") for f in field_elements}
    width = len(field_elements)
    try:
        i_lon, i_lat, i_mmi = (int(fields[name]) - 1 for name in ("LON", "LAT", "MMI"))
    except (KeyError, ValueError) as exc:
        raise UnsupportedGrid(f"grid_field sem LON, LAT ou MMI: {fields}") from exc
    if len(fields) != width or not all(0 <= i < width for i in (i_lon, i_lat, i_mmi)):
        raise UnsupportedGrid(f"grid_field inconsistente: {fields}")

    rows = (_only(root, "grid_data").text or "").splitlines()
    rows = [r for r in rows if r.strip()]
    if len(rows) != nlon * nlat:
        raise UnsupportedGrid(f"{len(rows)} linhas, esperado {nlon * nlat}")

    mmi = array("d")
    first = last = (math.nan, math.nan)
    for n, row in enumerate(rows):
        values = row.split()
        if len(values) != width:
            raise UnsupportedGrid(f"linha {n + 1} com {len(values)} colunas, esperado {width}")
        try:
            lon, lat, value = float(values[i_lon]), float(values[i_lat]), float(values[i_mmi])
        except ValueError as exc:
            raise UnsupportedGrid(f"linha {n + 1}: {exc}") from exc
        if not (math.isfinite(lon) and math.isfinite(lat) and math.isfinite(value)):
            raise UnsupportedGrid(f"linha {n + 1} com valor não finito")
        if n == 0:
            first = (lon, lat)
        last = (lon, lat)
        mmi.append(value)

    if not (_same_lon(first[0], lon_min) and abs(first[1] - lat_max) <= COORD_TOLERANCE):
        raise UnsupportedGrid(f"primeira linha em {first}, esperado ({lon_min}, {lat_max})")
    if not (_same_lon(last[0], lon_max) and abs(last[1] - lat_min) <= COORD_TOLERANCE):
        raise UnsupportedGrid(f"última linha em {last}, esperado ({lon_max}, {lat_min})")
    return Grid(lon_min, lat_min, lon_max, lat_max, nlon, nlat, mmi)


def _local(element: Element) -> str:
    return element.tag.rpartition("}")[2]


def _only(root: Element, name: str) -> Element:
    found = [e for e in root if _local(e) == name]
    if len(found) != 1:
        raise UnsupportedGrid(f"esperado um {name}, achei {len(found)}")
    return found[0]


def _same_lon(a: float, b: float) -> bool:
    return abs((a - b + 180.0) % 360.0 - 180.0) <= COORD_TOLERANCE
