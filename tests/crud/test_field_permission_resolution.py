"""Roadmap 2.1 — unified field-visibility resolution.

Pins the two primitives that consolidate the three field-visibility
mechanisms (``protected_fields`` / ``readonly_fields`` /
``AdminPolicy.field_permission``) into a single decision:

* :meth:`FieldPermission.strictest` — combine permissions, most
  restrictive wins.
* :func:`static_field_permission` — translate the static admin config
  (protected → HIDDEN, calculated/readonly/auto → READ) into a
  FieldPermission.
"""

from __future__ import annotations

from sqlalchemy import Column, Integer, String
from sqlalchemy.orm import DeclarativeBase

from asterion.admin.policy import FieldPermission, static_field_permission
from asterion.registry import ModelAdmin

# ---------------------------------------------------------------------------
# FieldPermission.strictest
# ---------------------------------------------------------------------------


def test_strictest_empty_defaults_to_write():
    assert FieldPermission.strictest() is FieldPermission.WRITE


def test_strictest_picks_most_restrictive():
    W, R, H = FieldPermission.WRITE, FieldPermission.READ, FieldPermission.HIDDEN
    assert FieldPermission.strictest(W, R) is R
    assert FieldPermission.strictest(R, W) is R
    assert FieldPermission.strictest(W, H) is H
    assert FieldPermission.strictest(R, H) is H
    assert FieldPermission.strictest(W, R, H) is H
    assert FieldPermission.strictest(W, W) is W


def test_strictest_is_order_independent():
    W, R, H = FieldPermission.WRITE, FieldPermission.READ, FieldPermission.HIDDEN
    assert FieldPermission.strictest(H, R, W) is FieldPermission.strictest(W, R, H)


def test_rank_ordering():
    assert FieldPermission.WRITE._rank < FieldPermission.READ._rank
    assert FieldPermission.READ._rank < FieldPermission.HIDDEN._rank


# ---------------------------------------------------------------------------
# static_field_permission
# ---------------------------------------------------------------------------


class _Base(DeclarativeBase):
    pass


class _Doc(_Base):
    __tablename__ = "fpr_docs"
    id = Column(Integer, primary_key=True)
    title = Column(String(100), nullable=False)
    secret = Column(String(100), nullable=True)
    created_at = Column(String(40), nullable=True)
    locked_field = Column(String(40), nullable=True)


class _DocAdmin(ModelAdmin):
    model = _Doc
    protected_fields = ["secret"]
    readonly_fields = ["locked_field"]
    calculated_fields = {"display_name": lambda obj: obj.title}


def test_static_protected_field_is_hidden():
    assert static_field_permission(_DocAdmin(), "secret") is FieldPermission.HIDDEN


def test_static_globally_protected_field_is_hidden():
    # hashed_password is in the global DEFAULT_PROTECTED_FIELDS seed.
    assert static_field_permission(_DocAdmin(), "hashed_password") is FieldPermission.HIDDEN


def test_static_readonly_field_is_read():
    assert static_field_permission(_DocAdmin(), "locked_field") is FieldPermission.READ


def test_static_auto_column_is_read():
    """``created_at`` is an auto-managed column even though it's not in
    the admin's readonly_fields list."""
    assert static_field_permission(_DocAdmin(), "created_at") is FieldPermission.READ
    assert static_field_permission(_DocAdmin(), "id") is FieldPermission.READ


def test_static_calculated_field_is_read():
    assert static_field_permission(_DocAdmin(), "display_name") is FieldPermission.READ


def test_static_plain_writable_field_is_write():
    assert static_field_permission(_DocAdmin(), "title") is FieldPermission.WRITE


def test_static_protected_beats_readonly():
    """A field that is both protected and readonly resolves to HIDDEN —
    protection is the strictest static class and wins."""

    class _Admin(ModelAdmin):
        model = _Doc
        protected_fields = ["locked_field"]
        readonly_fields = ["locked_field"]

    assert static_field_permission(_Admin(), "locked_field") is FieldPermission.HIDDEN
