"""Snapshot público REPLAY: fatos calculados do domínio ou respostas da API local.

    uv run python scripts/export_demo.py --commit <sha-40>
    uv run python scripts/export_demo.py --commit <sha-40> --api-base http://127.0.0.1:8000

Sem --api-base, usa capturas versionadas, a carteira seed 42 e uma correção retroativa.
Este caminho calcula MMI; o raio depende do PostGIS e só é exportado pela API.
Não acessa o USGS nem exporta dados de contato, identidade ou payloads do CDC.
"""

import argparse
import gzip
import hashlib
import json
import re
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from uuid import NAMESPACE_URL, uuid5

import httpx

from quakeexposure.domain.canonical import iso_ms
from quakeexposure.domain.events import revision_from_feature
from quakeexposure.domain.exposure import InputVector, StreamScope, mmi_facts, summarize
from quakeexposure.domain.grid import Grid, parse_grid
from quakeexposure.domain.portfolio import AssetVersion, assets_as_of
from quakeexposure.domain.rule import DEFAULT_RULE, Mode
from quakeexposure.domain.shakemap import product_revision_id, select_shakemap
from quakeexposure.portfolio.synthetic import TENANTS, generate

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures"
DEFAULT_EVENTS = ("us7000tiur", "us6000u0hy")
ASSET_KEYS = (
    "asset_id",
    "lon",
    "lat",
    "currency",
    "insured_value_minor",
    "eligible",
    "qualifying",
    "mmi",
)
RESULT_KEYS = (
    "calculation_id",
    "input_vector",
    "status",
    "eligible_count",
    "qualifying_count",
    "qualifying_value_minor",
    "calculated_at",
    "published_at",
    "limitations",
)
VECTOR_KEYS = (
    "stream_scope",
    "earthquake_uid",
    "event_semantic_sha256",
    "product_revision_id",
    "tenant_id",
    "portfolio_epoch",
    "portfolio_source_epoch",
    "mode",
    "privacy_epoch",
    "rule_version",
)
EVENT_KEYS = (
    "earthquake_uid",
    "preferred_id",
    "stream_scope",
    "lifecycle",
    "event_time",
    "lon",
    "lat",
    "depth_km",
    "mag",
    "event_type",
    "source_updated_at",
    "product_revision_id",
    "revisions",
)
REVISION_KEYS = (
    "revision_id",
    "provider_event_id",
    "source_updated_at",
    "mag",
    "lon",
    "lat",
    "status",
    "event_type",
)
SOURCE_KEYS = ("last_ok_age_s", "stale", "last_error")
CDC_KEYS = (
    "connector_state",
    "task_state",
    "pending_transactions",
    "retained_wal_bytes",
    "stale",
)


def pick(value: dict[str, Any], keys: tuple[str, ...]) -> dict[str, Any]:
    return {key: value[key] for key in keys}


def result(value: dict[str, Any]) -> dict[str, Any]:
    clean = pick(value, RESULT_KEYS)
    clean["input_vector"] = pick(value["input_vector"], VECTOR_KEYS)
    if clean["input_vector"]["stream_scope"] != "REPLAY":
        raise ValueError("A exportação pública aceita somente REPLAY")
    return clean


def current(value: dict[str, Any]) -> dict[str, Any]:
    return {
        "publication_id": value["publication_id"],
        "generation": value["generation"],
        "result": result(value["result"]),
    }


def source_tree_hash() -> str:
    paths = [
        Path(__file__).resolve(),
        ROOT / "src/quakeexposure/portfolio/synthetic.py",
        *sorted((ROOT / "src/quakeexposure/domain").glob("*.py")),
    ]
    content = b"".join(
        str(path.relative_to(ROOT)).replace("\\", "/").encode() + b"\0" + path.read_bytes()
        for path in sorted(paths)
    )
    return hashlib.sha256(content).hexdigest()


