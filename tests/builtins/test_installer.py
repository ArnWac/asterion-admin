"""Tests for builtin admin installer."""

from __future__ import annotations

from asterion.builtins.installer import install_builtin_admins
from asterion.registry import AdminRegistry


def test_install_registers_tenant_role_admins():
    registry = AdminRegistry()
    install_builtin_admins(registry)
    names = registry.model_names()
    assert "tenant_roles" in names
    assert "tenant_role_permissions" in names
    assert "tenant_membership_roles" in names


def test_install_skips_already_registered():
    registry = AdminRegistry()
    install_builtin_admins(registry)
    install_builtin_admins(registry)  # second call should not raise
    assert registry.model_names().count("tenant_roles") == 1


def test_install_with_extra_admin():
    from asterion.registry import ModelAdmin

    class _FakeModel:
        __tablename__ = "custom_things"

    class CustomAdmin(ModelAdmin):
        model = _FakeModel

    registry = AdminRegistry()
    install_builtin_admins(registry, extra_admins=(CustomAdmin,))
    assert "custom_things" in registry.model_names()


def test_skip_tenant_admins():
    registry = AdminRegistry()
    install_builtin_admins(
        registry,
        include_tenant_admins=False,
        include_audit_admins=False,
    )
    assert registry.model_names() == []


def test_install_registers_audit_admin_by_default():
    """Roadmap 5.1 — every app with enable_builtin_admins gets the
    Audit-UI for free. Pin the table name so a rename is loud."""
    registry = AdminRegistry()
    install_builtin_admins(registry)
    assert "audit_logs" in registry.model_names()


def test_skip_audit_admins():
    registry = AdminRegistry()
    install_builtin_admins(
        registry,
        include_audit_admins=False,
    )
    assert "audit_logs" not in registry.model_names()
    # Tenant admins still install — independent flag.
    assert "tenant_roles" in registry.model_names()
