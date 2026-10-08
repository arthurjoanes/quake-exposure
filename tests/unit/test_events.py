import hashlib
import json
from collections.abc import Callable
from typing import Any

import pytest
from builders import feature, revision

from quakeexposure.domain.events import (
    EventHistory,
    Lifecycle,
    Outcome,
    revision_from_feature,
)


@pytest.mark.parametrize("name", ["feed_all_day", "fdsn_shakemap_minmmi6"])
def test_parses_every_captured_feature(name: str, usgs_json: Callable[[str], Any]) -> None:
    for f in usgs_json(name)["features"]:
        rev = revision_from_feature(f)
        assert rev.provider_event_id in rev.ids
        assert len(rev.revision_id) == len(rev.semantic_sha256) == 64


def test_hashes_follow_the_canonical_definition() -> None:
    rev = revision("us6000u0hy", updated=1_791_392_087_721)
    semantic = json.dumps(
        {
            "event_type": "earthquake",
            "lat": 48.4437,
            "lon": 154.9316,
            "mag": 5.8,
            "status": "reviewed",
            "time": "2026-10-06T16:48:22.347Z",
        },
        separators=(",", ":"),
    )
    assert rev.semantic_sha256 == hashlib.sha256(semantic.encode()).hexdigest()
    ident = json.dumps(
        ["us6000u0hy", "2026-10-07T16:54:47.721Z", rev.semantic_sha256], separators=(",", ":")
    )
    assert rev.revision_id == hashlib.sha256(ident.encode()).hexdigest()


def test_semantic_hash_ignores_number_spelling_and_other_fields() -> None:
    base = revision("us1", updated=1, mag=5.0, lon=0.0)
    assert revision("us1", updated=1, mag=5, lon=-0.0).semantic_sha256 == base.semantic_sha256
    # depth, types e ids não entram no hash semântico
    other = revision("us1", updated=2, mag=5.0, lon=0.0, depth=99.0, types=",dyfi,", ids=["ak1"])
    assert other.semantic_sha256 == base.semantic_sha256
    assert other.revision_id != base.revision_id


def test_accepts_null_and_empty_fields() -> None:
    f = feature("us1", updated=1, mag=None, depth=None, event_type="", types="")
    rev = revision_from_feature(f)
    assert (rev.mag, rev.depth_km, rev.event_type, rev.types) == (None, None, None, frozenset())
    f["geometry"]["coordinates"] = [10.0, 20.0]
    assert revision_from_feature(f).depth_km is None


def test_deleted_revision_ignores_the_unreliable_type() -> None:
    # visto no FDSN real (aka2026tuxiyh): coleção com "earthquake", eventid com ""
    from_collection = revision("ak1", updated=1, status="deleted", event_type="earthquake")
    from_eventid = revision("ak1", updated=1, status="deleted", event_type="")
    assert from_collection.revision_id == from_eventid.revision_id
    assert from_collection.event_type is None
    assert revision("ak1", updated=1, event_type="explosion").event_type == "explosion"


def test_f03_deleted_status_retracts() -> None:
    assert revision("us1", updated=1).lifecycle is Lifecycle.ACTIVE
    assert revision("us1", updated=2, status="deleted").lifecycle is Lifecycle.RETRACTED


def test_f02_out_of_order_revisions() -> None:
    r1, r2, r3 = (revision("us1", updated=t, mag=m) for t, m in [(1, 5.0), (2, 5.2), (3, 5.4)])
    history = EventHistory()
    outcomes = []
    for rev in (r3, r1, r2):
        history, outcome = history.apply(rev)
        outcomes.append(outcome)
    assert outcomes == [Outcome.CURRENT, Outcome.HISTORY, Outcome.HISTORY]
    assert history.current is r3
    assert history.revisions == (r1, r2, r3)


def test_f06_same_version_with_other_content_is_a_conflict() -> None:
    history, _ = EventHistory().apply(revision("us1", updated=10, mag=5.0))
    same = revision("us1", updated=10, mag=5.0)
    changed = revision("us1", updated=10, mag=6.1)
    assert history.apply(same) == (history, Outcome.DUPLICATE)
    assert history.apply(changed) == (history, Outcome.CONFLICT)
    # outro id preferido com o mesmo `updated` também não entra
    assert history.apply(revision("ak1", updated=10, mag=5.0))[1] is Outcome.CONFLICT
