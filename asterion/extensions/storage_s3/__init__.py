"""S3-compatible storage backend extension (Roadmap P4.6).

Plug-in backend for :class:`asterion.storage.StorageBackend`. The
extension does NOT auto-mount routes or contribute to the admin
context — it just exposes :class:`S3StorageBackend`, which the host
app passes to ``create_admin(storage=...)``::

    from asterion import create_admin
    from asterion.extensions.storage_s3 import S3StorageBackend

    storage = S3StorageBackend(
        bucket="my-admin-uploads",
        region_name="eu-central-1",
    )
    app = create_admin(config=..., storage=storage)

That's it — :class:`FileField` columns, the ``/storage/upload`` route,
and the ``/storage/{key}`` serve route all work transparently against
S3 instead of the local filesystem.

Dependencies
------------

Requires ``boto3``. Install via the optional extras::

    pip install asterion-admin[storage-s3]

Importing this module without ``boto3`` is safe (the dependency is
loaded lazily inside :class:`S3StorageBackend.__init__`); only
constructing the backend raises a clear :class:`ImportError`
pointing at the install command.
"""

from __future__ import annotations

from asterion.extensions.storage_s3.backend import S3StorageBackend

__all__ = ["S3StorageBackend"]
