"""HTTP surface for the storage backend (Roadmap P4.4).

Two endpoints, both mounted under ``admin_api_prefix`` (default
``/api/v1/admin``) so they share the admin auth path:

``POST /api/v1/admin/_storage/upload``
    Multipart upload. Returns the storage key the caller writes into a
    :class:`FileField`. The key is opaque — currently
    ``YYYY/MM/{uuid4hex}`` for time partitioning + entropy — but the
    framework treats it as a black box. Size cap enforced from
    ``CoreAdminConfig.storage_max_upload_bytes``.

``GET /api/v1/admin/_storage/{key:path}``
    Serves stored bytes. Used by the UI as a fallback when the backend
    has no native signed-URL scheme (:class:`LocalFileStorage`). Auth
    is required — admin uploads are not public.

Why not its own prefix?
-----------------------

Mounting under ``admin_api_prefix`` reuses the existing
``require_admin_context`` chain (which already validates the access
token + tenant). A separate prefix would duplicate that wiring and
make CORS / cookie scoping more complex for no benefit.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import (
    APIRouter,
    Depends,
    File,
    HTTPException,
    Request,
    Response,
    UploadFile,
    status,
)

from asterion.admin.context import AdminContext, require_admin_context
from asterion.storage.base import StorageBackend
from asterion.storage.errors import ObjectNotFound, StorageRejected

router = APIRouter()

_DEFAULT_CONTENT_TYPE = "application/octet-stream"


def _storage(request: Request) -> StorageBackend:
    """Resolve the runtime's storage backend or 503 if none was wired.

    Returning 503 — not 500 — is deliberate: the app booted fine, but
    the optional storage capability isn't configured. The caller's
    next step is "wire ``storage_root`` or pass ``storage=``", not
    "file a bug".
    """
    runtime = request.app.state.asterion
    storage = getattr(runtime, "storage", None)
    if storage is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "Storage backend not configured. "
                "Set CoreAdminConfig.storage_root or pass storage= to create_admin()."
            ),
        )
    return storage


def _mint_key() -> str:
    """``YYYY/MM/{uuid4hex}`` — short, partitioned, no filename leak.

    Time partition keeps directory fan-out reasonable on filesystems
    that don't love millions of siblings (ext4, NTFS) and helps S3
    spread keys across hash partitions. UUID4 hex (32 chars) gives
    122 bits of entropy — collision-free in practice.
    """
    now = datetime.now(UTC)
    return f"{now.year:04d}/{now.month:02d}/{uuid.uuid4().hex}"


@router.post("/_storage/upload")
async def upload(
    request: Request,
    file: UploadFile = File(...),
    ctx: AdminContext = Depends(require_admin_context),
) -> dict:
    """Accept a multipart upload and return the storage key + metadata.

    The wire shape mirrors :class:`StoredObject` so the caller (admin
    form) can stash it directly into the FileField on save.
    """
    storage = _storage(request)
    cfg = request.app.state.asterion.config

    # Fast-fail on Content-Length when the client advertised one — saves
    # us reading 100 MB just to reject it. The handler still re-checks
    # after read() because Content-Length can lie.
    advertised = request.headers.get("content-length")
    if advertised is not None:
        try:
            if int(advertised) > cfg.storage_max_upload_bytes:
                raise HTTPException(
                    status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                    detail=(
                        f"Upload exceeds storage_max_upload_bytes "
                        f"({cfg.storage_max_upload_bytes} bytes)."
                    ),
                )
        except ValueError:
            # Malformed header — let the read path enforce the cap.
            pass

    data = await file.read()

    if len(data) == 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Empty upload.",
        )
    if len(data) > cfg.storage_max_upload_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail=(
                f"Upload exceeds storage_max_upload_bytes ({cfg.storage_max_upload_bytes} bytes)."
            ),
        )

    content_type = (file.content_type or _DEFAULT_CONTENT_TYPE).strip()
    key = _mint_key()

    try:
        obj = await storage.put(key, data, content_type=content_type)
    except StorageRejected as exc:
        # Backend-side validation (e.g. S3 bucket policy) said no — map
        # to 400 because the request itself is at fault, not the server.
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

    return {
        "key": obj.key,
        "size": obj.size,
        "content_type": obj.content_type,
        "etag": obj.etag,
        "filename": file.filename,
    }


@router.get("/_storage/{key:path}")
async def serve(
    request: Request,
    key: str,
    ctx: AdminContext = Depends(require_admin_context),
) -> Response:
    """Stream a stored object back to the client.

    Used by the UI to render a download link when the backend has no
    native signed-URL scheme. Cloud backends that DO expose signed
    URLs typically bypass this route entirely — the form embeds the
    signed URL directly.
    """
    storage = _storage(request)

    try:
        # ``stat`` first so we can set Content-Length / Content-Type
        # headers correctly without buffering everything before the
        # client sees a byte. For LocalFileStorage stat is cheap.
        meta = await storage.stat(key)
        data = await storage.get(key)
    except ObjectNotFound as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Stored object not found: {key}",
        ) from exc

    return Response(
        content=data,
        media_type=meta.content_type or _DEFAULT_CONTENT_TYPE,
        headers={"Content-Length": str(meta.size)},
    )
