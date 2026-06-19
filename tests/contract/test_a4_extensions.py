"""A4 contract extensions: widget/required/help_text + capabilities.

Tests the new first-class FieldMeta fields and the per-user
CapabilitiesMeta block. Contract-version bumped to ``"2"``.
"""

from __future__ import annotations

import pytest
from sqlalchemy import Column, DateTime, Integer, String, Text, func
from sqlalchemy.orm import DeclarativeBase

from asterion.actions import AdminAction
from asterion.contract.service import (
    CONTRACT_VERSION,
    build_field_metadata,
    build_model_contract,
)
from asterion.registry import ModelAdmin


class _Base(DeclarativeBase):
    pass


class _Post(_Base):
    __tablename__ = "a4_posts"
    id = Column(Integer, primary_key=True)
    title = Column(String(200), nullable=False, doc="Public post title.")
    summary = Column(String(500), nullable=True)
    body = Column(Text, nullable=False)
    status = Column(String(20), nullable=False, server_default="draft")
    created_at = Column(DateTime, nullable=False, server_default=func.now())


class _PublishAction(AdminAction):
    name = "publish"
    label = "Publish selected"

    async def execute(self, records, session, user):  # pragma: no cover
        return {"summary": "ok", "affected": len(records)}


class _ArchiveAction(AdminAction):
    name = "archive"
    label = "Archive selected"

    async def execute(self, records, session, user):  # pragma: no cover
        return {"summary": "ok", "affected": len(records)}


class _PostAdmin(ModelAdmin):
    model = _Post
    list_display = ["id", "title", "status"]
    actions = [_PublishAction(), _ArchiveAction()]


def _meta_by_name(metas, name):
    return next(m for m in metas if m.name == name)


# ---------------------------------------------------------------------------
# Contract version
# ---------------------------------------------------------------------------


def test_contract_version_is_v2():
    """A4 bumps the contract version. Pin it so a future bump becomes
    deliberate."""
    assert CONTRACT_VERSION == "2"
    contract = build_model_contract(_PostAdmin())
    assert contract.contract_version == "2"


# ---------------------------------------------------------------------------
# required
# ---------------------------------------------------------------------------


def test_required_true_for_nonnull_no_default():
    metas = build_field_metadata(_PostAdmin())
    assert _meta_by_name(metas, "title").required is True
    assert _meta_by_name(metas, "body").required is True


def test_required_false_for_nullable_field():
    metas = build_field_metadata(_PostAdmin())
    assert _meta_by_name(metas, "summary").required is False


def test_required_false_for_server_default():
    """A column with server_default is not required from the client —
    the DB fills it in. Critical for ``status`` and ``created_at``
    style fields where the server owns the value."""
    metas = build_field_metadata(_PostAdmin())
    assert _meta_by_name(metas, "status").required is False
    assert _meta_by_name(metas, "created_at").required is False


def test_required_false_for_primary_key():
    """Primary keys are server-generated → not required from clients."""
    metas = build_field_metadata(_PostAdmin())
    assert _meta_by_name(metas, "id").required is False


class _ReadonlyAdmin(ModelAdmin):
    model = _Post
    readonly_fields = ["title"]


def test_required_false_for_readonly_field():
    """A field listed in readonly_fields cannot be written by clients
    so it must not be marked required either, even if the underlying
    column is non-nullable. Otherwise the form is unsubmittable."""
    metas = build_field_metadata(_ReadonlyAdmin())
    assert _meta_by_name(metas, "title").required is False


# ---------------------------------------------------------------------------
# help_text
# ---------------------------------------------------------------------------


def test_help_text_picked_from_column_doc():
    metas = build_field_metadata(_PostAdmin())
    assert _meta_by_name(metas, "title").help_text == "Public post title."


def test_help_text_none_when_column_has_no_doc():
    metas = build_field_metadata(_PostAdmin())
    assert _meta_by_name(metas, "summary").help_text is None


# ---------------------------------------------------------------------------
# widget
# ---------------------------------------------------------------------------


def test_widget_promoted_from_adapter_metadata():
    """A4 promotes ``widget`` from adapter metadata into a top-level
    FieldMeta field. The leftover metadata dict no longer carries the
    widget key — clients should read ``field.widget`` directly."""
    metas = build_field_metadata(_PostAdmin())
    body = _meta_by_name(metas, "body")
    assert body.widget == "textarea"
    assert "widget" not in body.metadata


