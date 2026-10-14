"""Cliente HTTP para o USGS.

Só HTTPS para hosts da allowlist, TLS verificado, redirect é erro. Os limites contam bytes
decodificados (o gzip é aberto aqui, com saída limitada). Retry em 429/5xx e erro de
transporte, respeitando Retry-After; backoff exponencial com jitter.
"""

import base64
import hashlib
import logging
import random
import threading
import time
import zlib
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from importlib.metadata import PackageNotFoundError, version

import httpx

log = logging.getLogger(__name__)

USGS_HOST = "earthquake.usgs.gov"
MIB = 1024 * 1024
JSON_LIMIT = 10 * MIB
GRID_LIMIT = 100 * MIB
CONNECT_TIMEOUT = 3.0
READ_TIMEOUT = 20.0
FDSN_READ_TIMEOUT = 90.0
CONCURRENCY = 2
MAX_ATTEMPTS = 4
BACKOFF_BASE = 2.0
BACKOFF_CAP = 60.0
RETRY_AFTER_CAP = 300.0
RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})
try:
    USER_AGENT = f"quake-exposure/{version('quake-exposure')}"
except PackageNotFoundError:
    USER_AGENT = "quake-exposure/development"


class FetchError(Exception):
    """`transient`: vale tentar de novo mais tarde (rede, 429, 5xx)."""

    def __init__(self, message: str, *, transient: bool = False) -> None:
        super().__init__(message)
        self.transient = transient


class NotAllowed(FetchError):
    """URL fora da allowlist ou sem HTTPS."""


class ContentRejected(FetchError):
    """Resposta recusada: acima do limite, gzip inválido ou diferente do declarado."""


class TooLarge(ContentRejected):
    """Acima do limite de bytes decodificados (ou do `length` declarado)."""


class HttpStatusError(FetchError):
    def __init__(self, url: str, status: int, excerpt: str = "") -> None:
        super().__init__(
            f"{url}: HTTP {status} {excerpt}".rstrip(), transient=status in RETRY_STATUSES
        )
        self.url = url
        self.status = status
        self.excerpt = excerpt  # começo do corpo de um 4xx (o FDSN explica o erro ali)


@dataclass(frozen=True)
class Fetched:
    url: str
    status: int
    fetched_at: datetime
    body: bytes

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.body).hexdigest()


def sha256_b64(data: bytes) -> str:
    """Formato do sha256 declarado no detail do USGS."""
    return base64.b64encode(hashlib.sha256(data).digest()).decode("ascii")


