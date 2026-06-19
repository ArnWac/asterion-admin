"""S3 storage backend (Roadmap P4.6).

Runs against ``moto`` (in-process S3 mock). Skipped entirely when
``moto`` or ``boto3`` aren't installed — those are optional dev
extras, not part of the framework's hard dependencies.

Covers Protocol conformance, put/get/stat roundtrip, delete
semantics (True on existing, False on missing — same contract as
LocalFileStorage), 404 mapping, signed-URL generation, and the
"None when expires_in <= 0" opt-out.
"""

from __future__ import annotations

import pytest

# Module-level skip: these dev-only deps aren't shipped with the
# framework — if they're not installed, the whole file is invisible
# to pytest. Importing inside the test bodies would cost us a clear
# "what's missing?" signal at collect time.
boto3 = pytest.importorskip("boto3")
moto = pytest.importorskip("moto")

from moto import mock_aws

from asterion.extensions.storage_s3 import S3StorageBackend
from asterion.storage import (
    ObjectNotFound,
    StorageBackend,
    StoredObject,
)

_BUCKET = "asterion-test"
_REGION = "us-east-1"


@pytest.fixture
def s3_backend():
    """Mock S3 + pre-created bucket. Each test gets a fresh moto
    context so state never leaks between tests."""
    with mock_aws():
        client = boto3.client("s3", region_name=_REGION)
        client.create_bucket(Bucket=_BUCKET)
        backend = S3StorageBackend(bucket=_BUCKET, region_name=_REGION)
        yield backend


# ---------------------------------------------------------------------------
# Protocol conformance
# ---------------------------------------------------------------------------


def test_s3_backend_satisfies_protocol(s3_backend):
    assert isinstance(s3_backend, StorageBackend)


def test_s3_backend_default_name_is_s3(s3_backend):
    assert s3_backend.name == "s3"


def test_name_is_overridable():
    with mock_aws():
        boto3.client("s3", region_name=_REGION).create_bucket(Bucket=_BUCKET)
        backend = S3StorageBackend(bucket=_BUCKET, region_name=_REGION, name="prod-uploads")
        assert backend.name == "prod-uploads"


# ---------------------------------------------------------------------------
# Construction guards
# ---------------------------------------------------------------------------


def test_missing_bucket_is_rejected():
    with mock_aws():
        with pytest.raises(ValueError, match="bucket"):
            S3StorageBackend(bucket="", region_name=_REGION)


def test_missing_region_is_rejected():
    with mock_aws():
        with pytest.raises(ValueError, match="region"):
            S3StorageBackend(bucket=_BUCKET, region_name="")


# ---------------------------------------------------------------------------
# put / get / stat roundtrip
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_put_then_get_roundtrips_bytes(s3_backend):
    obj = await s3_backend.put(
        "uploads/2026/05/cover.png",
        b"hello s3",
        content_type="image/png",
    )
    assert isinstance(obj, StoredObject)
    assert obj.size == 8
    assert obj.content_type == "image/png"
    assert obj.etag  # boto3 always sets an ETag

    payload = await s3_backend.get("uploads/2026/05/cover.png")
    assert payload == b"hello s3"


@pytest.mark.asyncio
async def test_stat_reads_content_type_back(s3_backend):
    await s3_backend.put("a.txt", b"hi", content_type="text/plain")
    stat = await s3_backend.stat("a.txt")
    assert stat.size == 2
    assert stat.content_type == "text/plain"


@pytest.mark.asyncio
async def test_put_overwrites_existing_key(s3_backend):
    await s3_backend.put("k", b"first", content_type="text/plain")
    await s3_backend.put("k", b"second", content_type="text/plain")
    assert await s3_backend.get("k") == b"second"


@pytest.mark.asyncio
async def test_put_with_custom_metadata_roundtrips(s3_backend):
    await s3_backend.put(
        "k",
        b"x",
        content_type="text/plain",
        metadata={"owner": "alice", "tag": "draft"},
    )
    stat = await s3_backend.stat("k")
    assert stat.metadata.get("owner") == "alice"
    assert stat.metadata.get("tag") == "draft"


# ---------------------------------------------------------------------------
# delete + exists semantics (must match LocalFileStorage)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_returns_true_on_existing_key(s3_backend):
    await s3_backend.put("gone", b"x", content_type="text/plain")
    assert await s3_backend.delete("gone") is True
    assert await s3_backend.exists("gone") is False


@pytest.mark.asyncio
async def test_delete_missing_key_returns_false(s3_backend):
    """Same Protocol semantics as LocalFileStorage — idempotent
    success, not an error."""
    assert await s3_backend.delete("never-existed") is False


@pytest.mark.asyncio
async def test_exists_distinguishes_present_and_absent(s3_backend):
    await s3_backend.put("here", b"x", content_type="text/plain")
    assert await s3_backend.exists("here") is True
    assert await s3_backend.exists("not_here") is False


# ---------------------------------------------------------------------------
# Error mapping — driver exceptions must not leak
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_missing_key_raises_object_not_found(s3_backend):
    with pytest.raises(ObjectNotFound):
        await s3_backend.get("nope")


@pytest.mark.asyncio
async def test_stat_missing_key_raises_object_not_found(s3_backend):
    with pytest.raises(ObjectNotFound):
        await s3_backend.stat("nope")


# ---------------------------------------------------------------------------
# signed_url
# ---------------------------------------------------------------------------


def test_signed_url_returns_presigned_get(s3_backend):
    url = s3_backend.signed_url("uploads/a.png", expires_in=600)
    assert url is not None
    # Presigned URL pins: includes the bucket + key, carries the
    # expiry parameter, points at S3.
    assert _BUCKET in url
    assert "uploads/a.png" in url
    # boto3 presigned URLs always carry an Expires/X-Amz-Expires param.
    assert "Expires" in url or "X-Amz-Expires" in url


def test_signed_url_returns_none_when_expires_in_zero(s3_backend):
    """``expires_in=0`` is the documented "use the framework proxy
    route" signal — keeps deployments that want every read
    auth-checked from accidentally handing out direct S3 URLs."""
    assert s3_backend.signed_url("k", expires_in=0) is None
    assert s3_backend.signed_url("k", expires_in=-1) is None
