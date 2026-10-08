import copy
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from builders import T, asset, change, vector
from conftest import MANIFEST
from jsonschema import Draft202012Validator

from quakeexposure.domain.exposure import ResultStatus, radius_facts, summarize, without_values
from quakeexposure.domain.money import Currency
from quakeexposure.domain.rule import Mode

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


ASSET_CHANGE = validator("asset-interval-change")
EXPOSURE_RESULT = validator("exposure-result")


def test_asset_interval_change_contract() -> None:
    assert errors(ASSET_CHANGE, change()) == []
    assert errors(ASSET_CHANGE, change(valid_to="2026-10-08T00:00:00.000Z")) == []
    bad_changes: list[dict[str, Any]] = [
        {"insured_value_minor": 100},
        {"insured_value_minor": "1.5"},
        {"currency": "EUR"},
        {"valid_from": "2026-10-06T16:48:22Z"},
        {"subject_token": "not-a-uuid"},
        {"extra": 1},
    ]
    for bad in bad_changes:
        assert errors(ASSET_CHANGE, change(**bad)) != [], bad
    doc = change()
    del doc["subject_token"]
    assert errors(ASSET_CHANGE, doc) != []


def test_exposure_result_contract() -> None:
    radius = vector(mode=Mode.RADIUS_TRIAGE, product_revision_id=None)
    complete = summarize(radius, radius_facts([asset("a", 0, 0, 5, Currency.BRL)], {"a"}), T)
    assert errors(EXPOSURE_RESULT, complete.to_json()) == []
    assert errors(EXPOSURE_RESULT, complete.to_json(published_at=T)) == []
    for status in ResultStatus:
        if status is not ResultStatus.COMPLETE:
            assert errors(EXPOSURE_RESULT, without_values(vector(), status, T).to_json()) == []

    out = complete.to_json()
    for path, bad in [
        (("status",), "NO_MAP"),  # NO_MAP com contagens
        (("qualifying_value_minor", "BRL"), 5),  # número em vez de string
        (("eligible_count",), None),
        (("input_vector", "product_revision_id"), "urn:usgs-product:us:shakemap:x:1"),
    ]:
        doc = copy.deepcopy(out)
        target = doc
        for key in path[:-1]:
            target = target[key]
        target[path[-1]] = bad
        assert errors(EXPOSURE_RESULT, doc) != [], path


def test_first_error_names_the_rule_not_the_value(usgs_json: Callable[[str], Any]) -> None:
    from quakeexposure.contracts import EVENT as LIVE_EVENT
    from quakeexposure.contracts import first_error

    event = copy.deepcopy(usgs_json("feed_all_day")["features"][0])
    del event["properties"]["time"]
    assert first_error(LIVE_EVENT, event) == "properties: required ['time']"
    event = copy.deepcopy(usgs_json("feed_all_day")["features"][0])
    event["properties"]["mag"] = "SEGREDO"
    message = first_error(LIVE_EVENT, event)
    assert message is not None and message.startswith("properties/mag:")
    assert "SEGREDO" not in message
