import gzip
import hashlib
from pathlib import Path

import pytest

from quakeexposure.ingestion.blobs import BlobIntegrityError, BlobStore


def test_intact_blob_is_reused_and_checked(tmp_path: Path) -> None:
    blobs = BlobStore(tmp_path)
    body = b'{"capture": "original"}'
    sha, rel = blobs.put(body)
    assert sha == hashlib.sha256(body).hexdigest()
    assert blobs.get(rel, expected_sha256=sha) == body
    original = (tmp_path / rel).read_bytes()
    assert blobs.put(body) == (sha, rel)
    assert (tmp_path / rel).read_bytes() == original


def test_replaced_valid_gzip_is_rejected_and_preserved(tmp_path: Path) -> None:
    blobs = BlobStore(tmp_path)
    body = b'{"capture": "original"}'
    sha, rel = blobs.put(body)
    suspect = gzip.compress(b'{"capture": "different"}', mtime=0)
    (tmp_path / rel).write_bytes(suspect)
    with pytest.raises(BlobIntegrityError):
        blobs.get(rel, expected_sha256=sha)
    with pytest.raises(BlobIntegrityError):
        blobs.put(body)
    assert (tmp_path / rel).read_bytes() == suspect


def test_expected_hash_comes_from_capture_not_only_filename(tmp_path: Path) -> None:
    blobs = BlobStore(tmp_path)
    _, rel = blobs.put(b"actual")
    with pytest.raises(BlobIntegrityError):
        blobs.get(rel, expected_sha256=hashlib.sha256(b"expected").hexdigest())


def test_truncated_gzip_is_not_reused(tmp_path: Path) -> None:
    blobs = BlobStore(tmp_path)
    sha, rel = blobs.put(b"original")
    path = tmp_path / rel
    path.write_bytes(path.read_bytes()[:-5])
    with pytest.raises(EOFError):
        blobs.get(rel, expected_sha256=sha)
    with pytest.raises(EOFError):
        blobs.put(b"original")
