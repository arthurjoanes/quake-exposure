"""Seleção da ShakeMap de um evento a partir do detail."""

from collections.abc import Mapping
from typing import Any

GRID_PATH = "download/grid.xml"


def select_shakemap(detail: Mapping[str, Any]) -> Mapping[str, Any] | None:
    """Maior `preferredWeight`; empate, o `updateTime` mais recente."""
    products = detail["properties"].get("products", {}).get("shakemap", [])
    live = [p for p in products if p.get("status") != "DELETE"]
    return max(live, key=lambda p: (p["preferredWeight"], p["updateTime"]), default=None)


def product_revision_id(product: Mapping[str, Any]) -> str:
    return (
        f"urn:usgs-product:{product['source']}:shakemap:{product['code']}:{product['updateTime']}"
    )
