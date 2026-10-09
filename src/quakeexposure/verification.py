"""Demonstração executável com resultados, commit e hashes das capturas utilizadas."""

import gzip
import hashlib
import json
import re
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from quakeexposure.exposure.demo import demo
from quakeexposure.ingestion.blobs import BlobStore
from quakeexposure.ingestion.replay import MANIFEST
from quakeexposure.ingestion.store import Conn
from quakeexposure.portfolio.source import connect_source


def verify_demo(
    conn: Conn, other: Callable[[], Conn], blobs: BlobStore, *, commit: str, output: Path
) -> dict[str, Any]:
    if re.fullmatch(r"[0-9a-f]{40}", commit) is None:
        raise ValueError("informe o commit completo de 40 caracteres em --commit")
    manifest = json.loads(MANIFEST.read_text("utf-8"))
    hashes: dict[str, str] = {}
    missing: list[str] = []
    for fixture in manifest["fixtures"]:
        path = MANIFEST.parent / fixture["path"]
        if not path.exists() and not fixture["in_git"]:
            missing.append(fixture["name"])
            continue
        body = gzip.decompress(path.read_bytes())
        actual = hashlib.sha256(body).hexdigest()
        if actual != fixture["sha256"]:
            raise AssertionError(f"fixture alterada: {fixture['name']}")
        hashes[fixture["name"]] = actual
    with connect_source() as source:
        seeded = source.execute("SELECT * FROM ops.seed_status()").fetchone()
    if seeded is None or not seeded[2]:
        raise RuntimeError("seed completo ausente")
    started = datetime.now(UTC)
    messages: list[str] = []

    def out(message: str) -> None:
        messages.append(message)
        print(message, flush=True)

    assertions = demo(conn, other, blobs, out=out)
    mismatch = conn.execute(
        """SELECT count(*) FROM serving.current_publication p
           JOIN exposure.calculation_total t USING(calculation_id)
           WHERE t.qualifying_value_minor <> (
               SELECT coalesce(sum(f.insured_value_minor),0) FROM exposure.fact f
               WHERE f.calculation_id=t.calculation_id AND f.currency=t.currency
                 AND f.qualifying)"""
    ).fetchone()
    assertions["published_totals_match_facts"] = mismatch == (0,)
    assertions["api_cannot_read_cdc_payload"] = conn.execute(
        "SELECT has_table_privilege('qe_api_read','control.cdc_stage','SELECT')"
    ).fetchone() == (False,)
    if not all(assertions.values()):
        raise AssertionError(f"verificação falhou: {assertions}")
    result = {
        "commit": commit,
        "started_at": started.isoformat(),
        "completed_at": datetime.now(UTC).isoformat(),
        "seed": seeded[1],
        "seed_assets": seeded[0],
        "fixture_sha256": hashes,
        "optional_fixtures_missing": missing,
        "assertions": assertions,
        "demo_output": messages,
        "usgs_requests": 0,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", "utf-8")
    return result
