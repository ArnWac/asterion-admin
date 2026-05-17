"""adminfoundry.models exports every core table and registers it in Base.metadata."""
import adminfoundry.models as models
from adminfoundry.models.base import Base


def test_all_core_tables_registered_in_metadata():
    expected = {
        "users", "roles", "tenants", "audit_logs",
        "revoked_tokens", "password_reset_tokens",
        "user_roles", "role_permissions",
        "impersonation_logs",
    }
    missing = expected - set(Base.metadata.tables)
    assert not missing, f"missing tables: {missing}"


def test_models_all_matches_exports():
    expected = {
        "AuditLog", "ImpersonationLog",
        "PasswordResetToken", "RevokedToken",
        "Role", "RolePermission", "Tenant", "TenantMembership", "User",
        "membership_roles", "user_roles",
    }
    assert set(models.__all__) == expected
    for name in expected:
        assert hasattr(models, name), f"adminfoundry.models is missing {name}"


def test_user_roles_relationship_uses_table_object():
    """user_roles must be the actual Table, not a string — otherwise the bug returns."""
    from adminfoundry.models import User
    rel = User.__mapper__.relationships["roles"]
    assert rel.secondary is not None
    assert rel.secondary.name == "user_roles"
