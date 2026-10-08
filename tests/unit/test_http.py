import gzip
import hashlib
import socket
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime

import httpx
import pytest

from quakeexposure.ingestion.http import (
    FDSN_READ_TIMEOUT,
    GRID_LIMIT,
    JSON_LIMIT,
    MAX_ATTEMPTS,
    FetchError,
    HttpStatusError,
    UsgsClient,
    sha256_b64,
)

FEED = "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/all_day.geojson"
NOW = datetime(2026, 10, 7, 12, 0, tzinfo=UTC)
Reply = Callable[[], httpx.Response]


def reply(status: int = 200, body: bytes = b"{}", **headers: str | bytes) -> Reply:
    # stream= e não content=: com content= o httpx já lê o corpo e iter_raw() falha
    names = [
        (k.replace("_", "-").encode(), v if isinstance(v, bytes) else v.encode())
        for k, v in headers.items()
    ]
    return lambda: httpx.Response(status, headers=names, stream=httpx.ByteStream(body))


def gz(body: bytes) -> Reply:
    return reply(200, gzip.compress(body), content_encoding="gzip")


class Recorder:
    def __init__(self, *responses: Reply | Exception) -> None:
        self.responses = list(responses)
        self.requests: list[httpx.Request] = []
        self.sleeps: list[float] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        item = self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]
        if isinstance(item, Exception):
            raise item
        return item()

    def client(self) -> UsgsClient:
        return UsgsClient(
            transport=httpx.MockTransport(self),
            sleep=self.sleeps.append,
            now=lambda: NOW,
            jitter=lambda: 0.5,
        )


def test_decodes_gzip_and_hashes_original_bytes() -> None:
    body = b'{"type": "FeatureCollection", "features": []}'
    rec = Recorder(gz(body))
    fetched = rec.client().get_json(FEED)
    assert fetched.body == body
    assert fetched.sha256 == hashlib.sha256(body).hexdigest()
    assert fetched.fetched_at == NOW
    assert rec.requests[0].headers["accept-encoding"] == "gzip"


def test_keeps_query_string_already_in_url() -> None:
    rec = Recorder(reply())
    rec.client().get_json(
        "https://earthquake.usgs.gov/fdsnws/event/1/query?eventid=x&format=geojson"
    )
    assert rec.requests[0].url.params == httpx.QueryParams({"eventid": "x", "format": "geojson"})


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/feed.geojson",
        "http://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/all_day.geojson",
        "https://earthquake.usgs.gov.example.com/x",
    ],
)
def test_rejects_url_outside_allowlist(url: str) -> None:
    rec = Recorder(reply())
    with pytest.raises(FetchError, match="allowlist"):
        rec.client().get_json(url)
    assert rec.requests == []


def test_redirect_is_error() -> None:
    rec = Recorder(reply(302, location="https://example.com/"))
    with pytest.raises(FetchError, match="redirect"):
        rec.client().get_json(FEED)
    assert len(rec.requests) == 1


def test_limit_counts_decoded_bytes() -> None:
    bomb = gzip.compress(b"0" * (JSON_LIMIT + 1))
    assert len(bomb) < 64 * 1024
    rec = Recorder(reply(200, bomb, content_encoding="gzip"))
    with pytest.raises(FetchError, match="decodificados"):
        rec.client().get_json(FEED)


def test_limit_is_inclusive() -> None:
    rec = Recorder(gz(b"0" * JSON_LIMIT))
    assert len(rec.client().get_json(FEED).body) == JSON_LIMIT


def test_limit_without_content_encoding() -> None:
    rec = Recorder(reply(200, b"0" * (JSON_LIMIT + 1)))
    with pytest.raises(FetchError, match="acima de"):
        rec.client().get_json(FEED)


@pytest.mark.parametrize(
    ("content", "encoding"),
    [(gzip.compress(b"{}" * 1000)[:-12], "gzip"), (b"not gzip", "gzip"), (b"{}", "br")],
)
def test_rejects_bad_encoding(content: bytes, encoding: str) -> None:
    rec = Recorder(reply(200, content, content_encoding=encoding))
    with pytest.raises(FetchError):
        rec.client().get_json(FEED)


