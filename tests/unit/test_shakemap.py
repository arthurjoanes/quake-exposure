from collections.abc import Callable
from typing import Any

from conftest import MANIFEST

from quakeexposure.domain.shakemap import product_revision_id, select_shakemap

DETAILS = [e["name"] for e in MANIFEST if e["kind"] == "detail"]


def detail_with(*products: dict[str, Any]) -> dict[str, Any]:
    return {"properties": {"products": {"shakemap": list(products)}}}


def shakemap(source: str, weight: int, update_time: int, status: str = "UPDATE") -> dict[str, Any]:
    return {
        "source": source,
        "code": f"{source}1",
        "preferredWeight": weight,
        "updateTime": update_time,
        "status": status,
    }


def test_f05_aka2026tuxgky_selects_ak_by_weight(usgs_json: Callable[[str], Any]) -> None:
    detail = usgs_json("detail_aka2026tuxgky")
    products = detail["properties"]["products"]["shakemap"]
    newest = max(products, key=lambda p: p["updateTime"])
    assert newest["source"] == "us"

    chosen = select_shakemap(detail)
    assert chosen is not None
    assert (chosen["source"], chosen["preferredWeight"]) == ("ak", 227)
    assert chosen is not newest


def test_tie_on_weight_takes_newest() -> None:
    old, new = shakemap("us", 100, 1), shakemap("ci", 100, 2)
    assert select_shakemap(detail_with(old, new)) is new


def test_deleted_product_is_ignored() -> None:
    heavy = shakemap("us", 500, 9, status="DELETE")
    light = shakemap("ci", 10, 1)
    assert select_shakemap(detail_with(heavy, light)) is light
    assert select_shakemap(detail_with(heavy)) is None


def test_no_shakemap() -> None:
    assert select_shakemap({"properties": {"products": {"origin": []}}}) is None
    assert select_shakemap({"properties": {}}) is None


def test_product_revision_id_matches_source_urn(usgs_json: Callable[[str], Any]) -> None:
    for name in DETAILS:
        for product in usgs_json(name)["properties"]["products"]["shakemap"]:
            assert product_revision_id(product) == product["id"]
