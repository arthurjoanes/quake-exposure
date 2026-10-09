"""`qe demo` e `qe bench` (tempo de revisão aplicada até publicação).

1. replay de captura real e cálculo inicial;
2. revisão FAULT que muda a exposição, e a mesma captura de novo sem efeito;
3. correção retroativa da carteira pela origem e CDC;
4. job velho recusado pela geração.
"""

import gzip
import hashlib
import json
import random
import statistics
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from quakeexposure.domain.rule import Mode
from quakeexposure.exposure import jobs, worker
from quakeexposure.exposure.explain import diff
from quakeexposure.ingestion import store
from quakeexposure.ingestion.blobs import BlobStore
from quakeexposure.ingestion.projection import project_pending
from quakeexposure.ingestion.replay import MANIFEST, capture_fault, replay
from quakeexposure.ingestion.store import Conn
from quakeexposure.portfolio import store as portfolio

EVENT = "us7000tiur"  # M 5,5 em Flores, MMI até 6,7: a carteira de demo tem ativos em volta
Out = Callable[[str], None]


def demo(
    conn: Conn,
    other: Callable[[], Conn],
    blobs: BlobStore,
    out: Out = print,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> None:
    ensure_portfolio(conn, out)

    out("1. replay da captura real e cálculo inicial")
    done = replay(conn, blobs, now())
    out(f"   capturas gravadas {done.stored}, já existiam {done.already}")
    _drain(conn, blobs, now)
    _summary(conn, "REPLAY", out)

    out("2. revisão FAULT que muda a exposição")
    detail, grid = _fixture("detail_us7000tiur"), _fixture("grid_us7000tiur")
    _fault_base(conn, blobs, detail, grid, now())
    _drain(conn, blobs, now)
    before = _current(conn)
    moved = shifted(detail, dlon=0.5, updated=_next_updated(conn, detail))
    _fault(conn, blobs, detail, moved, now())
    _drain(conn, blobs, now)
    after = _current(conn)
    for key, calc in after.items():
        if key[3] != Mode.RADIUS_TRIAGE:
            continue
        d = diff(conn, before.get(key), calc)
        out(f"   {key[0]} RADIUS: epicentro 0,5° a leste; entraram {len(d['entered'])},"
            f" saíram {len(d['left'])}; causa: {', '.join(d['causes'])}")  # fmt: skip
    _summary(conn, "FAULT", out)
    out("   a mesma captura de novo:")
    _fault(conn, blobs, detail, moved, now())
    _drain(conn, blobs, now)
    same = _current(conn) == after
    out("   revisão duplicada, nenhum cálculo novo; publication_id igual: "
        + ("sim" if same else "NÃO"))  # fmt: skip
    assert same, "revisão duplicada mudou a publicação"

    _portfolio_step(conn, blobs, now, out)

    out("4. job velho recusado pela geração")
    tenant = portfolio.tenants(conn)[0].tenant_id
    uid = _fault_uid(conn)
    job = None
    for n in range(5):  # o worker do Compose pode pegar o job antes: outra revisão
        step4 = shifted(detail, dlon=1.0 + n / 10, updated=_next_updated(conn, detail))
        _fault(conn, blobs, detail, step4, now())
        jobs.mark(conn, now())
        job = worker.claim(conn, now(), key=(tenant, uid, "FAULT", Mode.RADIUS_TRIAGE))
        if job is not None:
            break
    assert job is not None
    newest = shifted(detail, dlon=1.5, updated=_next_updated(conn, detail, extra=60))

    def new_revision_during_calculation() -> None:
        with other() as second:
            _fault(second, blobs, detail, newest, now())  # a projeção sobe a geração

    result = worker.run(conn, blobs, job, now, before_commit=new_revision_during_calculation)
    assert result == "stale", "o job velho foi publicado"
    generation = conn.execute(
        """SELECT desired_generation FROM control.exposure_job WHERE tenant_id = %s
           AND earthquake_uid = %s AND stream_scope = 'FAULT' AND mode = %s""",
        (tenant, uid, Mode.RADIUS_TRIAGE),
    ).fetchone()
    assert generation is not None
    out(f"   cálculo da geração {job.generation}: {'recusado' if result == 'stale' else result};"
        f" geração atual {generation[0]}")  # fmt: skip
    _drain(conn, blobs, now)
    published = conn.execute(
        """SELECT generation FROM serving.current_publication WHERE tenant_id = %s
           AND earthquake_uid = %s AND stream_scope = 'FAULT' AND mode = %s""",
        (tenant, uid, Mode.RADIUS_TRIAGE),
    ).fetchone()
    out(f"   publicada a geração {published[0] if published else '-'}")


def _portfolio_step(conn: Conn, blobs: BlobStore, now: Callable[[], datetime], out: Out) -> None:
    from quakeexposure.bootstrap import wait_for_epoch
    from quakeexposure.domain.portfolio import assets_as_of
    from quakeexposure.portfolio.source import connect_source, correct

    out("3. correção retroativa da carteira via CDC, com comparação as-of")
    event_time = conn.execute(
        """SELECT e.event_time FROM quake.earthquake e JOIN quake.alias a
             USING (stream_scope, earthquake_uid)
           WHERE e.stream_scope = 'REPLAY' AND a.provider_id = %s""",
        (EVENT,),
    ).fetchone()
    assert event_time is not None
    with connect_source() as source:
        row = source.execute(
            """SELECT a.tenant_id, a.asset_interval_id, a.asset_id, a.lon, a.lat,
                      c.epoch, c.source_epoch
               FROM public.asset_interval a JOIN public.portfolio_clock c USING (tenant_id)
               WHERE NOT a.is_deleted AND a.valid_from <= %s
                 AND (a.valid_to IS NULL OR a.valid_to > %s)
               ORDER BY a.tenant_id, a.asset_interval_id LIMIT 1""",
            (event_time[0], event_time[0]),
        ).fetchone()
        if row is None:
            raise RuntimeError("nenhum ativo na origem para a demonstração CDC")
        tenant, interval, asset_id, lon, lat, old_epoch, source_epoch = row
        before = next(
            a
            for a in assets_as_of(
                portfolio.versions_known(conn, tenant, old_epoch),
                tenant_id=tenant,
                at=event_time[0],
                epoch=old_epoch,
            )
            if a.asset_id == asset_id
        )
        new_lon = round(lon + (0.02 if lon < 179 else -0.02), 6)
        result = correct(source, tenant, interval, lon=new_lon, lat=lat)
        epoch = result.epoch
    wait_for_epoch(conn, tenant, epoch, source_epoch)
    known = portfolio.versions_known(conn, tenant, epoch)
    old = next(
        a
        for a in assets_as_of(
            known,
            tenant_id=tenant,
            at=event_time[0],
            epoch=old_epoch,
        )
        if a.asset_id == asset_id
    )
    current = next(
        a
        for a in assets_as_of(
            known,
            tenant_id=tenant,
            at=event_time[0],
            epoch=epoch,
        )
        if a.asset_id == asset_id
    )
    assert old.lon == before.lon and current.lon == new_lon
    _drain(conn, blobs, now)
    out(f"   {tenant}/{asset_id}: epoch {old_epoch} lon {old.lon}; epoch {epoch} lon {current.lon}")


def bench(
    conn: Conn,
    blobs: BlobStore,
    rounds: int = 5,
    timeout: float = 120.0,
    out: Out = print,
) -> list[float]:
    """Revisão FAULT nova -> as 4 publicações (2 tenants x 2 modos) trocadas pelo worker.

    Precisa do worker rodando (serviço `worker` do Compose)."""
    ensure_portfolio(conn, out)
    detail, grid = _fixture("detail_us7000tiur"), _fixture("grid_us7000tiur")
    _fault_base(conn, blobs, detail, grid, datetime.now(UTC))
    times = []
    for n in range(rounds):
        before = _current(conn)
        # posição nova a cada rodada: com uma posição já vista, o cálculo já existe e só a
        # publicação seria medida
        dlon = random.uniform(-0.45, 0.45)  # noqa: S311
        revision = shifted(detail, dlon=dlon, updated=_next_updated(conn, detail))
        _fault(conn, blobs, detail, revision, datetime.now(UTC))
        applied = time.monotonic()
        expected = 2 * len(portfolio.tenants(conn))
        while True:
            after = _current(conn)
            changed = [k for k, c in after.items() if before.get(k) != c]
            if len(changed) >= expected:
                break
            if time.monotonic() - applied > timeout:
                raise TimeoutError(f"rodada {n + 1}: {len(changed)} de {expected} publicadas")
            time.sleep(0.05)
        elapsed = time.monotonic() - applied
        times.append(elapsed)
        out(f"rodada {n + 1}: {elapsed:.2f} s até as {expected} publicações")
    out(
        f"mediana {statistics.median(times):.2f} s, máximo {max(times):.2f} s,"
        f" mínimo {min(times):.2f} s"
    )
    return times


def ensure_portfolio(conn: Conn, out: Out = print) -> None:
    if portfolio.tenants(conn):
        return
    raise RuntimeError("carteira ausente: execute qe bootstrap antes de qe demo")


def shifted(detail: bytes, *, dlon: float, updated: int) -> dict[str, Any]:
    """Mutação de teste: o mesmo evento com o epicentro deslocado e outro `updated`."""
    doc: dict[str, Any] = json.loads(detail)
    lon, lat, *rest = doc["geometry"]["coordinates"]
    doc["geometry"]["coordinates"] = [round(lon + dlon, 4), lat, *rest]
    doc["properties"]["updated"] = updated
    return doc


def _fixture(name: str) -> bytes:
    entry = next(
        e for e in json.loads(MANIFEST.read_text("utf-8"))["fixtures"] if e["name"] == name
    )
    return gzip.decompress((MANIFEST.parent / entry["path"]).read_bytes())


def _drain(conn: Conn, blobs: BlobStore, now: Callable[[], datetime], timeout: float = 120) -> None:
    """Roda o worker até a fila esvaziar. Com o worker do Compose no ar, ele pode pegar parte dos
    jobs: aqui também se espera por esses."""
    deadline = time.monotonic() + timeout
    while True:
        worker.run_pending(conn, blobs, now)
        row = conn.execute(
            """SELECT count(*) FROM control.exposure_job
               WHERE desired_generation > published_generation AND attempts < %s""",
            (worker.MAX_ATTEMPTS,),
        ).fetchone()
        if row is None or row[0] == 0 or time.monotonic() > deadline:
            return
        time.sleep(0.2)


def _fault_base(conn: Conn, blobs: BlobStore, detail: bytes, grid: bytes, at: datetime) -> None:
    """Cópias rotuladas como FAULT do detail e do grid reais (o ponto de partida da mutação)."""
    doc = json.loads(detail)
    product = doc["properties"]["products"]["shakemap"][0]["id"]
    for kind, body, prod in (("detail", detail, None), ("grid", grid, product)):
        sha = hashlib.sha256(body).hexdigest()
        if not store.captured(conn, "FAULT", sha):
            capture_fault(
                conn, blobs, parent_sha256=sha, kind=kind, url=f"fault://{kind}/{EVENT}",
                fetched_at=at, body=body, product=prod,
            )  # fmt: skip
    project_pending(conn, blobs, at)


def _fault(conn: Conn, blobs: BlobStore, parent: bytes, doc: dict[str, Any], at: datetime) -> None:
    capture_fault(
        conn, blobs, parent_sha256=hashlib.sha256(parent).hexdigest(), kind="detail",
        url=f"fault://detail/{EVENT}", fetched_at=at, body=json.dumps(doc).encode(),
    )  # fmt: skip
    project_pending(conn, blobs, at)


def _next_updated(conn: Conn, detail: bytes, extra: int = 0) -> int:
    """Um `updated` depois de tudo o que o FAULT já viu deste evento (a demo pode repetir)."""
    row = conn.execute(
        """SELECT max(source_updated_at) FROM quake.event_revision
           WHERE stream_scope = 'FAULT' AND provider_event_id = %s""",
        (EVENT,),
    ).fetchone()
    base = int(json.loads(detail)["properties"]["updated"])
    if row and row[0]:
        base = max(base, int(row[0].timestamp() * 1000))
    return base + 60_000 + extra * 1000


def _fault_uid(conn: Conn) -> Any:
    row = conn.execute(
        "SELECT earthquake_uid FROM quake.alias WHERE stream_scope = 'FAULT' AND provider_id = %s",
        (EVENT,),
    ).fetchone()
    assert row is not None
    return row[0]


def _current(conn: Conn) -> dict[tuple[str, Any, str, str], str]:
    rows = conn.execute(
        """SELECT p.tenant_id, p.earthquake_uid, p.stream_scope, p.mode, p.calculation_id
           FROM serving.current_publication p
           JOIN quake.alias a USING (stream_scope, earthquake_uid)
           WHERE p.stream_scope = 'FAULT' AND a.provider_id = %s""",
        (EVENT,),
    ).fetchall()
    return {(t, u, s, m): c for t, u, s, m, c in rows}


def _summary(conn: Conn, scope: str, out: Out) -> None:
    rows = conn.execute(
        """SELECT p.tenant_id, p.mode, c.status, c.eligible_count, c.qualifying_count,
                  (SELECT string_agg(currency || ' ' || qualifying_value_minor::text, ', '
                                     ORDER BY currency)
                   FROM exposure.calculation_total t WHERE t.calculation_id = c.calculation_id)
           FROM serving.current_publication p
           JOIN exposure.calculation c USING (calculation_id)
           JOIN quake.alias a ON a.stream_scope = p.stream_scope
                              AND a.earthquake_uid = p.earthquake_uid
           WHERE p.stream_scope = %s AND a.provider_id = %s
           ORDER BY 1, 2""",
        (scope, EVENT),
    ).fetchall()
    for tenant, mode, status, eligible, qualifying, totals in rows:
        out(f"   {scope} {EVENT} {tenant} {mode}: {status}, elegíveis {eligible},"
            f" classificados {qualifying}; {totals or 'sem valores'} (unidade menor)")  # fmt: skip