class UsgsClient:
    def __init__(
        self,
        *,
        allowed_hosts: frozenset[str] = frozenset({USGS_HOST}),
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
        jitter: Callable[[], float] = random.random,
    ) -> None:
        self._allowed_hosts = allowed_hosts
        self._sleep = sleep
        self._now = now
        self._jitter = jitter
        self._slots = threading.BoundedSemaphore(CONCURRENCY)
        self._http = httpx.Client(
            transport=transport,
            follow_redirects=False,
            verify=True,
            timeout=httpx.Timeout(READ_TIMEOUT, connect=CONNECT_TIMEOUT),
            headers={"User-Agent": USER_AGENT, "Accept-Encoding": "gzip"},
        )

    def __enter__(self) -> UsgsClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self._http.close()

    def get_json(
        self,
        url: str,
        *,
        params: Mapping[str, str] | None = None,
        read_timeout: float = READ_TIMEOUT,
    ) -> Fetched:
        return self._get(url, params, JSON_LIMIT, read_timeout)

    def get_product(self, url: str, *, declared_length: int, declared_sha256: str) -> Fetched:
        """Baixa um arquivo de produto e confere com o `length` e o `sha256` do detail."""
        if declared_length > GRID_LIMIT:
            raise TooLarge(f"{url}: length declarado {declared_length} acima de {GRID_LIMIT}")
        fetched = self._get(url, None, GRID_LIMIT, READ_TIMEOUT)
        if len(fetched.body) != declared_length or sha256_b64(fetched.body) != declared_sha256:
            raise ContentRejected(f"{url}: conteúdo não bate com o length/sha256 declarados")
        return fetched

    def _get(
        self, url: str, params: Mapping[str, str] | None, limit: int, read_timeout: float
    ) -> Fetched:
        # httpx.URL(url, params=None) apaga a query que já vem na URL
        target = httpx.URL(url).copy_merge_params(params or {})
        if target.scheme != "https" or target.host not in self._allowed_hosts:
            raise NotAllowed(f"{target}: fora da allowlist")
        timeout = httpx.Timeout(read_timeout, connect=CONNECT_TIMEOUT)
        for attempt in range(1, MAX_ATTEMPTS + 1):
            fetched_at = self._now()
            retry_after: float | None = None
            excerpt = ""
            try:
                with self._slots, self._http.stream("GET", target, timeout=timeout) as resp:
                    status = resp.status_code
                    body = _read_limited(resp, limit, str(target)) if status == 200 else b""
                    if status in RETRY_STATUSES:
                        retry_after = _retry_after(resp.headers.get("retry-after"), fetched_at)
                    elif 400 <= status < 500:
                        excerpt = _excerpt(resp, str(target))
            except httpx.TransportError as exc:
                log.warning("GET %s falhou: %r (tentativa %d)", target, exc, attempt)
                if attempt == MAX_ATTEMPTS:
                    raise FetchError(f"{target}: {exc!r}", transient=True) from exc
            else:
                log.info("GET %s -> %d, %d bytes", target, status, len(body))
                if status == 200:
                    return Fetched(str(target), status, fetched_at, body)
                if 300 <= status < 400:
                    raise FetchError(f"{target}: redirect HTTP {status} recusado")
                if status not in RETRY_STATUSES:
                    raise HttpStatusError(str(target), status, excerpt)
                if attempt == MAX_ATTEMPTS:
                    raise HttpStatusError(str(target), status)
            self._sleep(retry_after if retry_after is not None else self._backoff(attempt))
        raise AssertionError("unreachable")

    def _backoff(self, attempt: int) -> float:
        return self._jitter() * min(BACKOFF_CAP, BACKOFF_BASE * 2.0 ** (attempt - 1))


def _retry_after(value: str | None, now: datetime) -> float | None:
    if value is None:
        return None
    value = value.strip()
    if value.isascii() and value.isdigit():
        seconds = float(value)
    else:
        try:
            seconds = (parsedate_to_datetime(value) - now).total_seconds()
        except TypeError, ValueError:
            return None
    return min(max(seconds, 0.0), RETRY_AFTER_CAP)


def _excerpt(resp: httpx.Response, url: str) -> str:
    try:
        text = _read_limited(resp, 64 * 1024, url)[:300].decode("utf-8", "replace")
    except FetchError:
        return ""
    return " ".join(text.split())


def _read_limited(resp: httpx.Response, limit: int, url: str) -> bytes:
    encoding = resp.headers.get("content-encoding", "identity").strip().lower()
    if encoding not in ("identity", "gzip"):
        raise ContentRejected(f"{url}: content-encoding {encoding!r} não suportado")
    out = bytearray()
    chunks: Iterator[bytes] = resp.iter_raw()
    if encoding == "identity":
        for chunk in chunks:
            out += chunk
            if len(out) > limit:
                raise TooLarge(f"{url}: acima de {limit} bytes")
        return bytes(out)
    gz = zlib.decompressobj(wbits=31)
    for chunk in chunks:
        pending = chunk
        while pending:
            # max_length nunca é 0 aqui (0 seria "sem limite"): len(out) <= limit
            try:
                out += gz.decompress(pending, limit + 1 - len(out))
            except zlib.error as exc:
                raise ContentRejected(f"{url}: gzip inválido") from exc
            # o USGS manda um membro só; o que vier depois do fim é lixo e não conta no limite
            if gz.unused_data:
                raise ContentRejected(f"{url}: dados após o fim do gzip")
            if len(out) > limit:
                raise TooLarge(f"{url}: acima de {limit} bytes decodificados")
            pending = gz.unconsumed_tail
    out += gz.flush()
    if len(out) > limit:
        raise TooLarge(f"{url}: acima de {limit} bytes decodificados")
    if not gz.eof:
        raise ContentRejected(f"{url}: gzip truncado")
    return bytes(out)
