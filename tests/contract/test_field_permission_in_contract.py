"""Roadmap 2.4 — FieldPermission visible in the contract.

Before 2.4: ``FieldPermission`` only shaped the write schema + serialized
response. A client UI could not learn from the contract whether the
current caller may write a specific field — it had to discover it by
trial-and-error on PATCH.

After 2.4: each :class:`FieldMeta` carries a ``field_permission``
string (``"write"``, ``"read"``, ``"hidden"``) reflecting what THIS
caller can do. ``"hidden"`` fields are dropped from the output
entirely (same shape as protected fields); ``"read"`` also flips
``read_only=True``.

The async ``compute_field_permissions`` helper runs the admin's
:class:`AdminPolicy` once per request; sync ``build_model_contract``
stamps the resulting map into the FieldMeta entries.
"""

from __future__ import annotations

from sqlalchemy import Column, Integer, String
from sqlalchemy.orm import DeclarativeBase

from asterion.admin.context import AdminContext
from asterion.admin.policy import AdminPolicy, FieldPermission
from asterion.contract.service import (
    build_field_metadata,
    build_model_contract,
    compute_field_permissions,
)
from asterion.providers.base import AdminPrincipal
from asterion.registry import ModelAdmin


class _Base(DeclarativeBase):
    pass


class _Doc(_Base):
    __tablename__ = "fp_docs"
    id = Column(Integer, primary_key=True)
    title = Column(String(100), nullable=False)
    salary = Column(Integer, nullable=True)
    private_note = Column(String(200), nullable=True)


def _ctx(role: str = "user") -> AdminContext:
    return AdminContext(
        request=None,
        principal=AdminPrincipal(id=role),
        tenant=None,
        roles=frozenset({role}),
    )


def _by_name(metas, name):
    return next(m for m in metas if m.name == name)


# ---------------------------------------------------------------------------
# No policy → default "write" everywhere
# ---------------------------------------------------------------------------


class _BareAdmin(ModelAdmin):
    model = _Doc


def test_no_policy_defaults_to_write():
    metas = build_field_metadata(_BareAdmin())
    for m in metas:
        assert m.field_permission == "write"


def test_no_policy_returns_static_classification():
    """Roadmap 2.1: ``compute_field_permissions`` now always returns a
    populated map reflecting the static field class, even with no
    policy attached. The PK ``id`` resolves to ``"read"`` (auto-managed
    column); plain writable columns to ``"write"``."""
    import asyncio

    perms = asyncio.run(compute_field_permissions(_BareAdmin(), _ctx()))
    assert perms["id"] == "read"  # primary key → read-only static class
    assert perms["title"] == "write"
    assert perms["salary"] == "write"
    assert perms["private_note"] == "write"


# ---------------------------------------------------------------------------
# Policy yields HIDDEN → field dropped
# ---------------------------------------------------------------------------


class _HideSalaryPolicy(AdminPolicy):
    async def field_permission(self, field, obj, ctx):
        if field == "salary" and "manager" not in ctx.roles:
            return FieldPermission.HIDDEN
        return FieldPermission.WRITE


class _HideSalaryAdmin(ModelAdmin):
    model = _Doc
    policy = _HideSalaryPolicy()


def test_hidden_field_dropped_from_contract():
    """Per-caller HIDDEN → field is absent entirely. Matches the
    existing protected-fields contract."""
    perms = {"salary": "hidden"}
    metas = build_field_metadata(_HideSalaryAdmin(), field_permissions=perms)
    names = [m.name for m in metas]
    assert "salary" not in names
    assert "title" in names


def test_hidden_field_dropped_from_model_contract():
    """End-to-end through ``build_model_contract``."""
    contract = build_model_contract(_HideSalaryAdmin(), field_permissions={"salary": "hidden"})
    names = [f.name for f in contract.fields]
    assert "salary" not in names


# ---------------------------------------------------------------------------
# Policy yields READ → field visible + read_only=True
# ---------------------------------------------------------------------------


