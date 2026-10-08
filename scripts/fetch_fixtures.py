"""Fixtures reais do USGS e o manifesto delas.

    uv run python scripts/fetch_fixtures.py capture   # baixa tudo de novo e reescreve o manifesto
    uv run python scripts/fetch_fixtures.py pull      # baixa os grids que não vão para o Git

Vão para o Git: feed, detail, resposta do FDSN e grids de produto `source=us`, em gzip.
Grids de outras redes ficam em `tests/fixtures/external/` (fora do Git), só com URL e sha256
no manifesto. Se um deles sumir da fonte (404/410), o `pull` marca `gone_at` no manifesto.
"""

import argparse
import gzip
import hashlib
import json
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from quakeexposure.domain.shakemap import GRID_PATH, product_revision_id, select_shakemap
from quakeexposure.ingestion.http import (
    FDSN_READ_TIMEOUT,
    READ_TIMEOUT,
    Fetched,
    HttpStatusError,
    UsgsClient,
)

FIXTURES = Path(__file__).resolve().parent.parent / "tests" / "fixtures"
MANIFEST = FIXTURES / "manifest.json"
FEED_URL = "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/all_day.geojson"
DETAIL_URL = "https://earthquake.usgs.gov/earthquakes/feed/v1.0/detail/{}.geojson"
FDSN_QUERY_URL = "https://earthquake.usgs.gov/fdsnws/event/1/query"
NAMED_EVENTS = ("us6000u0hy", "aka2026tuxgky")

log = logging.getLogger("fetch_fixtures")


def iso(dt: datetime) -> str:
    return dt.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def store(rel_path: str, body: bytes) -> None:
    path = FIXTURES / rel_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(gzip.compress(body, mtime=0))


def entry(
    name: str,
    kind: str,
    fetched: Fetched,
    rel_path: str,
    *,
    in_git: bool = True,
    grid: dict[str, Any] | None = None,
    product: str | None = None,
    detail: str | None = None,
) -> dict[str, Any]:
    store(rel_path, fetched.body)
    return {
        "name": name,
        "kind": kind,
        "url": fetched.url,
        "fetched_at": iso(fetched.fetched_at),
        "http_status": fetched.status,
        "bytes": len(fetched.body),
        "sha256": fetched.sha256,
        "declared_length": grid["length"] if grid else None,
        "declared_sha256": grid["sha256"] if grid else None,
        "detail": detail,
        "product": product,
        "path": rel_path,
        "in_git": in_git,
        "fault": False,
        "parent_sha256": None,
        "gone_at": None,
    }


def capture(usgs: UsgsClient) -> list[dict[str, Any]]:
    feed = usgs.get_json(FEED_URL)
    entries = [entry("feed_all_day", "feed", feed, "usgs/feed_all_day.geojson.gz")]

    start = feed.fetched_at - timedelta(days=90)
    query = usgs.get_json(
        FDSN_QUERY_URL,
        params={
            "format": "geojson",
            "producttype": "shakemap",
            "minmmi": "6",
            "starttime": start.strftime("%Y-%m-%dT%H:%M:%S"),
            "orderby": "time",
        },
        read_timeout=FDSN_READ_TIMEOUT,
    )
    entries.append(entry("fdsn_shakemap_minmmi6", "fdsn", query, "usgs/fdsn_minmmi6.geojson.gz"))
    # o evento mais recente da rede `us` que ainda não está na lista
    picked = next(
        f
        for f in json.loads(query.body)["features"]
        if f["properties"]["net"] == "us" and f["id"] not in NAMED_EVENTS
    )
    log.info("evento escolhido pelo FDSN minmmi=6: %s", picked["id"])

    targets = [(e, DETAIL_URL.format(e)) for e in NAMED_EVENTS]
    targets.append((picked["id"], picked["properties"]["detail"]))
    for event_id, url in targets:
        detail = usgs.get_json(
            url, read_timeout=FDSN_READ_TIMEOUT if "/fdsnws/" in url else READ_TIMEOUT
        )
        name = f"detail_{event_id}"
        entries.append(entry(name, "detail", detail, f"usgs/{name}.geojson.gz"))

        product = select_shakemap(json.loads(detail.body))
        grid = product["contents"].get(GRID_PATH) if product else None
        if product is None or grid is None:
            log.warning("%s: sem ShakeMap com grid.xml", event_id)
            continue
        in_git = product["source"] == "us"
        file_name = f"grid_{event_id}_{product['source']}_{product['updateTime']}.xml.gz"
        fetched = usgs.get_product(
            grid["url"], declared_length=grid["length"], declared_sha256=grid["sha256"]
        )
        entries.append(
            entry(
                f"grid_{event_id}",
                "grid",
                fetched,
                ("usgs/" if in_git else "external/") + file_name,
                in_git=in_git,
                grid=grid,
                product=product_revision_id(product),
                detail=name,
            )
        )
    return entries


def pull(usgs: UsgsClient, entries: list[dict[str, Any]]) -> None:
    for e in entries:
        if e["in_git"] or e["gone_at"]:
            continue
        path = FIXTURES / e["path"]
        if (
            path.exists()
            and hashlib.sha256(gzip.decompress(path.read_bytes())).hexdigest() == e["sha256"]
        ):
            continue
        try:
            fetched = usgs.get_product(
                e["url"], declared_length=e["declared_length"], declared_sha256=e["declared_sha256"]
            )
        except HttpStatusError as exc:
            if exc.status not in (404, 410):
                raise
            e["gone_at"] = iso(datetime.now(UTC))
            log.warning("%s: não está mais disponível (HTTP %d)", e["name"], exc.status)
            continue
        store(e["path"], fetched.body)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["capture", "pull"])
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)

    with UsgsClient() as usgs:
        if args.command == "capture":
            entries = capture(usgs)
            kept = {e["path"] for e in entries}
            for old in FIXTURES.glob("*/*.gz"):
                if old.relative_to(FIXTURES).as_posix() not in kept:
                    old.unlink()
        else:
            entries = json.loads(MANIFEST.read_text("utf-8"))["fixtures"]
            pull(usgs, entries)
    MANIFEST.write_text(
        json.dumps({"fixtures": entries}, indent=2, ensure_ascii=False) + "\n", "utf-8"
    )


if __name__ == "__main__":
    main()
