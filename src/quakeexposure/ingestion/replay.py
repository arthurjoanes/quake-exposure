"""REPLAY (capturas reais, com o horário original) e FAULT (mutação de teste, rotulada).

Os dois usam o mesmo caminho de captura e projeção do LIVE, cada um no seu `stream_scope`.
Nada aqui acessa a rede.
"""

import gzip
import hashlib
import json
import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from quakeexposure.db import ROOT
from quakeexposure.ingestion import store
from quakeexposure.ingestion.blobs import BlobStore
from quakeexposure.ingestion.projection import Projected, project_pending
from quakeexposure.ingestion.store import Conn

log = logging.getLogger(__name__)

MANIFEST = ROOT / "tests" / "fixtures" / "manifest.json"


@dataclass
class Replayed:
    stored: int = 0
    already: int = 0
    missing: int = 0
    projected: Projected | None = None


def replay(conn: Conn, blobs: BlobStore, now: datetime, manifest: Path = MANIFEST) -> Replayed:
    """Grava as capturas do manifesto como REPLAY (ou FAULT, se rotuladas) e projeta."""
    report = Replayed()
    entries = json.loads(manifest.read_text("utf-8"))["fixtures"]
    for e in sorted(entries, key=lambda e: e["fetched_at"]):
        path = manifest.parent / e["path"]
        if not path.exists():
            log.warning("%s fora do Git e não baixado (fetch_fixtures.py pull)", e["name"])
            report.missing += 1
            continue
        body = gzip.decompress(path.read_bytes())
        if hashlib.sha256(body).hexdigest() != e["sha256"]:
            raise ValueError(f"{e['name']}: sha256 não bate com o manifesto")
        scope = "FAULT" if e["fault"] else "REPLAY"
        if store.captured(conn, scope, e["sha256"]):
            report.already += 1
            continue
        _capture(
            conn, blobs, scope=scope, kind=e["kind"], url=e["url"],
            fetched_at=datetime.fromisoformat(e["fetched_at"]), http_status=e["http_status"],
            body=body, product=e["product"], parent_sha256=e["parent_sha256"],
        )  # fmt: skip
        report.stored += 1
    report.projected = project_pending(conn, blobs, now)
    return report


def capture_fault(
    conn: Conn,
    blobs: BlobStore,
    *,
    parent_sha256: str,
    kind: str,
    url: str,
    fetched_at: datetime,
    body: bytes,
    product: str | None = None,
) -> int:
    """Grava uma mutação de teste como FAULT, ligada à captura real de origem."""
    return _capture(
        conn, blobs, scope="FAULT", kind=kind, url=url, fetched_at=fetched_at, http_status=200,
        body=body, product=product, parent_sha256=parent_sha256,
    )  # fmt: skip


def _capture(
    conn: Conn,
    blobs: BlobStore,
    *,
    scope: str,
    kind: str,
    url: str,
    fetched_at: datetime,
    http_status: int,
    body: bytes,
    product: str | None,
    parent_sha256: str | None,
) -> int:
    sha, rel = blobs.put(body)
    with conn.transaction():
        return store.store_capture(
            conn, scope=scope, kind=kind, url=url, fetched_at=fetched_at,
            http_status=http_status, body_bytes=len(body), sha256=sha, blob_path=rel,
            product_revision_id=product, parent_sha256=parent_sha256,
        )  # fmt: skip