def new_snapshot(commit: str, at: datetime) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "metadata": {
            "snapshot_at": iso_ms(at),
            "source_commit": commit,
            "source_tree_sha256": source_tree_hash(),
            "fixture_manifest_sha256": hashlib.sha256(
                (FIXTURES / "manifest.json").read_bytes()
            ).hexdigest(),
            "scope": "REPLAY",
            "portfolio": "synthetic",
            "seed": 42,
            "assets": 10_000,
            "available_modes": [],
            "default_selection": {},
            "scenario": None,
        },
        "tenants": [],
        "events": [],
        "revisions": {},
        "status": {},
        "operations": {},
        "current": [],
        "publications": {},
    }


def validate_publication(publication: dict[str, Any]) -> None:
    """Confere contagens e valores com os fatos, sem float monetário."""
    r = publication["current"]["result"]
    if r["status"] != "COMPLETE":
        return
    assets = publication["assets"]
    if len({asset["asset_id"] for asset in assets}) != len(assets):
        raise ValueError("Ativo duplicado no snapshot")
    if sum(asset["eligible"] for asset in assets) != r["eligible_count"]:
        raise ValueError("Contagem de elegíveis divergente")
    if sum(asset["qualifying"] for asset in assets) != r["qualifying_count"]:
        raise ValueError("Contagem de classificados divergente")
    for currency in ("BRL", "USD"):
        total = sum(
            int(asset["insured_value_minor"])
            for asset in assets
            if asset["currency"] == currency and asset["qualifying"]
        )
        if str(total) != r["qualifying_value_minor"][currency]:
            raise ValueError(f"Total {currency} divergente")
    if any(asset["qualifying"] and not asset["eligible"] for asset in assets):
        raise ValueError("Classificado fora dos elegíveis")


def compare(before: dict[str, Any] | None, after: dict[str, Any]) -> dict[str, Any]:
    old = {a["asset_id"]: a for a in before["assets"]} if before else {}
    new = {a["asset_id"]: a for a in after["assets"]}
    changes = (
        [
            {
                "asset_id": asset_id,
                "change": "added"
                if asset_id not in old
                else "removed"
                if asset_id not in new
                else "changed",
                "before": old.get(asset_id),
                "after": new.get(asset_id),
            }
            for asset_id in sorted(old.keys() | new.keys())
            if old.get(asset_id) != new.get(asset_id)
        ]
        if before
        else []
    )
    return {
        "publication_id": after["current"]["publication_id"],
        "previous_publication_id": before["current"]["publication_id"] if before else None,
        "previous_available": before is not None,
        "unavailable_reason": None if before else "first_publication",
        "causes": ["epoch da carteira"] if before else ["primeira publicação"],
        "before": before["current"] if before else None,
        "after": after["current"],
        "total_changes": len(changes),
        "offset": 0,
        "limit": 2000,
        "changes": changes,
    }


def diff_from_comparison(comparison: dict[str, Any]) -> dict[str, Any]:
    entered: list[dict[str, Any]] = []
    left: list[dict[str, Any]] = []
    for change in comparison["changes"]:
        before, after = change["before"], change["after"]
        if bool(before and before["qualifying"]) == bool(after and after["qualifying"]):
            continue
        asset = after or before
        item = {
            "asset_id": change["asset_id"],
            "currency": asset["currency"],
            "insured_value_minor": asset["insured_value_minor"],
            "mmi_before": before["mmi"] if before else None,
            "mmi_after": after["mmi"] if after else None,
        }
        (entered if after and after["qualifying"] else left).append(item)
    return {
        "publication_id": comparison["publication_id"],
        "previous_publication_id": comparison["previous_publication_id"],
        "causes": comparison["causes"],
        "entered": entered,
        "left": left,
    }


def choose_scenario(snapshot: dict[str, Any], records: dict[str, Any]) -> None:
    snapshot["metadata"]["default_selection"] = {
        "tenant": "operadora-b",
        "event": snapshot["events"][0]["earthquake_uid"],
        "mode": "SHAKEMAP_MMI",
    }
    candidates = [
        record
        for record in records.values()
        if record["comparison"]["previous_available"] and record["comparison"]["total_changes"] > 0
    ]
    if candidates:
        selected = next(
            (
                item
                for item in candidates
                if item["current"]["result"]["input_vector"]["tenant_id"] == "operadora-b"
            ),
            candidates[0],
        )
        comp, vector = selected["comparison"], selected["current"]["result"]["input_vector"]
        snapshot["metadata"]["scenario"] = {
            "title": "Mudanças de posição da carteira",
            "tenant": vector["tenant_id"],
            "event": vector["earthquake_uid"],
            "mode": vector["mode"],
            "publication_id": comp["publication_id"],
            "previous_publication_id": comp["previous_publication_id"],
            "changes": comp["total_changes"],
        }