def test_widget_none_when_adapter_did_not_supply_one():
    metas = build_field_metadata(_PostAdmin())
    title = _meta_by_name(metas, "title")
    assert title.widget is None


# ---------------------------------------------------------------------------
# capabilities — no permission set (None) → all True
# ---------------------------------------------------------------------------


def test_capabilities_default_to_all_true_when_no_permissions():
    """Without a permission context, we cannot narrow capabilities, so
    every flag stays True. Pre-A4 wire-shape consumers see no behavior
    change because they ignored the missing block entirely."""
    contract = build_model_contract(_PostAdmin())
    assert contract.capabilities.create is True
    assert contract.capabilities.update is True
    assert contract.capabilities.delete is True
    assert set(contract.capabilities.bulk_actions) == {"publish", "archive"}


# ---------------------------------------------------------------------------
# capabilities — narrowed by caller permissions
# ---------------------------------------------------------------------------


def test_capabilities_create_off_when_permission_missing():
    perms = frozenset(
        {
            "admin.a4_posts.list",
            "admin.a4_posts.read",
        }
    )
    contract = build_model_contract(_PostAdmin(), permissions=perms)
    assert contract.capabilities.create is False
    assert contract.capabilities.update is False
    assert contract.capabilities.delete is False
    assert contract.capabilities.bulk_actions == []


def test_capabilities_filter_bulk_actions_by_permission():
    """A user with ``admin.a4_posts.publish`` but not ``admin.a4_posts.archive``
    must only see ``publish`` in the bulk-action list, even though both
    are declared on the admin."""
    perms = frozenset(
        {
            "admin.a4_posts.publish",
            "admin.a4_posts.read",
        }
    )
    contract = build_model_contract(_PostAdmin(), permissions=perms)
    assert contract.capabilities.bulk_actions == ["publish"]


def test_capabilities_respect_wildcard_permissions():
    """``admin.a4_posts.*`` should grant every CRUD + every action.
    Wildcards are part of the existing permission grammar — capability
    computation must use the same matcher."""
    perms = frozenset({"admin.a4_posts.*"})
    contract = build_model_contract(_PostAdmin(), permissions=perms)
    assert contract.capabilities.create is True
    assert contract.capabilities.update is True
    assert contract.capabilities.delete is True
    assert set(contract.capabilities.bulk_actions) == {"publish", "archive"}


def test_capabilities_respect_global_wildcard():
    perms = frozenset({"admin.*"})
    contract = build_model_contract(_PostAdmin(), permissions=perms)
    assert contract.capabilities.create is True
    assert contract.capabilities.update is True
    assert contract.capabilities.delete is True
    assert set(contract.capabilities.bulk_actions) == {"publish", "archive"}


# ---------------------------------------------------------------------------
# placeholder (Roadmap 5.4)
# ---------------------------------------------------------------------------


class _PlaceholderAdmin(ModelAdmin):
    model = _Post
    placeholders = {"title": "e.g. My first post", "summary": "Short teaser"}


def test_placeholder_emitted_from_admin_mapping():
    metas = build_field_metadata(_PlaceholderAdmin())
    assert _meta_by_name(metas, "title").placeholder == "e.g. My first post"
    assert _meta_by_name(metas, "summary").placeholder == "Short teaser"


def test_placeholder_none_when_not_configured():
    """Fields without a placeholders entry — and admins that set none —
    report ``None`` so the renderer shows no placeholder."""
    metas = build_field_metadata(_PostAdmin())
    assert _meta_by_name(metas, "title").placeholder is None
    # Even on the placeholder admin, an unlisted field stays None.
    metas2 = build_field_metadata(_PlaceholderAdmin())
    assert _meta_by_name(metas2, "body").placeholder is None


# ---------------------------------------------------------------------------
# conditional fields (Roadmap 5.4)
# ---------------------------------------------------------------------------


class _ConditionAdmin(ModelAdmin):
    model = _Post
    field_conditions = {
        # valid: equals rule referencing an existing field
        "summary": {"field": "status", "equals": "published"},
        # valid: in rule
        "body": {"field": "status", "in": ["draft", "review"]},
        # dropped: references a field that isn't in the contract
        "title": {"field": "does_not_exist", "equals": 1},
    }


