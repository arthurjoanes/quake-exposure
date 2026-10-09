"""Bootstrap explícito: origem -> Debezium -> plataforma, antes de ligar o worker."""

import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from quakeexposure.db import connect
from quakeexposure.ingestion.store import Conn


def wait_for_epoch(
    conn: Conn,
    tenant: str,
    epoch: int,
    source_epoch: int,
    *,
    timeout: float = 180.0,
    tick: Callable[[], None] | None = None,
) -> None:
    deadline = time.monotonic() + timeout
    while True:
        if tick is not None:
            tick()
        row = conn.execute(
            "SELECT portfolio_epoch, source_epoch FROM portfolio.tenant WHERE tenant_id = %s",
            (tenant,),
        ).fetchone()
        if row is not None and row[1] == source_epoch and row[0] >= epoch:
            return
        if time.monotonic() >= deadline:
            raise TimeoutError(f"CDC não alcançou {tenant}: epoch {epoch}, origem {source_epoch}")
        time.sleep(0.1)


def bootstrap(*, assets: int = 10_000, seed: int = 42) -> None:
    import os

    from quakeexposure.ingestion.blobs import BlobStore
    from quakeexposure.ingestion.replay import replay
    from quakeexposure.portfolio.source import connect_source
    from quakeexposure.portfolio.source import seed as seed_source

    started = time.monotonic()
    with connect_source() as source, connect() as platform:
        deadline = time.monotonic() + 120
        while source.execute("SELECT active FROM ops.cdc_slot_status()").fetchone() != (True,):
            if time.monotonic() >= deadline:
                raise TimeoutError("o slot qe_portfolio não ficou ativo")
            time.sleep(0.2)
        # O loader antigo não é uma origem CDC. Não confundir epochs coincidentes.
        legacy = platform.execute(
            """SELECT EXISTS(SELECT 1 FROM portfolio.tenant)
                      AND NOT EXISTS(SELECT 1 FROM control.cdc_transaction
                                     WHERE applied_at IS NOT NULL)"""
        ).fetchone()
        source_has_data = source.execute("SELECT EXISTS(SELECT 1 FROM public.portfolio_clock)")
        if legacy and legacy[0] and source_has_data.fetchone() == (False,):
            raise RuntimeError(
                "carteira da carga direta encontrada; use um projeto Compose novo para CDC"
            )
        seeded = source.execute("SELECT * FROM ops.seed_status()").fetchone()
        if seeded is None:
            result = seed_source(source, count=assets, seed=seed)
            print(f"comandos de origem: {result}")
        elif seeded != (assets, seed, True):
            raise RuntimeError(
                "seed incompleto ou parâmetros diferentes; siga o reset manual em docs/operacao.md"
            )
        else:
            print("carteira já semeada; aguardando o CDC")
        for tenant, epoch, source_epoch in source.execute(
            "SELECT tenant_id, epoch, source_epoch FROM public.portfolio_clock"
        ).fetchall():
            wait_for_epoch(platform, tenant, epoch, source_epoch)
        blobs = BlobStore(Path(os.environ.get("QE_BLOB_DIR", "data/blobs")))
        replay(platform, blobs, datetime.now(UTC))
    print(f"bootstrap concluído em {time.monotonic() - started:.2f} s")
