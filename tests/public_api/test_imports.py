"""Public API contract — Robustness-Doc §1.

Pins what counts as the officially supported import surface. Adding a
new public symbol means adding it here on purpose. Removing one is a
breaking change for downstream apps.

The framework has four public entry points:

* ``asterion`` — top-level convenience: ``create_admin``,
  ``CoreAdminConfig``, ``ModelAdmin``, ``AdminRegistry`` plus
  ``__version__``.
* ``asterion.admin`` — primitives for declaring admin behaviour:
  ``ModelAdmin`` (re-exported), ``AdminContext``, ``AdminPolicy``,
  ``FieldPermission``, ``Fieldset``, ``InlineAdmin``, plus the
  neutral provider DTOs (``AdminPrincipal``, ``AdminTenant``).
* ``asterion.providers`` — Protocols for replacing the builtin
  auth / user / permission / tenant stack, plus the four ``Builtin*``
  default implementations.
* ``asterion.fields`` — Field adapter SPI so extensions and apps
  can plug custom column types.

Everything outside these four packages is internal: stable behaviour
is not guaranteed and external code must not depend on it.
"""

from __future__ import annotations

import importlib

# ---------------------------------------------------------------------------
# Top-level surface
# ---------------------------------------------------------------------------

TOP_LEVEL_PUBLIC: set[str] = {
    "AdminRegistry",
    "CoreAdminConfig",
    "ModelAdmin",
    "__version__",
    "create_admin",
}


def test_top_level_public_imports_resolve():
    """Every name in the documented top-level surface must be importable
    from ``asterion``."""
    mod = importlib.import_module("asterion")
    for name in TOP_LEVEL_PUBLIC:
        assert hasattr(mod, name), f"asterion.{name} is missing"


def test_top_level_all_matches_pinned_set():
    """``__all__`` is the contract — adding/removing a public name
    must be deliberate. Pinning the set catches accidental exports."""
    mod = importlib.import_module("asterion")
    assert set(mod.__all__) == TOP_LEVEL_PUBLIC


# ---------------------------------------------------------------------------
# asterion.admin
# ---------------------------------------------------------------------------

ADMIN_PUBLIC: set[str] = {
    "AdminContext",
    "AdminPolicy",
    "AdminPrincipal",
    "AdminRegistry",
    "AdminTenant",
    "FieldPermission",
    "Fieldset",
    "InlineAdmin",
    "ModelAdmin",
    "ReadOnlyPolicy",
    "build_admin_context",
    "require_admin_context",
}


def test_admin_public_imports_resolve():
    mod = importlib.import_module("asterion.admin")
    for name in ADMIN_PUBLIC:
        assert hasattr(mod, name), f"asterion.admin.{name} is missing"


def test_admin_all_matches_pinned_set():
    mod = importlib.import_module("asterion.admin")
    assert set(mod.__all__) == ADMIN_PUBLIC


# ---------------------------------------------------------------------------
# asterion.providers
# ---------------------------------------------------------------------------

PROVIDERS_PUBLIC: set[str] = {
    "AdminPrincipal",
    "AdminTenant",
    "AuthIdentity",
    "AuthProvider",
    "AuthSession",
    "BuiltinJWTAuthProvider",
    "BuiltinPermissionProvider",
    "BuiltinSQLAlchemyUserProvider",
    "BuiltinTenantProvider",
    "CredentialAuthProvider",
    "LoginCredentials",
    "LoginError",
    "Page",
    "PermissionProvider",
    "TenantProvider",
    "UserListingProvider",
    "UserProvider",
    "UserQuery",
}


def test_providers_public_imports_resolve():
    mod = importlib.import_module("asterion.providers")
    for name in PROVIDERS_PUBLIC:
        assert hasattr(mod, name), f"asterion.providers.{name} is missing"


def test_providers_all_matches_pinned_set():
    mod = importlib.import_module("asterion.providers")
    assert set(mod.__all__) == PROVIDERS_PUBLIC


# ---------------------------------------------------------------------------
# asterion.fields
# ---------------------------------------------------------------------------

FIELDS_PUBLIC: set[str] = {
    "DEFAULT_FILE_ADAPTERS",
    "DEFAULT_RELATION_ADAPTERS",
    "DEFAULT_SCALAR_ADAPTERS",
    "BooleanAdapter",
    "DateTimeAdapter",
    "EnumAdapter",
    "FieldAdapter",
    "FieldContract",
    "FieldRegistry",
    "FileFieldAdapter",
    "FileFieldType",
    "FloatAdapter",
    "ForeignKeyAdapter",
    "IntegerAdapter",
    "JSONAdapter",
    "StringAdapter",
    "TextAdapter",
    "UUIDAdapter",
    "build_default_registry",
}


def test_fields_public_imports_resolve():
    mod = importlib.import_module("asterion.fields")
    for name in FIELDS_PUBLIC:
        assert hasattr(mod, name), f"asterion.fields.{name} is missing"


def test_fields_all_matches_pinned_set():
    mod = importlib.import_module("asterion.fields")
    assert set(mod.__all__) == FIELDS_PUBLIC


# ---------------------------------------------------------------------------
# Concrete import smoke tests — the shape downstream code will use
# ---------------------------------------------------------------------------


def test_quickstart_imports_resolve():
    """The minimal documented quickstart import shape."""
    from asterion import CoreAdminConfig, ModelAdmin, create_admin

    assert callable(create_admin)
    assert CoreAdminConfig is not None
    assert ModelAdmin is not None


def test_external_provider_imports_resolve():
    """What an app integrating an external auth system imports."""
    from asterion import create_admin
    from asterion.admin import AdminContext, AdminPrincipal, AdminTenant
    from asterion.providers import (
        AuthProvider,
        PermissionProvider,
        TenantProvider,
        UserProvider,
    )

    _ = (
        create_admin,
        AdminContext,
        AdminPrincipal,
        AdminTenant,
        AuthProvider,
        UserProvider,
        PermissionProvider,
        TenantProvider,
    )


def test_extension_developer_imports_resolve():
    """What an extension author / custom field-type author imports."""
    from asterion.admin import (
        AdminPolicy,
        FieldPermission,
        Fieldset,
        InlineAdmin,
    )
    from asterion.fields import (
        FieldAdapter,
        FieldContract,
        FieldRegistry,
        build_default_registry,
    )

    _ = (
        AdminPolicy,
        FieldPermission,
        Fieldset,
        InlineAdmin,
        FieldAdapter,
        FieldContract,
        FieldRegistry,
        build_default_registry,
    )
