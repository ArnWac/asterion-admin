"""LocalFileStorage — the default backend (Roadmap P4.1).

Covers the StorageBackend Protocol surface: put/get/delete/exists/stat
roundtrip, content-type persistence, the containment check that
prevents path-escape via ``..``, and idempotent delete semantics.
"""

from __future__ import annotations

import pytest

from asterion.storage import (
    LocalFileStorage,
    ObjectNotFound,
    StorageBackend,
    StorageRejected,
    StoredObject,
)


@pytest.fixture
def storage(tmp_path):
    return LocalFileStorage(tmp_path / "uploads")


# ---------------------------------------------------------------------------
# Protocol conformance
# ---------------------------------------------------------------------------


def test_local_storage_satisfies_protocol(storage):
    assert isinstance(storage, StorageBackend)


def test_local_storage_has_stable_name(storage):
    """The default name must be stable — keys persisted in the DB embed
    the backend name and changing it would orphan existing rows."""
    assert storage.name == "local"


def test_name_is_overridable(tmp_path):
    s = LocalFileStorage(tmp_path / "u", name="staging-local")
    assert s.name == "staging-local"


# ---------------------------------------------------------------------------
# put / get / stat roundtrip
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_put_then_get_roundtrips_bytes(storage):
    obj = await storage.put("docs/hello.txt", b"hi there", content_type="text/plain")
    assert isinstance(obj, StoredObject)
    assert obj.size == 8
    assert obj.content_type == "text/plain"
    assert obj.etag  # backend chooses the scheme, but it's set

    payload = await storage.get("docs/hello.txt")
    assert payload == b"hi there"


@pytest.mark.asyncio
async def test_stat_returns_persisted_content_type(storage):
    """Content-type survives a process restart — it's persisted in the
    sidecar, not held only in memory."""
    await storage.put("a.png", b"\x89PNG\r\n", content_type="image/png")
    stat = await storage.stat("a.png")
    assert stat.size == 6
    assert stat.content_type == "image/png"


@pytest.mark.asyncio
async def test_put_overwrites_existing_key(storage):
    """Two ``put`` calls for the same key keep the latest bytes — no
    half-written files leak through (atomic replace)."""
    await storage.put("k", b"first", content_type="text/plain")
    await storage.put("k", b"second-much-longer", content_type="text/plain")
    assert await storage.get("k") == b"second-much-longer"


# ---------------------------------------------------------------------------
# delete + exists
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_returns_true_on_existing_key(storage):
    await storage.put("gone.txt", b"x", content_type="text/plain")
    assert await storage.delete("gone.txt") is True
    assert await storage.exists("gone.txt") is False


@pytest.mark.asyncio
async def test_delete_is_idempotent(storage):
    """Deleting a missing key is success (``False``), not an error —
    matches S3 / GCS semantics."""
    assert await storage.delete("never-existed") is False


@pytest.mark.asyncio
async def test_get_missing_key_raises_object_not_found(storage):
    with pytest.raises(ObjectNotFound):
        await storage.get("nope")


@pytest.mark.asyncio
async def test_stat_missing_key_raises_object_not_found(storage):
    with pytest.raises(ObjectNotFound):
        await storage.stat("nope")


# ---------------------------------------------------------------------------
# Key validation / containment
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_empty_key_is_rejected(storage):
    with pytest.raises(StorageRejected):
        await storage.put("", b"x", content_type="text/plain")


@pytest.mark.asyncio
async def test_nul_byte_is_rejected(storage):
    with pytest.raises(StorageRejected):
        await storage.put("evil\x00key", b"x", content_type="text/plain")


@pytest.mark.asyncio
async def test_absolute_key_is_rejected(storage):
    with pytest.raises(StorageRejected):
        await storage.put("/abs/key", b"x", content_type="text/plain")


@pytest.mark.asyncio
async def test_dotdot_escape_is_rejected(storage):
    """The whole point of containment: ``../../etc/passwd`` cannot
    write outside the storage root."""
    with pytest.raises(StorageRejected):
        await storage.put("../../etc/passwd", b"x", content_type="text/plain")


@pytest.mark.asyncio
async def test_backslash_keys_resolve_to_posix(storage):
    """Windows-style separators are accepted but mapped to POSIX so
    the same key works regardless of the OS the framework runs on."""
    await storage.put("a\\b\\c.txt", b"win", content_type="text/plain")
    # Same logical key wins, regardless of which separator was used.
    assert await storage.get("a/b/c.txt") == b"win"


# ---------------------------------------------------------------------------
# signed_url contract
# ---------------------------------------------------------------------------


def test_local_storage_returns_no_native_signed_url(storage):
    """Local backend has no native scheme — returning ``None`` is the
    signal to the framework to proxy bytes through ``/storage``."""
    assert storage.signed_url("anything") is None
    assert storage.signed_url("anything", expires_in=60) is None
