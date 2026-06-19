"""Tests for AdminRegistry."""

from __future__ import annotations

import pytest

from asterion import CoreAdminConfig, create_admin
from asterion.extensions.errors import RegistryFrozenError
from asterion.registry import AdminRegistry, ModelAdmin


class _FakeModel:
    __tablename__ = "fake_things"


class FakeAdmin(ModelAdmin):
    model = _FakeModel
    list_display = ["id"]


def test_register_and_get():
    registry = AdminRegistry()
    registry.register(FakeAdmin)
    result = registry.get("fake_things")
    assert result is not None
    assert isinstance(result, FakeAdmin)


def test_register_instance():
    registry = AdminRegistry()
    registry.register(FakeAdmin())
    assert registry.get("fake_things") is not None


def test_get_unknown_returns_none():
    registry = AdminRegistry()
    assert registry.get("nonexistent") is None


def test_all_returns_all():
    registry = AdminRegistry()
    registry.register(FakeAdmin)
    admins = registry.all()
    assert len(admins) == 1


def test_model_names():
    registry = AdminRegistry()
    registry.register(FakeAdmin)
    assert "fake_things" in registry.model_names()


def test_no_singleton():
    r1 = AdminRegistry()
    r2 = AdminRegistry()
    r1.register(FakeAdmin)
    assert r2.get("fake_things") is None


def test_is_registered():
    registry = AdminRegistry()
    assert not registry.is_registered(_FakeModel)
    registry.register(FakeAdmin)
    assert registry.is_registered(_FakeModel)


# ---------------------------------------------------------------------------
# Freeze semantics (Robustness-Doc §8)
# ---------------------------------------------------------------------------


def test_freeze_blocks_further_registration():
    """Once frozen, ``register`` must refuse with a clear error."""
    registry = AdminRegistry()
    registry.freeze()
    with pytest.raises(RegistryFrozenError, match="AdminRegistry is frozen"):
        registry.register(FakeAdmin)


def test_freeze_is_idempotent():
    registry = AdminRegistry()
    registry.freeze()
    registry.freeze()  # second call must not error
    assert registry.is_frozen is True


def test_freeze_does_not_block_reads():
    """Reads stay open after freeze — only writes are gated. This is
    the property cached contracts / route tables rely on."""
    registry = AdminRegistry()
    registry.register(FakeAdmin)
    registry.freeze()
    assert registry.get("fake_things") is not None
    assert registry.all()
    assert registry.model_names() == ["fake_things"]
    assert registry.metadata()
    assert registry.is_registered(_FakeModel)


def test_default_is_unfrozen():
    """A fresh registry is mutable — freeze is an explicit decision
    by ``create_admin``, not a default."""
    registry = AdminRegistry()
    assert registry.is_frozen is False
    registry.register(FakeAdmin)  # must not raise


def test_create_admin_freezes_registry_after_setup():
    """End-to-end: ``create_admin`` freezes the registry after the
    user's ``register=`` callback and the extension setup phase have
    run. Subsequent register attempts (e.g. from a request handler)
    must fail."""
    app = create_admin(
        config=CoreAdminConfig(
            secret_key="x" * 64,
            database_url="sqlite+aiosqlite:///:memory:",
            environment="development",
        ),
        register=lambda reg: reg.register(FakeAdmin),
    )
    runtime = app.state.asterion
    assert runtime.registry.is_frozen is True
    assert runtime.registry.get("fake_things") is not None

    class _LateAdmin(ModelAdmin):
        model = _FakeModel
        list_display = ["id"]

    with pytest.raises(RegistryFrozenError):
        runtime.registry.register(_LateAdmin)
