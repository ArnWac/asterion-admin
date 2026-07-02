"""Platform-tier keys use the ``platform.*`` namespace in the catalog (ADR-0004).

A ``superadmin_only`` admin is a platform-tier resource: its permission keys are
``platform.<res>.<action>`` (assignable to platform roles), never
``admin.<res>.<action>`` (which tenant seeding would hand to a tenant owner).
Every other admin stays tenant-tier.
"""

from __future__ import annotations

from sqlalchemy import Integer, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from asterion.authz.catalog import generate_permission_keys
from asterion.registry import AdminRegistry, ModelAdmin


class _Base(DeclarativeBase):
    pass


class _Post(_Base):
    __tablename__ = "cat_posts"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(200), default="")


class _Secret(_Base):
    __tablename__ = "cat_secrets"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    body: Mapped[str] = mapped_column(String(200), default="")


class _PostAdmin(ModelAdmin):
    model = _Post


class _SecretAdmin(ModelAdmin):
    model = _Secret
    superadmin_only = True


def _keys() -> set[str]:
    reg = AdminRegistry()
    reg.register(_PostAdmin)
    reg.register(_SecretAdmin)
    return generate_permission_keys(reg)


def test_tenant_admin_emits_admin_namespace_keys():
    keys = _keys()
    assert "admin.cat_posts.list" in keys
    assert "admin.cat_posts.create" in keys
    # A tenant-tier admin never leaks a platform key.
    assert not any(k.startswith("platform.cat_posts") for k in keys)


def test_superadmin_only_admin_emits_platform_namespace_keys():
    keys = _keys()
    assert "platform.cat_secrets.list" in keys
    assert "platform.cat_secrets.read" in keys
    # And it does NOT emit tenant-assignable admin.* keys for the same resource —
    # those would be seeded onto tenant roles yet are unreachable (the route is
    # platform-gated), i.e. dead + dangerous.
    assert not any(k.startswith("admin.cat_secrets") for k in keys)