def test_condition_equals_emitted():
    metas = build_field_metadata(_ConditionAdmin())
    assert _meta_by_name(metas, "summary").condition == {
        "field": "status",
        "equals": "published",
    }


def test_condition_in_emitted():
    metas = build_field_metadata(_ConditionAdmin())
    assert _meta_by_name(metas, "body").condition == {
        "field": "status",
        "in": ["draft", "review"],
    }


def test_condition_with_dangling_reference_dropped():
    """A rule pointing at a non-existent field degrades to 'always
    visible' (condition None) instead of shipping an unevaluable rule."""
    metas = build_field_metadata(_ConditionAdmin())
    assert _meta_by_name(metas, "title").condition is None


def test_condition_none_when_not_configured():
    metas = build_field_metadata(_PostAdmin())
    assert _meta_by_name(metas, "summary").condition is None


@pytest.mark.parametrize(
    "bad",
    [
        {"field": "status"},  # no equals/in
        {"field": "status", "equals": 1, "in": [1]},  # both
        {"equals": 1},  # no field
        {"field": "status", "in": "notalist"},  # in not a list
        "notadict",
    ],
)
def test_malformed_condition_dropped(bad):
    class _A(ModelAdmin):
        model = _Post
        field_conditions = {"summary": bad}

    metas = build_field_metadata(_A())
    assert _meta_by_name(metas, "summary").condition is None


# ---------------------------------------------------------------------------
# list_badges (Roadmap 5.5)
# ---------------------------------------------------------------------------


def test_list_badges_emitted_and_stringified():
    class _BadgeAdmin(ModelAdmin):
        model = _Post
        list_badges = {"status": {"published": "success", "draft": "neutral"}}

    contract = build_model_contract(_BadgeAdmin())
    assert contract.list_badges == {"status": {"published": "success", "draft": "neutral"}}


def test_list_badges_drops_unknown_styles():
    class _BadgeAdmin(ModelAdmin):
        model = _Post
        list_badges = {"status": {"published": "success", "draft": "rainbow"}}

    contract = build_model_contract(_BadgeAdmin())
    # "rainbow" isn't in the allowed vocabulary → dropped; column kept.
    assert contract.list_badges == {"status": {"published": "success"}}


def test_list_badges_column_dropped_when_all_styles_invalid():
    class _BadgeAdmin(ModelAdmin):
        model = _Post
        list_badges = {"status": {"published": "rainbow"}}

    contract = build_model_contract(_BadgeAdmin())
    assert contract.list_badges == {}


def test_list_badges_stringifies_non_string_values():
    class _BadgeAdmin(ModelAdmin):
        model = _Post
        list_badges = {"id": {1: "info", 2: "danger"}}

    contract = build_model_contract(_BadgeAdmin())
    assert contract.list_badges == {"id": {"1": "info", "2": "danger"}}


def test_list_badges_default_empty():
    contract = build_model_contract(_PostAdmin())
    assert contract.list_badges == {}


# ---------------------------------------------------------------------------
# dependent fields (Roadmap 5.4)
# ---------------------------------------------------------------------------


def test_dependency_emitted_and_stringified():
    class _A(ModelAdmin):
        model = _Post
        field_dependencies = {"summary": {"field": "status", "options": {"published": ["a", "b"]}}}

    metas = build_field_metadata(_A())
    assert _meta_by_name(metas, "summary").dependency == {
        "field": "status",
        "options": {"published": ["a", "b"]},
    }


def test_dependency_dangling_controlling_field_dropped():
    class _A(ModelAdmin):
        model = _Post
        field_dependencies = {"summary": {"field": "nope", "options": {"x": ["y"]}}}

    metas = build_field_metadata(_A())
    assert _meta_by_name(metas, "summary").dependency is None


@pytest.mark.parametrize(
    "bad",
    [
        {"field": "status"},  # no options
        {"options": {"x": ["y"]}},  # no field
        {"field": "status", "options": "notadict"},
        "notadict",
    ],
)
def test_dependency_malformed_dropped(bad):
    class _A(ModelAdmin):
        model = _Post
        field_dependencies = {"summary": bad}

    metas = build_field_metadata(_A())
    assert _meta_by_name(metas, "summary").dependency is None


def test_dependency_default_none():
    metas = build_field_metadata(_PostAdmin())
    assert _meta_by_name(metas, "summary").dependency is None


