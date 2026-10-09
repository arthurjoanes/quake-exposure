"""PostGIS real num projeto Compose próprio, derrubado no fim da sessão. USGS falso, sem rede."""

import gzip
import json
import os
import secrets
import shutil
import subprocess
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import psycopg
import pytest
from psycopg import sql

from quakeexposure.cli import SECRET_NAMES
from quakeexposure.db import ROOT, connect
from quakeexposure.ingestion.blobs import BlobStore
from quakeexposure.ingestion.collector import Collector
from quakeexposure.ingestion.http import UsgsClient
from quakeexposure.migrate import migrate

from .cdc_support import cdc_stack as cdc_stack

PROJECT = f"qe-it-{uuid.uuid4().hex[:8]}"


@dataclass(frozen=True)
class Database:
    secrets_dir: Path
    external: bool = False

    def connect(self, user: str = "qe_pipeline") -> psycopg.Connection[Any]:
        return connect(user=user, password_file=str(self.secrets_dir / user))


def _compose(env: dict[str, str], *args: str) -> str:
    cmd = ["docker", "compose", "-p", PROJECT, "-f", str(ROOT / "compose.yaml")]
    cmd += ["-f", str(ROOT / "compose.test.yaml"), *args]
    done = subprocess.run(cmd, env=env, capture_output=True, text=True, check=False)  # noqa: S603
    if done.returncode != 0:
        raise RuntimeError(f"{' '.join(args)}: {done.stderr[-2000:]}")
    return done.stdout


def _external_database(secrets_dir: Path) -> Database:
    """Service PostgreSQL descartável de CI; o fixture não chama Docker nesse modo."""
    (secrets_dir / "postgres").write_text(os.environ["QE_TEST_POSTGRES_PASSWORD"], "utf-8")
    with psycopg.connect(
        host=os.environ.get("QE_DB_HOST", "127.0.0.1"),
        port=int(os.environ.get("QE_DB_PORT", "5432")),
        dbname="qe",
        user="postgres",
        password=os.environ["QE_TEST_POSTGRES_PASSWORD"],
        autocommit=True,
    ) as admin:
        for role in ("qe_owner", "qe_pipeline", "qe_api_read"):
            if admin.execute("SELECT 1 FROM pg_roles WHERE rolname=%s", (role,)).fetchone() is None:
                admin.execute(sql.SQL("CREATE ROLE {} LOGIN").format(sql.Identifier(role)))
            admin.execute(
                sql.SQL("ALTER ROLE {} PASSWORD {}").format(
                    sql.Identifier(role), sql.Literal((secrets_dir / role).read_text("utf-8"))
                )
            )
        admin.execute("ALTER DATABASE qe OWNER TO qe_owner")
        admin.execute("CREATE EXTENSION IF NOT EXISTS postgis")
        admin.execute("CREATE EXTENSION IF NOT EXISTS btree_gist")
        admin.execute("ALTER ROLE qe_pipeline SET statement_timeout = '60s'")
        admin.execute("ALTER ROLE qe_pipeline SET idle_in_transaction_session_timeout = '60s'")
        admin.execute("ALTER ROLE qe_api_read SET default_transaction_read_only = on")
        admin.execute("ALTER ROLE qe_api_read SET statement_timeout = '10s'")
    db = Database(secrets_dir, external=True)
    with db.connect("qe_owner") as conn:
        migrate(conn)
    return db


