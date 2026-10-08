import copy
import dataclasses
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import pytest
from builders import asset, grid_xml, vector

from quakeexposure.domain.canonical import sha256_hex
from quakeexposure.domain.events import revision_from_feature
from quakeexposure.domain.exposure import (
    ResultStatus,
    StreamScope,
    mmi_facts,
    radius_facts,
    summarize,
    without_values,
)
from quakeexposure.domain.grid import parse_grid
from quakeexposure.domain.money import Currency
from quakeexposure.domain.rule import DEFAULT_RULE, Mode, Rule
from quakeexposure.domain.shakemap import latest_detail, product_revision_id, select_shakemap

NOW = datetime(2026, 10, 7, 12, 0, tzinfo=UTC)


def test_calculation_id_is_hash_of_the_canonical_vector() -> None:
    v = vector()
    assert v.calculation_id == sha256_hex(v.to_json())
    assert v.to_json()["product_revision_id"].startswith("urn:usgs-product:")


@pytest.mark.parametrize(
    "change",
    [
        {"stream_scope": StreamScope.FAULT},
        {"earthquake_uid": UUID(int=2)},
        {"event_semantic_sha256": "b" * 64},
        {"product_revision_id": "urn:usgs-product:ak:shakemap:a2026tuxgky:1791312073169"},
        {"product_revision_id": None},
        {"tenant_id": "t2"},
        {"portfolio_epoch": 4},
        {"portfolio_source_epoch": 2},
        {"privacy_epoch": 1},
        {"rule_version": Rule(mmi_threshold=7.0).version},
    ],
)
def test_any_input_change_gives_new_calculation_id(change: dict[str, Any]) -> None:
    assert vector(**change).calculation_id != vector().calculation_id


def test_radius_vector_does_not_depend_on_the_map() -> None:
    with pytest.raises(ValueError, match="RADIUS_TRIAGE"):
        vector(mode=Mode.RADIUS_TRIAGE)
    v = vector(mode=Mode.RADIUS_TRIAGE, product_revision_id=None)
    assert v.to_json()["product_revision_id"] == "NONE"


def test_rule_version_is_content_addressed() -> None:
    assert Rule().version == DEFAULT_RULE.version == Rule(mmi_threshold=6).version
    changes: list[dict[str, Any]] = [
        {"radius_m": 100_001},
        {"mmi_threshold": 5.9},
        {"event_types": frozenset({"earthquake", "explosion"})},
        {"product_selection": "latest"},
        {"sampling": "bilinear"},
    ]
    for change in changes:
        assert dataclasses.replace(DEFAULT_RULE, **change).version != DEFAULT_RULE.version


def test_event_type_outside_the_rule() -> None:
    assert DEFAULT_RULE.event_eligible("earthquake")
    assert not DEFAULT_RULE.event_eligible("explosion")
    assert not DEFAULT_RULE.event_eligible(None)


def test_mmi_threshold_is_inclusive() -> None:
    grid = parse_grid(
        grid_xml([0.0, 1.0], [1.0, 0.0], mmi=lambda r, c: [[6.0, 5.9], [7.2, 1.0]][r][c])
    )
    assets = [
        asset("a", 0.0, 1.0, 100, Currency.BRL),  # 6,0
        asset("b", 1.0, 1.0, 200, Currency.BRL),  # 5,9
        asset("c", 0.0, 0.0, 300, Currency.USD),  # 7,2
        asset("d", 5.0, 5.0, 400, Currency.USD),  # fora
    ]
    facts = {f.asset_id: f for f in mmi_facts(assets, grid, DEFAULT_RULE)}
    assert [(f.eligible, f.qualifying, f.mmi) for f in facts.values()] == [
        (True, True, 6.0),
        (True, False, 5.9),
        (True, True, 7.2),
        (False, False, None),
    ]
    result = summarize(vector(), facts.values(), NOW)
    assert (result.eligible_count, result.qualifying_count) == (3, 2)
    assert result.qualifying_value_minor == {Currency.BRL: 100, Currency.USD: 300}


def test_f15_outside_real_grid_is_not_eligible(usgs_fixture: Callable[[str], bytes]) -> None:
    grid = parse_grid(usgs_fixture("grid_us7000tiur"))
    assets = [
        asset("epicentro", 120.5793, -8.307, 1_000, Currency.USD),
        asset("borda", grid.lon_max, grid.lat_min, 10, Currency.BRL),
        asset("leste", grid.lon_max + 0.01, -8.307, 5_000, Currency.USD),
        asset("norte", 120.5793, grid.lat_max + 0.01, 7_000, Currency.BRL),
    ]
    facts = {f.asset_id: f for f in mmi_facts(assets, grid, DEFAULT_RULE)}
    assert facts["epicentro"].eligible and facts["epicentro"].qualifying
    assert facts["borda"].eligible
    assert not facts["leste"].eligible and not facts["norte"].eligible
    result = summarize(vector(), facts.values(), NOW)
    assert result.eligible_count == 2
    assert result.qualifying_value_minor is not None
    assert result.qualifying_value_minor[Currency.USD] == 1_000


def test_radius_facts() -> None:
    assets = [asset("a", 0, 0, 10, Currency.BRL), asset("b", 1, 1, 20, Currency.USD)]
    result = summarize(
        vector(mode=Mode.RADIUS_TRIAGE, product_revision_id=None),
        radius_facts(assets, within={"b"}),
        NOW,
    )
    assert (result.eligible_count, result.qualifying_count) == (2, 1)
    assert result.qualifying_value_minor == {Currency.BRL: 0, Currency.USD: 20}
    assert result.limitations == ("RADIUS_IS_PROXIMITY_NOT_INTENSITY",)


def test_f05_selection_and_map_that_disappears(usgs_json: Callable[[str], Any]) -> None:
    detail = usgs_json("detail_aka2026tuxgky")
    chosen = select_shakemap(detail)
    assert chosen is not None
    assert product_revision_id(chosen) == "urn:usgs-product:ak:shakemap:a2026tuxgky:1791312073169"

    # revisão seguinte do detail sem ShakeMap: vale o detail com maior `updated`
    newer = copy.deepcopy(detail)
    newer["properties"]["updated"] += 1
    del newer["properties"]["products"]["shakemap"]
    assert select_shakemap(latest_detail([detail, newer])) is None

    event = revision_from_feature(newer)
    v = vector(event_semantic_sha256=event.semantic_sha256, product_revision_id=None)
    result = without_values(v, ResultStatus.NO_MAP, NOW)
    out = result.to_json()
    assert out["status"] == "NO_MAP"
    assert out["eligible_count"] is out["qualifying_count"] is out["qualifying_value_minor"] is None
    assert out["input_vector"]["product_revision_id"] == "NONE"
    assert "NO_MAP" in out["limitations"]


def test_complete_cannot_be_published_without_values() -> None:
    with pytest.raises(ValueError):
        without_values(vector(), ResultStatus.COMPLETE, NOW)


def test_f26_result_json_carries_money_as_string() -> None:
    big = 2**53 + 1
    facts = radius_facts(
        [asset("a", 0, 0, big, Currency.BRL), asset("b", 0, 0, big, Currency.BRL)],
        within={"a", "b"},
    )
    out = summarize(vector(mode=Mode.RADIUS_TRIAGE, product_revision_id=None), facts, NOW).to_json(
        published_at=NOW
    )
    assert out["qualifying_value_minor"] == {"BRL": str(2 * big), "USD": "0"}
    assert out["published_at"] == "2026-10-07T12:00:00.000Z"
