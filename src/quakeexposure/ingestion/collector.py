"""Coletor do USGS: feed, details, grids, incremental FDSN e backfill, num processo só.

Falha de rede vira fonte desatualizada no status; nunca apaga nem esconde terremoto.
"""

import hashlib
import json
import logging
import time
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from typing import Any

import psycopg

from quakeexposure.domain.canonical import from_epoch_ms
from quakeexposure.ingestion import store
from quakeexposure.ingestion.blobs import BlobStore
from quakeexposure.ingestion.http import (
    FDSN_READ_TIMEOUT,
    READ_TIMEOUT,
    ContentRejected,
    Fetched,
    FetchError,
    HttpStatusError,
    NotAllowed,
    TooLarge,
    UsgsClient,
)
from quakeexposure.ingestion.projection import (
    FDSN_QUERY_URL,
    feature_sha256,
    project_pending,
    reconsult_url,
    revision_of,
)
from quakeexposure.ingestion.store import Conn

log = logging.getLogger(__name__)

FEED_URL = "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/all_day.geojson"
FEED_EVERY = timedelta(seconds=60)
INCREMENTAL_EVERY = timedelta(seconds=600)
HORIZON = timedelta(days=90)
OVERLAP = timedelta(seconds=600)
SCOPE_WINDOW = timedelta(days=7)  # eventos com ShakeMap dos últimos 7 dias entram no cálculo
MIN_WINDOW = timedelta(hours=1)
REQUESTS_PER_TICK = 10
GRIDS_PER_TICK = 3
MAX_REQUEST_ATTEMPTS = 5


def fdsn_time(dt: datetime) -> str:
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S")


