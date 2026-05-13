"""File storage backends.

Default: LocalStorage writes files to a local directory and serves them via a
configurable URL prefix.

S3-compatible storage requires ``pip install boto3``.

Usage::

    from adminfoundry.storage import storage, configure

    configure(LocalStorage(base_dir="uploads", base_url="/uploads"))

    saved_path = await storage.save("avatars/user123.png", file_obj)
    url = storage.url(saved_path)   # "/uploads/avatars/user123.png"
    await storage.delete(saved_path)
"""
from __future__ import annotations

import shutil
import uuid
from pathlib import Path
from typing import Any, BinaryIO, Protocol


class StorageBackend(Protocol):
    async def save(self, path: str, file: BinaryIO) -> str: ...
    async def delete(self, path: str) -> None: ...
    def url(self, path: str) -> str: ...


class LocalStorage:
    """Stores files on the local filesystem."""

    def __init__(self, base_dir: str = "uploads", base_url: str = "/uploads") -> None:
        self.base_dir = Path(base_dir)
        self.base_url = base_url.rstrip("/")
        self.base_dir.mkdir(parents=True, exist_ok=True)

    async def save(self, path: str, file: BinaryIO) -> str:
        dest = self.base_dir / path
        dest.parent.mkdir(parents=True, exist_ok=True)
        with open(dest, "wb") as f:
            shutil.copyfileobj(file, f)
        return path

    async def delete(self, path: str) -> None:
        dest = self.base_dir / path
        if dest.exists():
            dest.unlink()

    def url(self, path: str) -> str:
        return f"{self.base_url}/{path}"


def _make_unique_path(filename: str, prefix: str = "") -> str:
    """Generate a collision-resistant storage path."""
    suffix = Path(filename).suffix.lower()
    name = uuid.uuid4().hex
    parts = [prefix, name + suffix] if prefix else [name + suffix]
    return "/".join(parts)


# Module-level singleton — replaced by configure()
storage: LocalStorage = LocalStorage()


def configure(backend: Any) -> None:
    """Replace the active storage backend."""
    global storage
    storage = backend


def generate_path(filename: str, prefix: str = "") -> str:
    """Return a unique storage path for *filename*."""
    return _make_unique_path(filename, prefix)


# Backward-compat: S3Storage lives in adminfoundry.extensions.storage_s3 but is
# re-exported here so `from adminfoundry.storage import S3Storage` keeps working.
# boto3 is only imported inside S3Storage.__init__, so this line does not pull
# boto3 at module import time.
from adminfoundry.extensions.storage_s3 import S3Storage  # noqa: E402, F401
