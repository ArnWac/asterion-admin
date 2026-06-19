"""``runtime.storage`` wiring (Roadmap P4.3).

Three paths covered:

1. explicit ``storage=`` wins
2. ``CoreAdminConfig.storage_root`` set → auto-wired LocalFileStorage
3. neither configured → ``runtime.storage`` is ``None`` (apps without
   FileField don't need storage)
"""

from __future__ import annotations

import pytest

from asterion import CoreAdminConfig, create_admin
from asterion.storage import LocalFileStorage, StorageBackend

SECRET = "test-storage-wiring-secret"


def _config(tmp_path, **kw) -> CoreAdminConfig:
    return CoreAdminConfig(
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'app.db'}",
        secret_key=SECRET,
        enable_multi_tenant=False,
        enable_builtin_ui=False,
        enable_builtin_admins=False,
        **kw,
    )


def test_no_storage_when_neither_configured(tmp_path):
    app = create_admin(config=_config(tmp_path))
    assert app.state.asterion.storage is None


def test_storage_root_auto_wires_local_filesystem(tmp_path):
    root = tmp_path / "uploads"
    app = create_admin(config=_config(tmp_path, storage_root=str(root)))
    storage = app.state.asterion.storage
    assert isinstance(storage, LocalFileStorage)
    assert isinstance(storage, StorageBackend)
    assert storage.root == root


def test_explicit_storage_wins_over_storage_root(tmp_path):
    """Passing ``storage=`` overrides config.storage_root — an
    extension that registers S3 must not be silently shadowed by a
    config default."""
    explicit_root = tmp_path / "explicit"
    explicit = LocalFileStorage(explicit_root, name="explicit")
    app = create_admin(
        config=_config(tmp_path, storage_root=str(tmp_path / "ignored")),
        storage=explicit,
    )
    assert app.state.asterion.storage is explicit
    assert app.state.asterion.storage.name == "explicit"


def test_explicit_storage_without_storage_root(tmp_path):
    custom = LocalFileStorage(tmp_path / "custom", name="custom")
    app = create_admin(config=_config(tmp_path), storage=custom)
    assert app.state.asterion.storage is custom


# ---------------------------------------------------------------------------
# Config validation
# ---------------------------------------------------------------------------


def test_storage_max_upload_bytes_must_be_positive(tmp_path):
    with pytest.raises(ValueError, match="storage_max_upload_bytes"):
        _config(tmp_path, storage_max_upload_bytes=0).validate()


def test_storage_root_default_is_none():
    """Apps with no file fields shouldn't pay a config-tax — leaving
    storage_root out is fine and yields no storage backend."""
    cfg = CoreAdminConfig(database_url="sqlite+aiosqlite:///:memory:", secret_key=SECRET)
    assert cfg.storage_root is None
    cfg.validate()  # must not raise