def fixture_snapshot(commit: str, at: datetime) -> tuple[dict[str, Any], dict[str, Any]]:
    snapshot = new_snapshot(commit, at)
    snapshot["metadata"].update(
        {
            "computation": "domain-fixtures",
            "pipeline_executed": False,
            "available_modes": ["SHAKEMAP_MMI"],
            "portfolio_epochs": [1, 2, 3],
            "moved_share": 0.05,
            "movement_effective_at": "2026-09-25T00:00:00.000Z",
        }
    )
    snapshot["tenants"] = [{"tenant_id": t, "name": name} for t, name, _, _ in TENANTS]
    entries = json.loads((FIXTURES / "manifest.json").read_text("utf-8"))["fixtures"]

    def body(name: str) -> bytes:
        entry = next(e for e in entries if e["name"] == name and not e["fault"])
        data = gzip.decompress((FIXTURES / entry["path"]).read_bytes())
        if hashlib.sha256(data).hexdigest() != entry["sha256"]:
            raise ValueError(f"Hash inválido: {name}")
        return data

    versions = [
        AssetVersion(
            row.tenant_id,
            row.asset_interval_id,
            row.asset_id,
            row.row_version,
            row.known_epoch,
            row.valid_from,
            row.valid_to,
            row.lon,
            row.lat,
            row.insured_value_minor,
            row.currency,
            row.is_deleted,
        )
        for row in generate()
    ]
    flores_detail = json.loads(body("detail_us7000tiur"))
    flores_revision = revision_from_feature(flores_detail)
    flores_grid = parse_grid(body("grid_us7000tiur"))
    correction = portfolio_correction(versions, flores_grid, flores_revision.time)
    versions.append(correction)
    old = next(v for v in versions if v.asset_id == correction.asset_id and v.known_epoch == 1)
    snapshot["metadata"]["correction"] = {
        "tenant": correction.tenant_id,
        "asset_id": correction.asset_id,
        "asset_interval_id": correction.asset_interval_id,
        "known_epoch": correction.known_epoch,
        "valid_from": iso_ms(correction.valid_from),
        "before": {"lon": old.lon, "lat": old.lat, "mmi": flores_grid.mmi_at(old.lon, old.lat)},
        "after": {
            "lon": correction.lon,
            "lat": correction.lat,
            "mmi": flores_grid.mmi_at(correction.lon, correction.lat),
        },
        "currency": correction.currency,
        "insured_value_minor": str(correction.insured_value_minor),
    }
    records: dict[str, Any] = {}
    for provider_id in DEFAULT_EVENTS:
        detail = json.loads(body(f"detail_{provider_id}"))
        revision = revision_from_feature(detail)
        product = select_shakemap(detail)
        if product is None:
            raise ValueError(f"Captura sem ShakeMap: {provider_id}")
        product_id = product_revision_id(product)
        grid_entry = next(e for e in entries if e["name"] == f"grid_{provider_id}")
        if grid_entry["product"] != product_id:
            raise ValueError(f"Grid diferente da ShakeMap selecionada: {provider_id}")
        grid = parse_grid(body(f"grid_{provider_id}"))
        uid = uuid5(NAMESPACE_URL, f"quake-exposure/REPLAY/{provider_id}")
        event = {
            "earthquake_uid": str(uid),
            "preferred_id": provider_id,
            "stream_scope": "REPLAY",
            "lifecycle": revision.lifecycle,
            "event_time": iso_ms(revision.time),
            "lon": revision.lon,
            "lat": revision.lat,
            "depth_km": revision.depth_km,
            "mag": revision.mag,
            "event_type": revision.event_type,
            "source_updated_at": iso_ms(revision.source_updated_at),
            "product_revision_id": product_id,
            "revisions": 1,
        }
        snapshot["events"].append(event)
        snapshot["revisions"][str(uid)] = [
            {
                "revision_id": revision.revision_id,
                "provider_event_id": provider_id,
                "source_updated_at": iso_ms(revision.source_updated_at),
                "mag": revision.mag,
                "lon": revision.lon,
                "lat": revision.lat,
                "status": revision.status,
                "event_type": revision.event_type,
            }
        ]
        for tenant_id, _, _, _ in TENANTS:
            previous = None
            for epoch in (1, 2, 3):
                assets = assets_as_of(versions, tenant_id=tenant_id, at=revision.time, epoch=epoch)
                facts = mmi_facts(assets, grid, DEFAULT_RULE)
                vector = InputVector(
                    StreamScope.REPLAY,
                    uid,
                    revision.semantic_sha256,
                    product_id,
                    tenant_id,
                    epoch,
                    1,
                    Mode.SHAKEMAP_MMI,
                    0,
                    DEFAULT_RULE.version,
                )
                summary = summarize(vector, facts, at).to_json(published_at=at)
                positions = {a.asset_id: a for a in assets}
                clean_assets = [
                    {
                        "asset_id": fact.asset_id,
                        "lon": positions[fact.asset_id].lon,
                        "lat": positions[fact.asset_id].lat,
                        "currency": fact.currency,
                        "insured_value_minor": str(fact.insured_value_minor),
                        "eligible": fact.eligible,
                        "qualifying": fact.qualifying,
                        "mmi": fact.mmi,
                    }
                    for fact in facts
                ]
                publication: dict[str, Any] = {
                    "current": {
                        "publication_id": vector.calculation_id,
                        "generation": epoch,
                        "result": summary,
                    },
                    "assets": clean_assets,
                }
                publication["comparison"] = compare(previous, publication)
                publication["diff"] = diff_from_comparison(publication["comparison"])
                validate_publication(publication)
                records[vector.calculation_id] = publication
                snapshot["publications"][vector.calculation_id] = {
                    "tenant": tenant_id,
                    "file": f"{vector.calculation_id}.json",
                }
                if epoch == 3:
                    snapshot["current"].append(publication["current"])
                previous = publication
    # Não existem coletor ou Kafka no cálculo offline. Campos nulos são exibidos como ausentes.
    sources = {
        key: {"last_ok_age_s": None, "stale": True, "last_error": None}
        for key in ("feed", "incremental")
    }
    cdc = {
        "connector_state": "NOT_CONNECTED",
        "task_state": "NOT_CONNECTED",
        "pending_transactions": 0,
        "retained_wal_bytes": None,
        "stale": True,
    }
    snapshot["status"] = {
        "sources": sources,
        "cdc": cdc,
        "jobs_pending": 0,
        "jobs_failed": 0,
        "publications_outdated": 0,
        "generated_at": iso_ms(at),
    }
    for tenant in snapshot["tenants"]:
        selected = [
            p
            for p in snapshot["current"]
            if p["result"]["input_vector"]["tenant_id"] == tenant["tenant_id"]
        ]
        snapshot["operations"][tenant["tenant_id"]] = {
            "generated_at": iso_ms(at),
            "tenant_id": tenant["tenant_id"],
            "cdc": cdc,
            "jobs": {"pending": 0, "leased": 0, "failed": 0, "outdated": 0},
            "epochs": {"portfolio": 3, "source": 1, "privacy": 0},
            "publications": [
                {
                    "publication_id": p["publication_id"],
                    "earthquake_uid": p["result"]["input_vector"]["earthquake_uid"],
                    "stream_scope": "REPLAY",
                    "mode": "SHAKEMAP_MMI",
                    "generation": p["generation"],
                    "status": p["result"]["status"],
                    "published_at": iso_ms(at),
                    "calculated_at": iso_ms(at),
                    "publication_delay_s": None,
                    "calculation_duration_s": None,
                }
                for p in selected
            ],
            "last_publication_at": iso_ms(at),
            "sources": sources,
        }
    choose_scenario(snapshot, records)
    return snapshot, records


