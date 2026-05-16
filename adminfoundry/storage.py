"""File storage backends.

Default: LocalStorage writes files to a local directory and serves them via a
configurable URL prefix.

S3-compatible storage requires ``pip install boto3``.

Usage::

    storage = request.app.state.adminfoundry.storage
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


def generate_path(filename: str, prefix: str = "") -> str:
    """Return a unique storage path for *filename*."""
    return _make_unique_path(filename, prefix)
