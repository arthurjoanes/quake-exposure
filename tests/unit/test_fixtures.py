"""Os bytes das fixtures batem com o manifesto e, nos grids, com o declarado no detail."""

import base64
import gzip
import hashlib
from collections.abc import Callable
from typing import Any

import pytest
from conftest import FIXTURES, MANIFEST

from quakeexposure.domain.shakemap import GRID_PATH, product_revision_id, select_shakemap

GRIDS = [e for e in MANIFEST if e["kind"] == "grid"]


def test_manifest_has_the_capture_set() -> None:
    names = {e["name"] for e in MANIFEST}
    assert {
        "feed_all_day",
        "fdsn_shakemap_minmmi6",
        "detail_us6000u0hy",
        "grid_us6000u0hy",
        "detail_aka2026tuxgky",
        "grid_aka2026tuxgky",
    } <= names
    # mais um evento achado pelo FDSN minmmi=6
    assert len([n for n in names if n.startswith("detail_")]) == 3


@pytest.mark.parametrize("entry", MANIFEST, ids=lambda e: e["name"])
def test_bytes_match_manifest(entry: dict[str, Any]) -> None:
    path = FIXTURES / entry["path"]
    if not entry["in_git"] and not path.exists():
        pytest.skip("fora do Git: uv run python scripts/fetch_fixtures.py pull")
    body = gzip.decompress(path.read_bytes())
    assert len(body) == entry["bytes"]
    assert hashlib.sha256(body).hexdigest() == entry["sha256"]
    assert entry["http_status"] == 200
    assert entry["fault"] is False and entry["parent_sha256"] is None


@pytest.mark.parametrize("entry", GRIDS, ids=lambda e: e["name"])
def test_grid_matches_declared_in_detail(
    entry: dict[str, Any], usgs_json: Callable[[str], Any]
) -> None:
    assert base64.b64decode(entry["declared_sha256"]).hex() == entry["sha256"]
    assert entry["declared_length"] == entry["bytes"]

    detail = usgs_json(entry["detail"])
    product = select_shakemap(detail)
    assert product is not None
    assert product_revision_id(product) == entry["product"]
    declared = product["contents"][GRID_PATH]
    assert (declared["url"], declared["length"], declared["sha256"]) == (
        entry["url"],
        entry["declared_length"],
        entry["declared_sha256"],
    )
    assert entry["in_git"] == (product["source"] == "us")


def test_only_manifest_files_in_git_folder() -> None:
    in_git = {e["path"] for e in MANIFEST if e["in_git"]}
    on_disk = {p.relative_to(FIXTURES).as_posix() for p in (FIXTURES / "usgs").iterdir()}
    assert on_disk == in_git
    assert all(p.startswith("usgs/") for p in in_git)
    assert all(e["path"].startswith("external/") for e in MANIFEST if not e["in_git"])
