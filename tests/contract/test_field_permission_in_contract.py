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

import pytest
from sqlalchemy import Column, Integer, String
from sqlalchemy.orm import DeclarativeBase

from adminfoundry.admin.context import AdminContext
from adminfoundry.admin.policy import AdminPolicy, FieldPermission
from adminfoundry.contract.service import (
    build_field_metadata,
    build_model_contract,
    compute_field_permissions,
)
from adminfoundry.providers.base import AdminPrincipal
from adminfoundry.registry import ModelAdmin


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


def test_no_policy_means_empty_permission_map():
    """``compute_field_permissions`` short-circuits when no policy is
    attached — saves a per-column await on the cheap path."""
    import asyncio

    perms = asyncio.run(compute_field_permissions(_BareAdmin(), _ctx()))
    assert perms == {}


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
    contract = build_model_contract(
        _HideSalaryAdmin(), field_permissions={"salary": "hidden"}
    )
    names = [f.name for f in contract.fields]
    assert "salary" not in names


# ---------------------------------------------------------------------------
# Policy yields READ → field visible + read_only=True
# ---------------------------------------------------------------------------


def test_read_permission_flips_read_only_and_field_permission():
    """``READ`` keeps the field in the wire but locks editing. The
    UI uses both flags: ``field_permission`` to label the field,
    ``read_only`` to actually disable the input."""
    metas = build_field_metadata(
        _BareAdmin(), field_permissions={"private_note": "read"}
    )
    note = _by_name(metas, "private_note")
    assert note.field_permission == "read"
    assert note.read_only is True


def test_read_permission_does_not_force_other_fields_readonly():
    """Per-field decisions are independent — pinning only the named
    field flips, the rest stay write."""
    metas = build_field_metadata(
        _BareAdmin(), field_permissions={"private_note": "read"}
    )
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
    metas = build_field_metadata(
        _WithCalculated(), field_permissions={"display_name": "hidden"}
    )
    names = [m.name for m in metas]
    assert "display_name" not in names


# ---------------------------------------------------------------------------
# compute_field_permissions runs the policy
# ---------------------------------------------------------------------------


def test_compute_field_permissions_runs_policy_for_each_column():
    """End-to-end: the helper calls the admin's policy once per
    column, async, and returns the string-valued map."""
    import asyncio

    perms = asyncio.run(
        compute_field_permissions(_HideSalaryAdmin(), _ctx("user"))
    )
    assert perms["salary"] == "hidden"
    assert perms["title"] == "write"


def test_compute_field_permissions_skips_when_ctx_none():
    """Defensive: callers that haven't built a ctx (test paths,
    background jobs) get an empty map without an async policy hop."""
    import asyncio

    perms = asyncio.run(compute_field_permissions(_HideSalaryAdmin(), None))
    assert perms == {}