@pytest.fixture(scope="session")
def database(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Database]:
    secrets_dir = tmp_path_factory.mktemp("secrets")
    for name in SECRET_NAMES:
        (secrets_dir / name).write_text(secrets.token_urlsafe(18), "utf-8")
    if os.environ.get("QE_TEST_EXTERNAL_DATABASE") == "1":
        yield _external_database(secrets_dir)
        return
    docker = shutil.which("docker")
    if docker is None or subprocess.run([docker, "info"], capture_output=True).returncode != 0:  # noqa: S603
        pytest.skip("Docker indisponível: os testes de integração usam o PostGIS real")
    env = {**os.environ, "QE_SECRETS_DIR": str(secrets_dir)}
    try:
        _compose(env, "up", "-d", "--wait", "platform-db")
        port = _compose(env, "port", "platform-db", "5432").strip().rsplit(":", 1)[1]
        os.environ["QE_DB_HOST"] = "127.0.0.1"
        os.environ["QE_DB_PORT"] = port
        db = Database(secrets_dir)
        with db.connect("qe_owner") as conn:
            migrate(conn)
        yield db
    finally:
        _compose(env, "down", "-v", "--remove-orphans")


@pytest.fixture
def conn(database: Database) -> Iterator[psycopg.Connection[Any]]:
    """Conexão do papel qe_pipeline com as tabelas vazias."""
    with database.connect("qe_owner") as owner:
        tables = [
            f"{s}.{t}"
            for s, t in owner.execute(
                """SELECT table_schema, table_name FROM information_schema.tables
                   WHERE table_schema IN (
                       'control', 'quake', 'portfolio', 'exposure', 'serving', 'privacy'
                   )
                     AND table_type = 'BASE TABLE'
                     AND table_name <> 'schema_migration'"""
            )
        ]
        owner.execute(f"TRUNCATE {', '.join(tables)} RESTART IDENTITY CASCADE")
    with database.connect() as c:
        yield c


Reply = tuple[int, bytes, dict[str, str]] | Exception


def ok(body: bytes | dict[str, Any], **headers: str) -> Reply:
    data = json.dumps(body).encode() if isinstance(body, dict) else body
    return 200, data, headers


@dataclass
class FakeUsgs:
    """Rotas do USGS em memória. Cada rota devolve as respostas em sequência (a última repete)."""

    routes: list[tuple[Callable[[httpx.Request], bool], list[Reply]]] = field(default_factory=list)
    requests: list[httpx.Request] = field(default_factory=list)

    def on(self, match: str | Callable[[httpx.Request], bool], *replies: Reply) -> None:
        test = match if callable(match) else (lambda r, m=match: str(r.url).startswith(m))
        self.routes.insert(0, (test, list(replies)))

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        for test, replies in self.routes:
            if test(request):
                reply = replies.pop(0) if len(replies) > 1 else replies[0]
                if isinstance(reply, Exception):
                    raise reply
                status, body, headers = reply
                if headers.get("content-encoding") == "gzip" and not body.startswith(b"\x1f\x8b"):
                    body = gzip.compress(body)
                return httpx.Response(status, headers=headers, stream=httpx.ByteStream(body))
        return httpx.Response(404, stream=httpx.ByteStream(b"not found"))

    def urls(self) -> list[str]:
        return [str(r.url) for r in self.requests]


@dataclass
class Clock:
    now: datetime = datetime(2026, 9, 22, 12, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **kwargs: float) -> None:
        self.now += timedelta(**kwargs)


@dataclass
class Harness:
    conn: psycopg.Connection[Any]
    usgs: FakeUsgs
    clock: Clock
    blobs: BlobStore
    sleeps: list[float]
    collector: Collector

    def one(self, sql: str, *params: Any) -> Any:
        row = self.conn.execute(sql, params).fetchone()
        return None if row is None else row[0]

    def count(self, table: str, where: str = "true", *params: Any) -> int:
        return int(self.one(f"SELECT count(*) FROM {table} WHERE {where}", *params))  # noqa: S608


@pytest.fixture
def harness(conn: psycopg.Connection[Any], tmp_path: Path) -> Harness:
    usgs = FakeUsgs()
    clock = Clock()
    sleeps: list[float] = []
    client = UsgsClient(
        transport=httpx.MockTransport(usgs), sleep=sleeps.append, now=clock, jitter=lambda: 0.5
    )
    blobs = BlobStore(tmp_path / "blobs")
    return Harness(conn, usgs, clock, blobs, sleeps, Collector(conn, client, blobs, clock))
