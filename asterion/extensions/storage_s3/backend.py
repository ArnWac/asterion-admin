"""S3-compatible storage backend (Roadmap P4.6).

Implements :class:`~asterion.storage.StorageBackend` against any
S3 API — real AWS, MinIO, Garage, R2, B2 (S3-compat endpoint). The
SDK (``boto3``) is imported lazily inside :meth:`S3StorageBackend.__init__`
so importing this module without ``boto3`` installed is fine; only
constructing the backend raises.

Why ``asyncio.to_thread`` and not ``aiobotocore``
-------------------------------------------------

``boto3`` is sync. The framework's Protocol is async. We dispatch the
sync client calls through ``asyncio.to_thread`` — single dependency,
no extra adapter library, identical concurrency story to
:class:`~asterion.storage.LocalFileStorage` (which dispatches
filesystem I/O the same way). When throughput needs change,
swapping to ``aiobotocore`` is a behind-the-Protocol detail.

Signed URLs
-----------

``signed_url`` returns a presigned GET URL valid for ``expires_in``
seconds. The default is 1h — long enough for normal admin browsing,
short enough that a leaked URL has limited blast radius. Set
``expires_in`` to ``0`` if you want the framework's proxy
``/storage/{key}`` route instead (returning ``None`` triggers that
fallback in the UI).
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from asterion.storage.base import StoredObject
from asterion.storage.errors import (
    ObjectNotFound,
    StorageError,
    StorageRejected,
)

if TYPE_CHECKING:  # pragma: no cover
    pass


_DEFAULT_CONTENT_TYPE = "application/octet-stream"


def _import_boto3() -> tuple[Any, Any]:
    """Pull boto3 + the ClientError class lazily.

    Kept in a helper so the import-error message can be customised
    (the default ``ModuleNotFoundError`` doesn't point users at the
    extras entry to install). Returns ``(boto3_module, ClientError)``.
    """
    try:
        import boto3  # type: ignore[import-not-found]
        from botocore.exceptions import ClientError  # type: ignore[import-not-found]
    except ImportError as exc:
        raise ImportError(
            "S3StorageBackend requires the 'boto3' package. "
            "Install with: pip install asterion-admin[storage-s3]"
        ) from exc
    return boto3, ClientError


class S3StorageBackend:
    """``StorageBackend`` against any S3-compatible object store.

    Parameters
    ----------
    bucket
        Bucket name. Must already exist — the backend never creates
        buckets (that's an infra concern, not a runtime one).
    region_name
        AWS region. Required for AWS; some S3-compat services accept
        ``us-east-1`` as a placeholder.
    endpoint_url
        Override for non-AWS S3 endpoints (MinIO, R2, ...). ``None``
        uses the default AWS endpoint for the region.
    aws_access_key_id / aws_secret_access_key
        Credentials. ``None`` defers to boto3's normal credential
        chain (env vars, IAM role, ``~/.aws/credentials``) — the
        recommended path for production.
    name
        Stable identifier persisted alongside stored keys; defaults
        to ``"s3"``. Override when running multiple S3 backends in
        the same app.
    """

    def __init__(
        self,
        *,
        bucket: str,
        region_name: str,
        endpoint_url: str | None = None,
        aws_access_key_id: str | None = None,
        aws_secret_access_key: str | None = None,
        name: str = "s3",
    ) -> None:
        if not bucket or not bucket.strip():
            raise ValueError("S3StorageBackend: bucket is required")
        if not region_name or not region_name.strip():
            raise ValueError("S3StorageBackend: region_name is required")

        boto3, client_error = _import_boto3()
        self._client_error = client_error
        self._client = boto3.client(
            "s3",
            region_name=region_name,
            endpoint_url=endpoint_url,
            aws_access_key_id=aws_access_key_id,
            aws_secret_access_key=aws_secret_access_key,
        )
        self._bucket = bucket
        self.name = name

    # -----------------------------------------------------------------
    # internal: translate boto3 errors into our Protocol exceptions
    # -----------------------------------------------------------------

    def _is_not_found(self, exc: Exception) -> bool:
        if not isinstance(exc, self._client_error):
            return False
        # head_object / get_object 404s come back differently on AWS
        # vs S3-compat services — check both shapes.
        meta = getattr(exc, "response", {}).get("Error", {}) or {}
        code = str(meta.get("Code", ""))
        return code in {"404", "NoSuchKey", "NotFound"}

    def _wrap(self, exc: Exception) -> StorageError:
        """Wrap a boto3 ClientError in our Protocol's exception type
        without leaking the driver type to callers."""
        return StorageError(f"S3 backend error: {exc}")

    # -----------------------------------------------------------------
    # StorageBackend
    # -----------------------------------------------------------------

    async def put(
        self,
        key: str,
        data: bytes,
        *,
        content_type: str,
        metadata: Mapping[str, str] | None = None,
    ) -> StoredObject:
        if not key or not key.strip():
            raise StorageRejected("storage key must not be empty")

        kwargs: dict[str, Any] = {
            "Bucket": self._bucket,
            "Key": key,
            "Body": data,
            "ContentType": content_type or _DEFAULT_CONTENT_TYPE,
        }
        if metadata:
            # S3 metadata values are strings; the caller is expected
            # to coerce — we don't silently cast non-strings.
            kwargs["Metadata"] = dict(metadata)

        try:
            resp = await asyncio.to_thread(self._client.put_object, **kwargs)
        except Exception as exc:  # boto3.ClientError + transport errors
            if isinstance(exc, self._client_error):
                raise self._wrap(exc) from exc
            raise

        # ETag in boto3 is wrapped in quotes; strip them so the value
        # is a plain hex string consistent with LocalFileStorage's
        # sha256.
        etag = (resp.get("ETag") or "").strip('"') or None
        return StoredObject(
            key=key,
            size=len(data),
            content_type=content_type or _DEFAULT_CONTENT_TYPE,
            etag=etag,
            metadata=dict(metadata or {}),
        )

    async def get(self, key: str) -> bytes:
        try:
            resp = await asyncio.to_thread(self._client.get_object, Bucket=self._bucket, Key=key)
        except Exception as exc:
            if self._is_not_found(exc):
                raise ObjectNotFound(key) from exc
            if isinstance(exc, self._client_error):
                raise self._wrap(exc) from exc
            raise

        body = resp["Body"]
        return await asyncio.to_thread(body.read)

    async def delete(self, key: str) -> bool:
        # S3 delete is idempotent — succeeds whether or not the key
        # exists. To mirror LocalFileStorage's "True if removed,
        # False if missing" contract we head_object first.
        try:
            await asyncio.to_thread(self._client.head_object, Bucket=self._bucket, Key=key)
        except Exception as exc:
            if self._is_not_found(exc):
                return False
            if isinstance(exc, self._client_error):
                raise self._wrap(exc) from exc
            raise

        try:
            await asyncio.to_thread(self._client.delete_object, Bucket=self._bucket, Key=key)
        except Exception as exc:
            if isinstance(exc, self._client_error):
                raise self._wrap(exc) from exc
            raise
        return True

    async def exists(self, key: str) -> bool:
        try:
            await asyncio.to_thread(self._client.head_object, Bucket=self._bucket, Key=key)
            return True
        except Exception as exc:
            if self._is_not_found(exc):
                return False
            if isinstance(exc, self._client_error):
                raise self._wrap(exc) from exc
            raise

    async def stat(self, key: str) -> StoredObject:
        try:
            resp = await asyncio.to_thread(self._client.head_object, Bucket=self._bucket, Key=key)
        except Exception as exc:
            if self._is_not_found(exc):
                raise ObjectNotFound(key) from exc
            if isinstance(exc, self._client_error):
                raise self._wrap(exc) from exc
            raise

        return StoredObject(
            key=key,
            size=int(resp.get("ContentLength", 0)),
            content_type=str(resp.get("ContentType") or _DEFAULT_CONTENT_TYPE),
            etag=(resp.get("ETag") or "").strip('"') or None,
            metadata=dict(resp.get("Metadata") or {}),
        )

    def signed_url(self, key: str, *, expires_in: int = 3600) -> str | None:
        """Presigned GET URL or ``None`` (when ``expires_in <= 0``).

        ``None`` is the documented opt-out: the framework's proxy
        ``/storage/{key}`` route will be used instead. Useful when
        the deployment wants every read to go through admin auth.
        """
        if expires_in <= 0:
            return None
        return self._client.generate_presigned_url(
            "get_object",
            Params={"Bucket": self._bucket, "Key": key},
            ExpiresIn=expires_in,
        )