class Collector:
    def __init__(
        self,
        conn: Conn,
        client: UsgsClient,
        blobs: BlobStore,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.conn = conn
        self.client = client
        self.blobs = blobs
        self.clock = clock
        self.next_feed: datetime | None = None
        self.next_incremental: datetime | None = None

    def tick(self) -> datetime:
        """Roda o que venceu e devolve quando vence a próxima tarefa."""
        now = self.clock()
        if self.next_feed is None or now >= self.next_feed:
            self.next_feed = now + FEED_EVERY
            self.poll_feed(now)
        if self.next_incremental is None or now >= self.next_incremental:
            self.next_incremental = now + INCREMENTAL_EVERY
            self.poll_incremental(now)
        self.fetch_requested()
        self.fetch_grids()
        self._project_pending()
        return min(self.next_feed, self.next_incremental)

    def poll_feed(self, now: datetime) -> None:
        try:
            new = self._take_collection("feed", self.client.get_json(FEED_URL))
        except (FetchError, psycopg.Error, ValueError) as exc:
            self._source_error("feed", now, exc)
            return
        self._source_ok("feed", now, new)

    def poll_incremental(self, now: datetime) -> None:
        """`updatedafter` = watermark menos 600 s; o watermark só avança se tudo terminar."""
        row = self.conn.execute(
            "SELECT watermark FROM control.source_status WHERE source = 'incremental'"
        ).fetchone()
        watermark = row[0] if row and row[0] else now
        params = {
            "format": "geojson",
            "includedeleted": "true",
            "updatedafter": fdsn_time(watermark - OVERLAP),
        }
        try:
            new = self._query_windows(params, now - HORIZON, now)
        except (FetchError, psycopg.Error, ValueError) as exc:
            self._source_error("incremental", now, exc)  # o watermark fica onde estava
            return
        self._source_ok("incremental", now, new, watermark=now)

    def backfill(self, first: date, last: date) -> int:
        """Janelas de 1 dia; a janela que falha não grava cursor e para o backfill."""
        total = 0
        day = first
        while day <= last:
            done = self.conn.execute(
                "SELECT 1 FROM control.backfill_window WHERE day = %s", (day,)
            ).fetchone()
            if not done:
                start = datetime(day.year, day.month, day.day, tzinfo=UTC)
                params = {"format": "geojson", "includedeleted": "true"}
                new = self._query_windows(params, start, start + timedelta(days=1))
                self.conn.execute(
                    """INSERT INTO control.backfill_window (day, completed_at, new_revisions)
                       VALUES (%s, %s, %s)""",
                    (day, self.clock(), new),
                )
                log.info("backfill %s: %d revisões novas", day, new)
                total += new
            day += timedelta(days=1)
        return total

    def _query_windows(self, params: dict[str, str], start: datetime, end: datetime) -> int:
        """Consulta o FDSN; se recusar por excesso de eventos, divide a janela ao meio."""
        windows = [(start, end)]
        new = 0
        while windows:
            a, b = windows.pop(0)
            try:
                fetched = self.client.get_json(
                    FDSN_QUERY_URL,
                    params={**params, "starttime": fdsn_time(a), "endtime": fdsn_time(b)},
                    read_timeout=FDSN_READ_TIMEOUT,
                )
            except FetchError as exc:
                refused = isinstance(exc, HttpStatusError) and (
                    exc.status == 400 and "limit" in exc.excerpt.lower()
                )
                if (refused or isinstance(exc, TooLarge)) and b - a > MIN_WINDOW:
                    mid = a + (b - a) / 2
                    windows[:0] = [(a, mid), (mid, b)]
                    log.info("FDSN: excesso em %s a %s (%s); dividindo", a, b, exc)
                    continue
                raise
            new += self._take_collection("fdsn", fetched)
        return new

    def fetch_requested(self) -> None:
        rows = self.conn.execute(
            """SELECT provider_event_id, url, requested_at, min_updated FROM control.fetch_request
               WHERE attempts < %s ORDER BY requested_at, provider_event_id LIMIT %s""",
            (MAX_REQUEST_ATTEMPTS, REQUESTS_PER_TICK),
        ).fetchall()
        for event_id, url, requested_at, min_updated in rows:
            now = self.clock()
            try:
                timeout = FDSN_READ_TIMEOUT if "/fdsnws/" in url else READ_TIMEOUT
                fetched = self.client.get_json(url, read_timeout=timeout)
                updated = _feature_updated(fetched.body)
                if updated is None:
                    raise ContentRejected(f"{url}: resposta não é Feature", transient=True)
            except HttpStatusError as exc:
                if exc.status in (404, 409, 410) and url != reconsult_url(event_id):
                    # deletado devolve 409: reconsulta por eventid com includedeleted=true
                    with self.conn.transaction():
                        store.request_fetch(
                            self.conn, event_id, reconsult_url(event_id), "deleted", now
                        )
                    continue
                self._request_failed(event_id, url, exc)
                continue
            except FetchError as exc:
                self._request_failed(event_id, url, exc)
                continue
            # grava sempre: o mesmo detail pode valer de novo (conflito resolvido, reconsulta)
            self._store("detail", fetched)
            self._project_pending()
            with self.conn.transaction():
                if min_updated is not None and updated < min_updated:
                    # detail de cache, mais velho que a revisão que o pediu: tenta de novo
                    self.conn.execute(
                        """UPDATE control.fetch_request SET attempts = attempts + 1,
                               last_error = 'detail mais velho que a revisão'
                           WHERE provider_event_id = %s AND requested_at = %s""",
                        (event_id, requested_at),
                    )
                else:
                    # só a linha lida: um pedido novo gravado durante o GET fica
                    self.conn.execute(
                        """DELETE FROM control.fetch_request
                           WHERE provider_event_id = %s AND requested_at = %s""",
                        (event_id, requested_at),
                    )

    def _project_pending(self) -> None:
        """Projeta o pendente; erro transitório do banco só adia para o ciclo seguinte."""
        try:
            project_pending(self.conn, self.blobs, self.clock())
        except psycopg.Error as exc:
            if self.conn.broken or self.conn.closed:
                raise
            log.warning("projeção adiada: %s", exc)

    def _request_failed(self, event_id: str, url: str, exc: FetchError) -> None:
        log.warning("detail %s: %s", event_id, exc)
        with self.conn.transaction():
            if exc.transient:
                self.conn.execute(
                    """UPDATE control.fetch_request SET attempts = attempts + 1, last_error = %s
                       WHERE provider_event_id = %s""",
                    (str(exc)[:500], event_id),
                )
                return
            kind = "not-allowed" if isinstance(exc, NotAllowed) else "rejected"
            store.quarantine(self.conn, "LIVE", None, f"fetch {kind}: {_reason(exc)}", _sha(url))
            self.conn.execute(
                "DELETE FROM control.fetch_request WHERE provider_event_id = %s", (event_id,)
            )

    def fetch_grids(self) -> None:
        """Grid só do produto escolhido de evento em escopo (7 dias ou watchlist)."""
        now = self.clock()
        rows = self.conn.execute(
            """SELECT m.product_revision_id, p.grid_url, p.grid_length, p.grid_sha256
               FROM quake.event_map m
               JOIN quake.earthquake e USING (stream_scope, earthquake_uid)
               JOIN quake.shakemap_product p USING (stream_scope, product_revision_id)
               LEFT JOIN quake.shakemap_grid g USING (stream_scope, product_revision_id)
               LEFT JOIN control.grid_attempt t USING (stream_scope, product_revision_id)
               WHERE m.stream_scope = 'LIVE' AND e.merged_into IS NULL
                 AND g.product_revision_id IS NULL AND p.grid_url IS NOT NULL
                 AND coalesce(t.attempts, 0) < %s
                 AND (e.event_time >= %s OR EXISTS (
                      SELECT 1 FROM quake.alias a
                      JOIN control.watchlist w ON w.provider_event_id = a.provider_id
                      WHERE a.stream_scope = 'LIVE' AND a.earthquake_uid = e.earthquake_uid))
               ORDER BY coalesce(t.attempts, 0), e.event_time DESC LIMIT %s""",
            (MAX_REQUEST_ATTEMPTS, now - SCOPE_WINDOW, GRIDS_PER_TICK),
        ).fetchall()
        for product, url, length, sha in rows:
            try:
                fetched = self.client.get_product(url, declared_length=length, declared_sha256=sha)
            except FetchError as exc:
                log.warning("grid %s: %s", product, exc)
                if exc.transient:
                    # tenta de novo nos próximos ciclos, até o limite, sem travar os outros
                    self.conn.execute(
                        """INSERT INTO control.grid_attempt (stream_scope, product_revision_id,
                               attempts, last_error, last_attempt_at)
                           VALUES ('LIVE', %s, 1, %s, %s)
                           ON CONFLICT (stream_scope, product_revision_id) DO UPDATE SET
                               attempts = control.grid_attempt.attempts + 1,
                               last_error = EXCLUDED.last_error,
                               last_attempt_at = EXCLUDED.last_attempt_at""",
                        (product, _reason(exc), self.clock()),
                    )
                    continue
                with self.conn.transaction():
                    store.quarantine(self.conn, "LIVE", None, f"grid: {_reason(exc)}", sha)
                    store.store_grid(
                        self.conn, "LIVE", product, status="REJECTED", reason=_reason(exc)
                    )
                continue
            self._store("grid", fetched, product=product)
            self.conn.execute(
                """DELETE FROM control.grid_attempt
                   WHERE stream_scope = 'LIVE' AND product_revision_id = %s""",
                (product,),
            )
            self._project_pending()

    def _take_collection(self, kind: str, fetched: Fetched) -> int:
        """Guarda a captura só se ela trouxer algo novo; senão fica só a linha de log."""
        try:
            doc = json.loads(fetched.body)
        except ValueError:
            doc = None
        if not (
            isinstance(doc, dict)
            and doc.get("type") == "FeatureCollection"
            and isinstance(doc.get("features"), list)
        ):
            # 200 com corpo inútil conta como falha: o watermark e o cursor não avançam
            if not store.quarantined(self.conn, "LIVE", fetched.sha256):
                with self.conn.transaction():
                    store.quarantine(
                        self.conn, "LIVE", None, f"{kind}: não é FeatureCollection", fetched.sha256
                    )
            raise ContentRejected(f"{fetched.url}: resposta não é FeatureCollection")
        if not self._has_news(doc):
            log.info("%s sem revisão nova (%d bytes)", kind, len(fetched.body))
            return 0
        capture_id = self._store(kind, fetched)
        new = project_pending(self.conn, self.blobs, self.clock()).new_revisions  # levanta
        status = self.conn.execute(
            "SELECT projection_status FROM control.capture WHERE capture_id = %s", (capture_id,)
        ).fetchone()
        if status is None or status[0] != "OK":
            # a resposta não foi aplicada: não conta como sucesso (watermark e cursor ficam)
            raise ContentRejected(f"{fetched.url}: projeção da captura {capture_id} falhou")
        return new

    def _has_news(self, doc: dict[str, Any]) -> bool:
        ids = []
        for f in doc["features"]:
            rev = revision_of(f)
            if rev is not None:
                ids.append(rev.revision_id)
            elif not store.quarantined(self.conn, "LIVE", feature_sha256(f)):
                return True  # a projeção põe na quarentena
        new = set(ids) - store.known_revisions(self.conn, "LIVE", ids)
        # revisão em conflito já está na quarentena: não grava o feed de novo a cada minuto
        return any(not store.quarantined(self.conn, "LIVE", r) for r in new)

    def _store(self, kind: str, fetched: Fetched, product: str | None = None) -> int:
        sha, rel = self.blobs.put(fetched.body)
        with self.conn.transaction():
            return store.store_capture(
                self.conn, scope="LIVE", kind=kind, url=fetched.url,
                fetched_at=fetched.fetched_at, http_status=fetched.status,
                body_bytes=len(fetched.body), sha256=sha, blob_path=rel,
                product_revision_id=product,
            )  # fmt: skip

    def _source_ok(
        self, source: str, now: datetime, new: int, watermark: datetime | None = None
    ) -> None:
        self.conn.execute(
            """INSERT INTO control.source_status AS s (source, last_attempt_at, last_ok_at,
                   last_error, watermark, last_new_revisions)
               VALUES (%s, %s, %s, NULL, %s, %s)
               ON CONFLICT (source) DO UPDATE SET last_attempt_at = EXCLUDED.last_attempt_at,
                   last_ok_at = EXCLUDED.last_ok_at, last_error = NULL,
                   watermark = COALESCE(EXCLUDED.watermark, s.watermark),
                   last_new_revisions = EXCLUDED.last_new_revisions""",
            (source, now, now, watermark, new),
        )

    def _source_error(self, source: str, now: datetime, exc: Exception) -> None:
        log.warning("%s falhou: %s", source, exc)
        self.conn.execute(
            """INSERT INTO control.source_status (source, last_attempt_at, last_error)
               VALUES (%s, %s, %s)
               ON CONFLICT (source) DO UPDATE SET last_attempt_at = EXCLUDED.last_attempt_at,
                   last_error = EXCLUDED.last_error""",
            (source, now, str(exc)[:500]),
        )


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _feature_updated(body: bytes) -> datetime | None:
    """`updated` do detail, ou None se a resposta não é uma Feature com `updated`."""
    try:
        doc = json.loads(body)
        if isinstance(doc, dict) and doc.get("type") == "Feature":
            return from_epoch_ms(int(doc["properties"]["updated"]))
    except ValueError, KeyError, TypeError, OverflowError:
        pass
    return None


def _reason(exc: FetchError) -> str:
    """Para a quarentena: sem o trecho do corpo que o HttpStatusError carrega."""
    if isinstance(exc, HttpStatusError):
        return f"{exc.url}: HTTP {exc.status}"
    return str(exc)


def run_forever(
    make: Callable[[], Collector],
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    collector: Collector | None = None
    while True:
        try:
            if collector is None:
                collector = make()
            wake = collector.tick()
        except psycopg.OperationalError:
            log.exception("banco indisponível; reconectando")
            collector = None
            wake = clock() + timedelta(seconds=10)
        except Exception:
            log.exception("ciclo do coletor falhou")
            wake = clock() + timedelta(seconds=10)
        sleep(max(1.0, (wake - clock()).total_seconds()))