def test_read_permission_flips_read_only_and_field_permission():
    """``READ`` keeps the field in the wire but locks editing. The
    UI uses both flags: ``field_permission`` to label the field,
    ``read_only`` to actually disable the input."""
    metas = build_field_metadata(_BareAdmin(), field_permissions={"private_note": "read"})
    note = _by_name(metas, "private_note")
    assert note.field_permission == "read"
    assert note.read_only is True


def test_read_permission_does_not_force_other_fields_readonly():
    """Per-field decisions are independent — pinning only the named
    field flips, the rest stay write."""
    metas = build_field_metadata(_BareAdmin(), field_permissions={"private_note": "read"})
    title = _by_name(metas, "title")
    assert title.field_permission == "write"
    assert title.read_only is False


# ---------------------------------------------------------------------------
# Default "write" — unchanged behavior
# ---------------------------------------------------------------------------


def test_write_permission_leaves_field_meta_unchanged():
    metas_default = build_field_metadata(_BareAdmin())
    metas_explicit = build_field_metadata(
        _BareAdmin(), field_permissions={"title": "write", "salary": "write"}
    )
    # Same fields, same flags — explicit "write" is the same as not
    # supplying anything.
    assert {m.name for m in metas_default} == {m.name for m in metas_explicit}
    for name in ("title", "salary", "private_note"):
        d = _by_name(metas_default, name)
        e = _by_name(metas_explicit, name)
        assert d.field_permission == e.field_permission == "write"
        assert d.read_only == e.read_only


# ---------------------------------------------------------------------------
# Calculated fields obey the same rules
# ---------------------------------------------------------------------------


class _WithCalculated(ModelAdmin):
    model = _Doc
    calculated_fields = {"display_name": lambda obj: obj.title.upper()}


def test_calculated_field_default_permission_is_read():
    """Calculated fields are inherently read-only — they have no
    underlying column to write back to. ``field_permission="read"``
    documents that in the contract."""
    metas = build_field_metadata(_WithCalculated())
    calc = _by_name(metas, "display_name")
    assert calc.calculated is True
    assert calc.field_permission == "read"
    assert calc.read_only is True


def test_calculated_field_can_be_hidden_per_caller():
    metas = build_field_metadata(_WithCalculated(), field_permissions={"display_name": "hidden"})
    names = [m.name for m in metas]
    assert "display_name" not in names


# ---------------------------------------------------------------------------
# compute_field_permissions runs the policy
# ---------------------------------------------------------------------------


def test_compute_field_permissions_runs_policy_for_each_column():
    """End-to-end: the helper calls the admin's policy once per
    column, async, and returns the string-valued map."""
    import asyncio

    perms = asyncio.run(compute_field_permissions(_HideSalaryAdmin(), _ctx("user")))
    assert perms["salary"] == "hidden"
    assert perms["title"] == "write"


def test_compute_field_permissions_ctx_none_skips_policy_keeps_static():
    """Roadmap 2.1: with no ctx the async policy hop is skipped, but
    the static classification still applies — so ``salary`` is NOT
    hidden (that was a policy decision) yet the PK ``id`` is still
    ``"read"`` (static class). A policy can only tighten on top of
    the static base; without a ctx there's no tightening."""
    import asyncio

    perms = asyncio.run(compute_field_permissions(_HideSalaryAdmin(), None))
    assert perms["salary"] == "write"  # policy not run → static WRITE
    assert perms["id"] == "read"  # static PK class still applies


def test_policy_cannot_loosen_static_readonly():
    """Roadmap 2.1 strictest contract: a policy returning WRITE for a
    statically read-only field (PK) must NOT loosen it — the combined
    result stays ``read``."""
    import asyncio

    class _LoosenPolicy(AdminPolicy):
        async def field_permission(self, field, obj, ctx):
            return FieldPermission.WRITE  # tries to grant write to everything

    class _Admin(ModelAdmin):
        model = _Doc
        policy = _LoosenPolicy()

    perms = asyncio.run(compute_field_permissions(_Admin(), _ctx()))
    # id is a PK → static READ; policy WRITE cannot loosen it.
    assert perms["id"] == "read"
