"""Blobs das capturas: gzip dos bytes originais, endereçados pelo sha256 desses bytes."""

import gzip
import hashlib
import os
from pathlib import Path


class BlobStore:
    def __init__(self, root: Path) -> None:
        self.root = root

    def put(self, body: bytes) -> tuple[str, str]:
        """Grava (arquivo temporário, fsync, rename) e devolve (sha256, caminho relativo)."""
        sha = hashlib.sha256(body).hexdigest()
        rel = f"{sha[:2]}/{sha}.gz"
        path = self.root / rel
        if path.exists():
            return sha, rel
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(f".tmp-{os.getpid()}")
        with open(tmp, "wb") as f:
            f.write(gzip.compress(body, mtime=0))
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
        _fsync_dir(path.parent)
        return sha, rel

    def get(self, rel: str) -> bytes:
        return gzip.decompress((self.root / rel).read_bytes())


def _fsync_dir(directory: Path) -> None:
    if os.name == "nt":  # o Windows não abre diretório para fsync; os testes rodam no host
        return
    fd = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