# ---------------------------------------------------------------------------
# widget override (Roadmap 5.4)
# ---------------------------------------------------------------------------


def test_widget_override_replaces_adapter_widget():
    class _A(ModelAdmin):
        model = _Post
        widgets = {"summary": "textarea"}

    metas = build_field_metadata(_A())
    assert _meta_by_name(metas, "summary").widget == "textarea"


def test_widget_override_only_affects_named_fields():
    class _A(ModelAdmin):
        model = _Post
        widgets = {"summary": "textarea"}

    metas = build_field_metadata(_A())
    # title got no override → keeps its adapter default (None here).
    assert _meta_by_name(metas, "title").widget is None


def test_widget_override_default_noop():
    metas = build_field_metadata(_PostAdmin())
    assert _meta_by_name(metas, "summary").widget is None


# ---------------------------------------------------------------------------
# list_editable (Roadmap 5.5)
# ---------------------------------------------------------------------------


def test_list_editable_keeps_writable_display_columns():
    class _A(ModelAdmin):
        model = _Post
        list_display = ["id", "title", "status"]
        list_editable = ["title", "status"]

    assert build_model_contract(_A()).list_editable == ["title", "status"]


def test_list_editable_drops_columns_not_in_list_display():
    class _A(ModelAdmin):
        model = _Post
        list_display = ["id", "title"]
        list_editable = ["title", "status"]  # status not displayed

    assert build_model_contract(_A()).list_editable == ["title"]


def test_list_editable_drops_primary_key_and_readonly():
    class _A(ModelAdmin):
        model = _Post
        list_display = ["id", "title", "status"]
        readonly_fields = ["status"]
        list_editable = ["id", "status", "title"]  # id is PK, status read-only

    assert build_model_contract(_A()).list_editable == ["title"]


def test_list_editable_preserves_declaration_order():
    class _A(ModelAdmin):
        model = _Post
        list_display = ["id", "title", "status", "summary"]
        list_editable = ["summary", "title"]

    assert build_model_contract(_A()).list_editable == ["summary", "title"]


def test_list_editable_default_empty():
    assert build_model_contract(_PostAdmin()).list_editable == []


# ---------------------------------------------------------------------------
# date_hierarchy (Roadmap 5.5)
# ---------------------------------------------------------------------------


def test_date_hierarchy_emitted_for_datetime_column():
    class _A(ModelAdmin):
        model = _Post
        date_hierarchy = "created_at"

    assert build_model_contract(_A()).date_hierarchy == "created_at"


def test_date_hierarchy_none_for_non_date_column():
    class _A(ModelAdmin):
        model = _Post
        date_hierarchy = "title"  # String column

    assert build_model_contract(_A()).date_hierarchy is None


def test_date_hierarchy_none_for_unknown_column():
    class _A(ModelAdmin):
        model = _Post
        date_hierarchy = "does_not_exist"

    assert build_model_contract(_A()).date_hierarchy is None


def test_date_hierarchy_default_none():
    assert build_model_contract(_PostAdmin()).date_hierarchy is None


# ---------------------------------------------------------------------------
# form_layout (Roadmap 5.4)
# ---------------------------------------------------------------------------


def test_form_layout_defaults_to_sections():
    contract = build_model_contract(_PostAdmin())
    assert contract.form_layout == "sections"


def test_form_layout_tabs_emitted():
    class _TabsAdmin(ModelAdmin):
        model = _Post
        form_layout = "tabs"

    contract = build_model_contract(_TabsAdmin())
    assert contract.form_layout == "tabs"


def test_form_layout_unknown_falls_back_to_sections():
    class _WeirdAdmin(ModelAdmin):
        model = _Post
        form_layout = "carousel"

    contract = build_model_contract(_WeirdAdmin())
    assert contract.form_layout == "sections"


# ---------------------------------------------------------------------------
# validation hints
# ---------------------------------------------------------------------------


def test_validation_dict_populated_from_string_column_length():
    """Roadmap 2.3 lifted the A4 "slot exists but adapters don't emit"
    state — ``Column(String(200))`` now produces
    ``validation = {"max_length": 200}``. Adapters that don't supply
    hints still leave the dict empty (see test_validation_hints.py for
    that branch)."""
    metas = build_field_metadata(_PostAdmin())
    assert _meta_by_name(metas, "title").validation == {"max_length": 200}
