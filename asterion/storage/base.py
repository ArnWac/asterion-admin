"""Storage SPI — the contract every backend implements (Roadmap P4).

Lives in core so that ``FileField`` / ``ImageField`` columns are
importable in any deployment, regardless of whether an S3/GCS extension
is installed. The framework ships :class:`LocalFileStorage` (filesystem)
as the default; concrete cloud backends live under
``asterion/extensions/storage_*``.

Design notes
------------

* **bytes-first.** Admin uploads in v1 are bytes-roundtripped — the
  router reads the multipart body, hands ``bytes`` to ``put``, and
  ``get`` returns the full payload. This keeps the protocol tiny and
  is fine for the typical 1-50 MB document/image. Streaming will be
  added when a concrete use case (video, large CSV exports) demands it.
* **Sync ``signed_url``.** Signing is a pure crypto op on the access
  key; it never hits the backend. Returning ``None`` is the explicit
  "no native signed URLs" signal — the framework's ``/storage`` route
  then proxies the bytes itself.
* **Idempotent ``delete``.** Deleting a missing key returns ``False``
  (not an error). Use ``stat`` / ``exists`` when the caller needs to
  distinguish.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class StoredObject:
    """Metadata about a stored object.

    Returned by ``put`` and ``stat`` — the canonical shape every
    backend speaks. ``etag`` is backend-defined (S3 etag, content hash
    for the local backend) and is opaque to the framework.
    """

    key: str
    size: int
    content_type: str
    etag: str | None = None
    metadata: Mapping[str, str] = field(default_factory=dict)


@runtime_checkable
class StorageBackend(Protocol):
    """The contract a storage backend implements.

    All async methods raise :class:`~asterion.storage.errors.StorageError`
    (or a subclass) on backend-level failures. Driver-specific
    exceptions must be wrapped.
    """

    #: Stable backend identifier. Used by ``runtime.storage`` lookups
    #: when multiple backends are registered and by the upload router
    #: to embed the backend tag in the stored key. Must be URL-safe
    #: and consist of lowercase ASCII letters/digits/underscore.
    name: str

    async def put(
        self,
        key: str,
        data: bytes,
        *,
        content_type: str,
        metadata: Mapping[str, str] | None = None,
    ) -> StoredObject: ...

    async def get(self, key: str) -> bytes: ...

    async def delete(self, key: str) -> bool:
        """Remove ``key``. Returns ``True`` if something was deleted,
        ``False`` if the key did not exist (idempotent success)."""
        ...

    async def exists(self, key: str) -> bool: ...

    async def stat(self, key: str) -> StoredObject: ...

    def signed_url(self, key: str, *, expires_in: int = 3600) -> str | None:
        """Return a time-limited public URL, or ``None`` if this
        backend has no native signed-URL support — the framework will
        proxy via its own ``/storage`` route in that case."""
        ...
