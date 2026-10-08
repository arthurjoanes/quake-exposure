"""Seleção da ShakeMap de um evento a partir do detail.

Sem ShakeMap escolhida, a publicação do modo SHAKEMAP_MMI é NO_MAP.
"""

from collections.abc import Iterable, Mapping
from typing import Any

GRID_PATH = "download/grid.xml"


def latest_detail(details: Iterable[Mapping[str, Any]]) -> Mapping[str, Any]:
    """O detail com maior `updated` é o que vale para a seleção."""
    return max(details, key=lambda d: d["properties"]["updated"])


def select_shakemap(detail: Mapping[str, Any]) -> Mapping[str, Any] | None:
    """Maior `preferredWeight`; empate, o `updateTime` mais recente."""
    products = detail["properties"].get("products", {}).get("shakemap", [])
    live = [p for p in products if p.get("status") != "DELETE"]
    return max(live, key=lambda p: (p["preferredWeight"], p["updateTime"]), default=None)


def product_revision_id(product: Mapping[str, Any]) -> str:
    return (
        f"urn:usgs-product:{product['source']}:shakemap:{product['code']}:{product['updateTime']}"
    )
