"""
Phase 4 — Schema builder tests (must pass before router is wired).
Tests cover multiple distinct model configurations.
"""
import uuid
import pytest
from adminfoundry.admin.model_admin import ModelAdmin, GLOBALLY_PROTECTED, AUTO_FIELDS
from adminfoundry.admin.schema_builder import SchemaBuilder
from adminfoundry.models.user import User
from adminfoundry.models.role import Role
from adminfoundry.models.tenant import Tenant


# ---------------------------------------------------------------------------
# Fixtures: model admin configurations
# ---------------------------------------------------------------------------

class UserAdmin(ModelAdmin):
    model = User
    list_display = ["email", "full_name", "is_active"]
    search_fields = ["email", "full_name"]
    filter_fields = ["is_active"]
    ordering = ["email"]
    readonly_fields = ["id", "created_at", "updated_at"]


class RoleAdmin(ModelAdmin):
    model = Role
    list_display = ["name"]
    readonly_fields = ["id", "created_at", "updated_at"]


class TenantAdmin(ModelAdmin):
    model = Tenant
    list_display = ["name", "slug", "is_active"]
    readonly_fields = ["id", "created_at", "updated_at"]
    protected_fields = []


@pytest.fixture
def builder():
    # Fresh builder per test (no shared cache)
    return SchemaBuilder()


# ---------------------------------------------------------------------------
# Protected fields are absent from all schemas
# ---------------------------------------------------------------------------

def test_list_schema_excludes_globally_protected(builder):
    schema = builder.build_list_schema(UserAdmin())
    field_names = set(schema.model_fields.keys())
    assert "hashed_password" not in field_names
    assert "password" not in field_names


def test_detail_schema_excludes_globally_protected(builder):
    schema = builder.build_detail_schema(UserAdmin())
    field_names = set(schema.model_fields.keys())
    assert "hashed_password" not in field_names


def test_create_schema_excludes_globally_protected(builder):
    schema = builder.build_create_schema(UserAdmin())
    field_names = set(schema.model_fields.keys())
    assert "hashed_password" not in field_names


def test_update_schema_excludes_globally_protected(builder):
    schema = builder.build_update_schema(UserAdmin())
    field_names = set(schema.model_fields.keys())
    assert "hashed_password" not in field_names


def test_per_admin_protected_fields_excluded(builder):
    class SecretAdmin(ModelAdmin):
        model = User
        protected_fields = ["full_name"]

    schema = builder.build_detail_schema(SecretAdmin())
    assert "full_name" not in schema.model_fields
    assert "hashed_password" not in schema.model_fields  # globally protected still excluded


# ---------------------------------------------------------------------------
# Auto and readonly fields excluded from create/update
# ---------------------------------------------------------------------------

def test_create_schema_excludes_auto_fields(builder):
    schema = builder.build_create_schema(UserAdmin())
    field_names = set(schema.model_fields.keys())
    for auto in AUTO_FIELDS:
        assert auto not in field_names, f"Auto field '{auto}' should not be in create schema"


def test_create_schema_excludes_readonly_fields(builder):
    schema = builder.build_create_schema(UserAdmin())
    field_names = set(schema.model_fields.keys())
    for ro in UserAdmin.readonly_fields:
        assert ro not in field_names, f"Readonly field '{ro}' should not be in create schema"


def test_update_schema_excludes_readonly_fields(builder):
    schema = builder.build_update_schema(UserAdmin())
    field_names = set(schema.model_fields.keys())
    for ro in UserAdmin.readonly_fields:
        assert ro not in field_names


def test_update_schema_all_fields_optional(builder):
    schema = builder.build_update_schema(UserAdmin())
    for name, field_info in schema.model_fields.items():
        assert not field_info.is_required(), f"Field '{name}' should be optional in update schema"


# ---------------------------------------------------------------------------
# List schema respects list_display
# ---------------------------------------------------------------------------

def test_list_schema_only_includes_list_display_fields(builder):
    schema = builder.build_list_schema(UserAdmin())
    field_names = set(schema.model_fields.keys())
    # Should include list_display fields + id
    for f in ["email", "full_name", "is_active"]:
        assert f in field_names
    # Should NOT include fields not in list_display (unless it's id)
    assert "is_superadmin" not in field_names


def test_list_schema_always_includes_id(builder):
    schema = builder.build_list_schema(UserAdmin())
    assert "id" in schema.model_fields


# ---------------------------------------------------------------------------
# Detail schema includes all non-protected fields
# ---------------------------------------------------------------------------

def test_detail_schema_includes_all_visible_fields(builder):
    schema = builder.build_detail_schema(UserAdmin())
    field_names = set(schema.model_fields.keys())
    assert "email" in field_names
    assert "full_name" in field_names
    assert "is_active" in field_names
    assert "is_superadmin" in field_names
    assert "created_at" in field_names


# ---------------------------------------------------------------------------
# Extra/forbidden fields raise ValidationError on create/update
# ---------------------------------------------------------------------------

def test_create_schema_rejects_auto_fields(builder):
    from pydantic import ValidationError
    schema = builder.build_create_schema(UserAdmin())
    with pytest.raises(ValidationError):
        schema.model_validate({"id": str(uuid.uuid4()), "email": "x@x.com"})


def test_update_schema_rejects_readonly_fields(builder):
    from pydantic import ValidationError
    schema = builder.build_update_schema(UserAdmin())
    with pytest.raises(ValidationError):
        schema.model_validate({"id": str(uuid.uuid4()), "email": "new@x.com"})


def test_create_schema_rejects_protected_fields(builder):
    from pydantic import ValidationError
    schema = builder.build_create_schema(UserAdmin())
    with pytest.raises(ValidationError):
        schema.model_validate({"hashed_password": "secret", "email": "x@x.com"})


# ---------------------------------------------------------------------------
# Multiple distinct model configurations
# ---------------------------------------------------------------------------

def test_role_admin_schema(builder):
    detail = builder.build_detail_schema(RoleAdmin())
    assert "name" in detail.model_fields
    assert "id" in detail.model_fields

    create = builder.build_create_schema(RoleAdmin())
    assert "name" in create.model_fields
    assert "id" not in create.model_fields


def test_tenant_admin_list_schema(builder):
    schema = builder.build_list_schema(TenantAdmin())
    field_names = set(schema.model_fields.keys())
    assert "name" in field_names
    assert "slug" in field_names
    assert "is_active" in field_names


def test_schema_builder_caches_schemas(builder):
    admin = UserAdmin()
    s1 = builder.build_detail_schema(admin)
    s2 = builder.build_detail_schema(admin)
    assert s1 is s2  # same object returned from cache


def test_from_attributes_on_list_detail_schemas(builder):
    """List and detail schemas must support ORM object deserialization."""
    admin = UserAdmin()
    list_s = builder.build_list_schema(admin)
    detail_s = builder.build_detail_schema(admin)
    assert list_s.model_config.get("from_attributes") is True
    assert detail_s.model_config.get("from_attributes") is True