def portfolio_correction(
    versions: list[AssetVersion], grid: Grid, event_at: datetime
) -> AssetVersion:
    """Corrige um ponto da carteira fictícia mantendo valor, moeda e vigência original."""
    candidates = assets_as_of(versions, tenant_id="operadora-b", at=event_at, epoch=2)
    previous = next(
        asset
        for asset in candidates
        if asset.valid_to is None
        and (mmi := grid.mmi_at(asset.lon, asset.lat)) is not None
        and not DEFAULT_RULE.mmi_qualifies(mmi)
    )
    index = max(range(len(grid.mmi)), key=lambda point: grid.mmi[point])
    row, column = divmod(index, grid.nlon)
    lon = grid.lon_min + column * (grid.lon_max - grid.lon_min) / (grid.nlon - 1)
    lat = grid.lat_max - row * (grid.lat_max - grid.lat_min) / (grid.nlat - 1)
    if not DEFAULT_RULE.mmi_qualifies(grid.mmi[index]):
        raise ValueError("Grid sem ponto classificado para a correção demonstrada")
    return replace(previous, row_version=previous.row_version + 1, known_epoch=3, lon=lon, lat=lat)


def api_snapshot(commit: str, at: datetime, base_url: str) -> tuple[dict[str, Any], dict[str, Any]]:
    parsed = urlsplit(base_url)
    if (
        parsed.scheme != "http"
        or parsed.hostname not in ("127.0.0.1", "localhost")
        or parsed.username
    ):
        raise ValueError("--api-base precisa ser a API HTTP local")
    snapshot = new_snapshot(commit, at)
    snapshot["metadata"].update(
        {
            "computation": "local-api",
            "pipeline_executed": True,
            "available_modes": [str(m) for m in Mode],
        }
    )
    records: dict[str, Any] = {}
    with httpx.Client(base_url=base_url, timeout=60, trust_env=False) as client:

        def read(path: str, **params: Any) -> Any:
            response = client.get(path, params=params)
            if response.status_code == 404:
                return None
            response.raise_for_status()
            return response.json()

        snapshot["tenants"] = [pick(t, ("tenant_id", "name")) for t in read("/api/tenants")]
        snapshot["events"] = [
            pick(e, EVENT_KEYS)
            for e in read("/api/events", scope="REPLAY")
            if e["preferred_id"] in DEFAULT_EVENTS
        ]
        status = read("/api/status")
        snapshot["status"] = {
            "sources": {k: pick(v, SOURCE_KEYS) for k, v in status["sources"].items()},
            "cdc": pick(status["cdc"], CDC_KEYS),
            **pick(
                status, ("jobs_pending", "jobs_failed", "publications_outdated", "generated_at")
            ),
        }
        for event in snapshot["events"]:
            uid = event["earthquake_uid"]
            snapshot["revisions"][uid] = [
                pick(r, REVISION_KEYS) for r in read(f"/api/events/{uid}/revisions", scope="REPLAY")
            ]
            for tenant in snapshot["tenants"]:
                tenant_id = tenant["tenant_id"]
                for mode in Mode:
                    pub = read(
                        "/api/publications/current",
                        tenant=tenant_id,
                        earthquake_uid=uid,
                        mode=str(mode),
                        scope="REPLAY",
                    )
                    if pub is None:
                        continue
                    clean = current(pub)
                    cid = clean["publication_id"]
                    assets = [
                        pick(a, ASSET_KEYS)
                        for a in read(f"/api/publications/{cid}/assets", tenant=tenant_id)["assets"]
                    ]
                    comparison = read(
                        f"/api/publications/{cid}/comparison", tenant=tenant_id, limit=2000
                    )
                    changes = comparison["changes"]
                    while len(changes) < comparison["total_changes"]:
                        batch = read(
                            f"/api/publications/{cid}/comparison",
                            tenant=tenant_id,
                            limit=2000,
                            offset=len(changes),
                        )["changes"]
                        if not batch:
                            raise ValueError("Comparação incompleta durante a exportação")
                        changes += batch
                    for change in changes:
                        for side in ("before", "after"):
                            if change[side] is not None:
                                change[side] = pick(change[side], ASSET_KEYS)
                    comparison = pick(
                        comparison,
                        (
                            "publication_id",
                            "previous_publication_id",
                            "previous_available",
                            "unavailable_reason",
                            "causes",
                            "before",
                            "after",
                            "total_changes",
                            "offset",
                            "limit",
                            "changes",
                        ),
                    )
                    for side in ("before", "after"):
                        if comparison[side] is not None:
                            comparison[side] = current(comparison[side])
                    comparison["changes"] = changes
                    record = {
                        "current": clean,
                        "assets": assets,
                        "comparison": comparison,
                        "diff": diff_from_comparison(comparison),
                    }
                    validate_publication(record)
                    records[cid] = record
                    snapshot["current"].append(clean)
                    snapshot["publications"][cid] = {"tenant": tenant_id, "file": f"{cid}.json"}
                    if comparison["previous_available"]:
                        before = comparison["before"]
                        previous_id = before["publication_id"]
                        before_assets = [
                            pick(a, ASSET_KEYS)
                            for a in read(
                                f"/api/publications/{previous_id}/assets", tenant=tenant_id
                            )["assets"]
                        ]
                        previous_record = {"current": before, "assets": before_assets}
                        previous_record["comparison"] = compare(None, previous_record)
                        previous_record["diff"] = diff_from_comparison(
                            previous_record["comparison"]
                        )
                        validate_publication(previous_record)
                        records[previous_id] = previous_record
                        snapshot["publications"][previous_id] = {
                            "tenant": tenant_id,
                            "file": f"{previous_id}.json",
                        }
        for tenant in snapshot["tenants"]:
            tenant_id = tenant["tenant_id"]
            operations = read("/api/operations", tenant=tenant_id)
            operations = pick(
                operations,
                (
                    "generated_at",
                    "tenant_id",
                    "cdc",
                    "jobs",
                    "epochs",
                    "publications",
                    "last_publication_at",
                    "sources",
                ),
            )
            operations["cdc"] = pick(operations["cdc"], CDC_KEYS)
            operations["sources"] = {
                k: pick(v, SOURCE_KEYS) for k, v in operations["sources"].items()
            }
            operations["publications"] = [
                pick(
                    p,
                    (
                        "publication_id",
                        "earthquake_uid",
                        "stream_scope",
                        "mode",
                        "generation",
                        "status",
                        "published_at",
                        "calculated_at",
                        "publication_delay_s",
                        "calculation_duration_s",
                    ),
                )
                for p in operations["publications"]
                if p["publication_id"] in records
            ]
            snapshot["operations"][tenant_id] = operations
    if not snapshot["current"]:
        raise ValueError("Sem publicações REPLAY dos eventos selecionados")
    choose_scenario(snapshot, records)
    return snapshot, records


