"""Filesystem-backed :class:`StorageBackend` — the default (Roadmap P4).

Writes to a configured root directory; keys map 1:1 to relative paths.
Safe to use in production for single-node deployments; multi-node or
horizontally-scaled apps want :mod:`asterion.extensions.storage_s3`
(or another shared backend) instead.

Security
--------

* Paths are normalised and a containment check rejects ``..`` escapes
  before any write/read — see :func:`_resolve`.
* Empty keys, NUL bytes, and absolute paths are rejected outright.
* ``signed_url`` returns ``None``: this backend has no out-of-band URL
  scheme, so the framework's ``/storage/{key}`` route proxies bytes.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
from collections.abc import Mapping
from pathlib import Path, PurePosixPath

from asterion.storage.base import StoredObject
from asterion.storage.errors import ObjectNotFound, StorageRejected

_DEFAULT_CONTENT_TYPE = "application/octet-stream"
_META_SUFFIX = ".meta"


def _validate_key(key: str) -> None:
    if not key or not key.strip():
        raise StorageRejected("storage key must not be empty")
    if "\x00" in key:
        raise StorageRejected("storage key must not contain NUL")
    # We accept POSIX-style relative keys only — Windows backslashes
    # are normalised by PurePosixPath below but absolute keys
    # (``/foo`` or ``C:\foo``) are rejected.
    if key.startswith("/") or (len(key) >= 2 and key[1] == ":"):
        raise StorageRejected("storage key must be relative")


def _resolve(root: Path, key: str) -> Path:
    """Resolve ``key`` against ``root`` and refuse to escape it.

    ``Path.resolve`` is intentionally NOT used (the file may not exist
    yet for writes). Instead we resolve only after joining, on the
    parent dir, and compare against the resolved root.
    """
    _validate_key(key)
    safe_key = PurePosixPath(key.replace("\\", "/")).as_posix()
    candidate = (root / safe_key).resolve(strict=False)
    root_resolved = root.resolve(strict=False)
    try:
        candidate.relative_to(root_resolved)
    except ValueError as exc:
        raise StorageRejected(f"key escapes storage root: {key!r}") from exc
    return candidate


def _meta_path(path: Path) -> Path:
    return path.with_name(path.name + _META_SUFFIX)


class LocalFileStorage:
    """Filesystem backend rooted at ``root``.

    The directory is created lazily on first write. Content-type is
    persisted alongside the data file in a small ``.meta`` sidecar so
    ``stat`` / ``get`` can return it without sniffing.
    """

    def __init__(self, root: str | os.PathLike[str], *, name: str = "local") -> None:
        self._root = Path(root)
        self.name = name

    @property
    def root(self) -> Path:
        return self._root

    async def put(
        self,
        key: str,
        data: bytes,
        *,
        content_type: str,
        metadata: Mapping[str, str] | None = None,
    ) -> StoredObject:
        path = _resolve(self._root, key)

        def _write() -> StoredObject:
            path.parent.mkdir(parents=True, exist_ok=True)
            # Atomic-ish: write to ``.tmp`` then replace. Avoids a half-
            # written file being read on the get path during overwrite.
            tmp = path.with_name(path.name + ".tmp")
            tmp.write_bytes(data)
            os.replace(tmp, path)
            _meta_path(path).write_text(content_type, encoding="utf-8")
            etag = hashlib.sha256(data).hexdigest()
            return StoredObject(
                key=key,
                size=len(data),
                content_type=content_type,
                etag=etag,
                metadata=dict(metadata or {}),
            )

        return await asyncio.to_thread(_write)

    async def get(self, key: str) -> bytes:
        path = _resolve(self._root, key)
        if not path.exists():
            raise ObjectNotFound(key)
        return await asyncio.to_thread(path.read_bytes)

    async def delete(self, key: str) -> bool:
        path = _resolve(self._root, key)

        def _unlink() -> bool:
            if not path.exists():
                return False
            path.unlink()
            meta = _meta_path(path)
            if meta.exists():
                meta.unlink()
            return True

        return await asyncio.to_thread(_unlink)

    async def exists(self, key: str) -> bool:
        path = _resolve(self._root, key)
        return await asyncio.to_thread(path.exists)

    async def stat(self, key: str) -> StoredObject:
        path = _resolve(self._root, key)
        if not path.exists():
            raise ObjectNotFound(key)

        def _read_meta() -> StoredObject:
            content_type = _DEFAULT_CONTENT_TYPE
            meta = _meta_path(path)
            if meta.exists():
                content_type = meta.read_text(encoding="utf-8").strip() or _DEFAULT_CONTENT_TYPE
            size = path.stat().st_size
            return StoredObject(
                key=key,
                size=size,
                content_type=content_type,
            )

        return await asyncio.to_thread(_read_meta)

    def signed_url(self, key: str, *, expires_in: int = 3600) -> str | None:
        # Local backend has no native signed-URL scheme; the framework
        # serves bytes via its own ``/storage`` proxy route.
        return None
