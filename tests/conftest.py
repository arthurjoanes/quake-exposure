import gzip
import json
import socket
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

FIXTURES = Path(__file__).parent / "fixtures"
MANIFEST: list[dict[str, Any]] = json.loads((FIXTURES / "manifest.json").read_text("utf-8"))[
    "fixtures"
]
LOOPBACK = {"127.0.0.1", "::1", "localhost"}


@pytest.fixture(autouse=True)
def _no_external_network(monkeypatch: pytest.MonkeyPatch) -> None:
    connect = socket.socket.connect

    def guarded(sock: socket.socket, address: Any) -> None:
        if isinstance(address, tuple) and address[0] not in LOOPBACK:
            raise RuntimeError(f"teste tentou acessar a rede externa: {address}")
        connect(sock, address)

    monkeypatch.setattr(socket.socket, "connect", guarded)


def load_fixture(name: str) -> bytes:
    entry = next(e for e in MANIFEST if e["name"] == name)
    path = FIXTURES / entry["path"]
    if not entry["in_git"] and not path.exists():
        pytest.skip(f"{name} não vai para o Git: uv run python scripts/fetch_fixtures.py pull")
    return gzip.decompress(path.read_bytes())


@pytest.fixture
def usgs_fixture() -> Callable[[str], bytes]:
    return load_fixture


@pytest.fixture
def usgs_json() -> Callable[[str], Any]:
    return lambda name: json.loads(load_fixture(name))