def write_snapshot(output: Path, snapshot: dict[str, Any], records: dict[str, Any]) -> None:
    output.mkdir(parents=True, exist_ok=True)
    old_files = {
        path for path in output.glob("*.json") if re.fullmatch(r"[a-f0-9]{64}\.json", path.name)
    }
    for cid, record in records.items():
        path = output / f"{cid}.json"
        payload = json.dumps(record, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
        path.write_text(payload + "\n", "utf-8", newline="\n")
        snapshot["publications"][cid]["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
        old_files.discard(path)
    # O índice é gravado depois de todos os arquivos; o próximo export remove arquivos antigos.
    (output / "snapshot.json").write_text(
        json.dumps(snapshot, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        "utf-8",
        newline="\n",
    )
    for path in old_files:
        path.unlink()
    print(
        f"REPLAY: {len(snapshot['current'])} publicações atuais, {len(records)} arquivos; "
        f"{snapshot['metadata']['snapshot_at']}"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--api-base")
    parser.add_argument("--snapshot-at", help="ISO 8601 UTC; padrão: instante da exportação")
    parser.add_argument("--output", type=Path, default=ROOT / "web/public/demo")
    args = parser.parse_args()
    if not re.fullmatch("[a-f0-9]{40}", args.commit):
        parser.error("--commit exige SHA completo")
    at = datetime.fromisoformat(args.snapshot_at) if args.snapshot_at else datetime.now(UTC)
    if at.tzinfo is None:
        parser.error("--snapshot-at exige fuso horário")
    snapshot, records = (
        api_snapshot(args.commit, at, args.api_base)
        if args.api_base
        else fixture_snapshot(args.commit, at)
    )
    write_snapshot(args.output, snapshot, records)


if __name__ == "__main__":
    main()