def test_retry_after_in_seconds() -> None:
    rec = Recorder(reply(503, retry_after="7"), reply())
    assert rec.client().get_json(FEED).body == b"{}"
    assert rec.sleeps == [7.0]


def test_retry_after_as_http_date() -> None:
    when = format_datetime(NOW + timedelta(seconds=30), usegmt=True)
    rec = Recorder(reply(429, retry_after=when), reply())
    rec.client().get_json(FEED)
    assert rec.sleeps == [30.0]


def test_backoff_with_jitter_then_gives_up() -> None:
    rec = Recorder(reply(503))
    with pytest.raises(HttpStatusError) as err:
        rec.client().get_json(FEED)
    assert err.value.status == 503
    assert len(rec.requests) == MAX_ATTEMPTS
    assert rec.sleeps == [1.0, 2.0, 4.0]


def test_transport_error_is_retried() -> None:
    rec = Recorder(httpx.ConnectError("boom"), reply())
    assert rec.client().get_json(FEED).body == b"{}"
    assert rec.sleeps == [1.0]


@pytest.mark.parametrize("status", [404, 409])
def test_client_errors_are_not_retried(status: int) -> None:
    rec = Recorder(reply(status))
    with pytest.raises(HttpStatusError) as err:
        rec.client().get_json(FEED)
    assert err.value.status == status
    assert len(rec.requests) == 1


def test_timeouts() -> None:
    rec = Recorder(reply())
    client = rec.client()
    client.get_json(FEED)
    client.get_json(
        "https://earthquake.usgs.gov/fdsnws/event/1/query", read_timeout=FDSN_READ_TIMEOUT
    )
    assert rec.requests[0].extensions["timeout"]["connect"] == 3.0
    assert rec.requests[0].extensions["timeout"]["read"] == 20.0
    assert rec.requests[1].extensions["timeout"]["read"] == 90.0


def test_product_must_match_declared() -> None:
    body = b"<shakemap_grid/>"
    url = "https://earthquake.usgs.gov/product/shakemap/x/us/1/download/grid.xml"
    rec = Recorder(gz(body))
    client = rec.client()
    assert (
        client.get_product(url, declared_length=len(body), declared_sha256=sha256_b64(body)).body
        == body
    )
    with pytest.raises(FetchError, match="declarados"):
        client.get_product(url, declared_length=len(body), declared_sha256=sha256_b64(b"x"))
    with pytest.raises(FetchError, match="declarados"):
        client.get_product(url, declared_length=len(body) + 1, declared_sha256=sha256_b64(body))


def test_product_declared_length_checked_before_download() -> None:
    rec = Recorder(reply())
    with pytest.raises(FetchError, match="length declarado"):
        rec.client().get_product(
            "https://earthquake.usgs.gov/x/grid.xml",
            declared_length=GRID_LIMIT + 1,
            declared_sha256=sha256_b64(b""),
        )
    assert rec.requests == []


def test_tests_cannot_reach_external_network() -> None:
    # 192.0.2.0/24 é TEST-NET-1; sem o bloqueio do conftest isto viraria OSError/timeout
    with pytest.raises(RuntimeError, match="rede externa"):
        socket.create_connection(("192.0.2.1", 443), timeout=0.5)


@pytest.mark.parametrize(
    "content",
    [
        gzip.compress(b'{"a": 1}') + b"GARBAGE",
        gzip.compress(b'{"a": ') + gzip.compress(b"1}"),
        gzip.compress(b"{}") + b"\0" * (JSON_LIMIT + 1),
    ],
    ids=["trailing-garbage", "multi-member", "trailing-over-limit"],
)
def test_rejects_data_after_gzip_end(content: bytes) -> None:
    rec = Recorder(reply(200, content, content_encoding="gzip"))
    with pytest.raises(FetchError, match="fim do gzip"):
        rec.client().get_json(FEED)


def test_non_ascii_retry_after_falls_back_to_backoff() -> None:
    rec = Recorder(reply(503, retry_after="²".encode("latin-1")), reply())
    assert rec.client().get_json(FEED).body == b"{}"
    assert rec.sleeps == [1.0]
