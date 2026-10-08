import copy
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from conftest import MANIFEST
from jsonschema import Draft202012Validator

CONTRACTS = Path(__file__).parents[2] / "contracts"


def validator(name: str) -> Draft202012Validator:
    schema = json.loads((CONTRACTS / f"{name}.schema.json").read_text("utf-8"))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


EVENT = validator("event")
SHAKEMAP = validator("shakemap")


def errors(v: Draft202012Validator, doc: Any) -> list[str]:
    return [e.message for e in v.iter_errors(doc)]


@pytest.mark.parametrize("name", ["feed_all_day", "fdsn_shakemap_minmmi6"])
def test_collection_features_follow_event_contract(
    name: str, usgs_json: Callable[[str], Any]
) -> None:
    features = usgs_json(name)["features"]
    assert features
    for feature in features:
        assert errors(EVENT, feature) == [], feature["id"]


@pytest.mark.parametrize("name", [e["name"] for e in MANIFEST if e["kind"] == "detail"])
def test_detail_follows_contracts(name: str, usgs_json: Callable[[str], Any]) -> None:
    detail = usgs_json(name)
    assert errors(EVENT, detail) == []
    products = detail["properties"]["products"]["shakemap"]
    assert products
    for product in products:
        assert errors(SHAKEMAP, product) == [], product["id"]


def test_event_accepts_null_or_empty_fields(usgs_json: Callable[[str], Any]) -> None:
    event = copy.deepcopy(usgs_json("feed_all_day")["features"][0])
    event["properties"].update(mag=None, type="", types="", status="deleted")
    event["geometry"]["coordinates"][2] = None
    assert errors(EVENT, event) == []
    event["properties"].update(type=None, types=None)
    event["geometry"]["coordinates"] = event["geometry"]["coordinates"][:2]
    assert errors(EVENT, event) == []


@pytest.mark.parametrize(
    "change",
    [
        lambda e: e["properties"].pop("ids"),
        lambda e: e["properties"].update(ids="us1"),
        lambda e: e["properties"].update(mag="5.1"),
        lambda e: e["properties"].update(status="AUTOMATIC"),
        lambda e: e["properties"].update(updated="2026-10-07"),
        lambda e: e["geometry"].update(coordinates=[200.0, 10.0, 5.0]),
        lambda e: e.update(geometry=None),
    ],
)
def test_event_rejects(
    change: Callable[[dict[str, Any]], Any], usgs_json: Callable[[str], Any]
) -> None:
    event = copy.deepcopy(usgs_json("feed_all_day")["features"][0])
    change(event)
    assert errors(EVENT, event) != []


@pytest.mark.parametrize(
    "change",
    [
        lambda p: p["contents"]["download/grid.xml"].pop("sha256"),
        lambda p: p["contents"]["download/grid.xml"].update(url="http://example.com/grid.xml"),
        lambda p: p.pop("preferredWeight"),
        lambda p: p.update(type="dyfi"),
    ],
)
def test_shakemap_rejects(
    change: Callable[[dict[str, Any]], Any], usgs_json: Callable[[str], Any]
) -> None:
    product = copy.deepcopy(usgs_json("detail_us6000u0hy")["properties"]["products"]["shakemap"][0])
    change(product)
    assert errors(SHAKEMAP, product) != []
