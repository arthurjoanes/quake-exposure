"""Fatos de uma publicação por área, com resposta limitada para o mapa."""

import math
from dataclasses import dataclass
from typing import Any

from psycopg import sql

from quakeexposure.ingestion.store import Conn


@dataclass(frozen=True)
class Bounds:
    west: float
    south: float
    east: float
    north: float

    @property
    def width(self) -> float:
        return self.east - self.west if self.east >= self.west else 360 + self.east - self.west

    def grid(self, zoom: int, limit: int) -> tuple[int, int]:
        cell = 90 / 2**zoom
        columns = min(max(1, math.ceil(self.width / cell)), math.isqrt(limit))
        rows = min(max(1, math.ceil((self.north - self.south) / cell)), limit // columns)
        return columns, rows


_WHERE = """calculation_id = %(publication)s AND lat BETWEEN %(south)s AND %(north)s
    AND ((%(west)s <= %(east)s AND lon BETWEEN %(west)s AND %(east)s)
         OR (%(west)s > %(east)s AND (lon >= %(west)s OR lon <= %(east)s))
         OR (%(west)s = -180 AND lon = 180)
         OR (%(east)s = 180 AND lon = -180))"""


def asset_json(row: tuple[Any, ...]) -> dict[str, Any]:
    asset_id, lon, lat, currency, value, eligible, qualifying, mmi = row
    return {
        "asset_id": asset_id,
        "lon": lon,
        "lat": lat,
        "currency": currency,
        "insured_value_minor": str(value),
        "eligible": eligible,
        "qualifying": qualifying,
        "mmi": mmi,
    }


def region(
    conn: Conn, publication_id: str, bounds: Bounds, zoom: int, limit: int
) -> dict[str, Any]:
    params: dict[str, Any] = {
        "publication": publication_id,
        "west": bounds.west,
        "south": bounds.south,
        "east": bounds.east,
        "north": bounds.north,
        "limit": limit,
    }
    totals = conn.execute(
        sql.SQL("""SELECT currency, count(*), count(*) FILTER (WHERE eligible),
                  count(*) FILTER (WHERE qualifying), sum(insured_value_minor)::text,
                  coalesce(sum(insured_value_minor) FILTER (WHERE qualifying), 0)::text
           FROM exposure.fact WHERE """)
        + sql.SQL(_WHERE)
        + sql.SQL(" GROUP BY currency ORDER BY currency"),
        params,
    ).fetchall()
    count = sum(row[1] for row in totals)
    result: dict[str, Any] = {
        "publication_id": publication_id,
        "bounds": {
            "west": bounds.west,
            "south": bounds.south,
            "east": bounds.east,
            "north": bounds.north,
        },
        "zoom": zoom,
        "total_count": count,
        "eligible_count": sum(row[2] for row in totals),
        "qualifying_count": sum(row[3] for row in totals),
        "insured_value_minor": {row[0]: row[4] for row in totals},
        "qualifying_value_minor": {row[0]: row[5] for row in totals},
        "assets": [],
        "clusters": [],
    }
    if zoom >= 7 and count <= limit:
        rows = conn.execute(
            sql.SQL("""SELECT asset_id, lon, lat, currency, insured_value_minor::text,
                      eligible, qualifying, mmi FROM exposure.fact WHERE """)
            + sql.SQL(_WHERE)
            + sql.SQL(" ORDER BY asset_id LIMIT %(limit)s"),
            params,
        ).fetchall()
        result["representation"] = "points"
        result["assets"] = [asset_json(row) for row in rows]
        return result

    columns, grid_rows = bounds.grid(zoom, limit)
    params.update(
        columns=columns,
        rows=grid_rows,
        width=max(bounds.width, 1e-12),
        height=max(bounds.north - bounds.south, 1e-12),
    )
    groups = conn.execute(
        sql.SQL("""WITH positions AS (
               SELECT *, CASE WHEN lon IN (-180, 180) AND %(west)s = -180 THEN -180
                              WHEN lon IN (-180, 180) AND %(east)s = 180 THEN 180
                              WHEN %(west)s > %(east)s AND lon < %(west)s
                              THEN lon + 360 ELSE lon END AS unwrapped
               FROM exposure.fact WHERE """)
        + sql.SQL(_WHERE)
        + sql.SQL("""), cells AS (
               SELECT *, least(%(columns)s - 1,
                   floor((unwrapped - %(west)s) / %(width)s * %(columns)s)::int) AS x,
                   least(%(rows)s - 1,
                   floor((lat - %(south)s) / %(height)s * %(rows)s)::int) AS y
               FROM positions)
           SELECT x, y, currency, count(*), count(*) FILTER (WHERE eligible),
                  count(*) FILTER (WHERE qualifying), sum(insured_value_minor)::text,
                  coalesce(sum(insured_value_minor) FILTER (WHERE qualifying), 0)::text,
                  sum(unwrapped), sum(lat)
           FROM cells GROUP BY x, y, currency ORDER BY x, y, currency"""),
        params,
    ).fetchall()
    clusters: dict[tuple[int, int], dict[str, Any]] = {}
    for (
        x,
        y,
        currency,
        n,
        eligible,
        qualifying,
        value,
        qualifying_value,
        lon_sum,
        lat_sum,
    ) in groups:
        cluster = clusters.setdefault(
            (x, y),
            {
                "cluster_id": f"{columns}:{grid_rows}:{x}:{y}",
                "count": 0,
                "eligible_count": 0,
                "qualifying_count": 0,
                "insured_value_minor": {},
                "qualifying_value_minor": {},
                "lon": 0.0,
                "lat": 0.0,
            },
        )
        cluster["count"] += n
        cluster["eligible_count"] += eligible
        cluster["qualifying_count"] += qualifying
        cluster["insured_value_minor"][currency] = value
        cluster["qualifying_value_minor"][currency] = qualifying_value
        cluster["lon"] += lon_sum
        cluster["lat"] += lat_sum
    for cluster in clusters.values():
        cluster["lon"] = (cluster["lon"] / cluster["count"] + 180) % 360 - 180
        cluster["lat"] /= cluster["count"]
    result["representation"] = "clusters"
    result["clusters"] = list(clusters.values())
    return result
