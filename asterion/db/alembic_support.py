"""Package-relative Alembic resolution helpers.

Shared by the CLI (``db upgrade-public`` / ``upgrade-tenant(s)``) and tenant
bootstrap so a pip-installed asterion (no repo checkout) can run its **bundled**
migrations from any cwd, while the tenant tree stays owned by the downstream
app.
"""

from __future__ import annotations

import os
from pathlib import Path


def bundled_migrations_path(env: str) -> Path:
    """Filesystem path to asterion's bundled Alembic migrations for ``env``
    (``"shared"`` or ``"tenant"``), resolved package-relatively via
    ``importlib.resources`` — works from any cwd and from a pip-installed wheel
    (the migrations ship as ``package-data`` under ``asterion/_migrations/``)."""
    from importlib.resources import files

    return Path(str(files("asterion").joinpath("_migrations", env)))


def shared_alembic_config():
    """Alembic ``Config`` for asterion's bundled SHARED (public) migrations.

    asterion owns the shared tree (users, tenants, audit, tokens, 2FA,
    password_reset), so this always points at the bundled migrations regardless
    of cwd. ``env.py`` reads the DB URL from ``ASTERION_DATABASE_URL`` /
    ``DATABASE_URL``.
    """
    from alembic.config import Config

    cfg = Config()
    cfg.set_main_option("script_location", str(bundled_migrations_path("shared")))
    return cfg


#: Alembic version table for asterion's framework tenant tree. Distinct from
#: the default ``alembic_version`` so the framework base history and the
#: downstream app's own tenant history can coexist in the same tenant schema
#: (Theme H — asterion owns its tenant tables, split like public/shared).
FRAMEWORK_TENANT_VERSION_TABLE = "alembic_version_asterion_tenant"

#: Tenant-schema tables owned by asterion's framework tenant tree. A downstream
#: app's tenant ``env.py`` feeds :func:`exclude_framework_tenant_tables` into
#: Alembic's ``include_object`` so its autogenerate never re-creates / drops
#: these — the framework base tree owns them now.
FRAMEWORK_TENANT_TABLES: frozenset[str] = frozenset(
    {
        "tenant_roles",
        "tenant_role_permissions",
        "tenant_membership_roles",
        "tenant_audit_logs",
    }
)


def framework_tenant_alembic_config():
    """Alembic ``Config`` for asterion's bundled FRAMEWORK tenant base tree.

    asterion owns its tenant tables (RBAC + audit), so — like the shared/public
    tree — this always points at the bundled migrations regardless of cwd, and
    is tracked in its own :data:`FRAMEWORK_TENANT_VERSION_TABLE` so it never
    collides with the downstream app's tenant history. Applied FIRST, before
    the app tree, by :func:`upgrade_tenant_schema`.
    """
    from alembic.config import Config

    cfg = Config()
    cfg.set_main_option("script_location", str(bundled_migrations_path("tenant")))
    cfg.set_main_option("version_table", FRAMEWORK_TENANT_VERSION_TABLE)
    return cfg


def app_tenant_alembic_config(explicit_ini: str | None = None):
    """Resolve the downstream APP's tenant Alembic ``Config``, or ``None``.

    The app owns only its DOMAIN tenant tables now (the framework base tree owns
    asterion's). Resolution order:

    1. an explicit ``--config`` / ``-c`` path, or ``ASTERION_ALEMBIC_TENANT_INI``;
    2. a project-local ``alembic_tenant.ini`` in the current directory.

    Returns ``None`` when the app owns no tenant tree (pure-asterion
    deployments): the framework base is then the whole tenant schema. This is
    the key Theme-H change — there is no longer a bundled "fallback" here,
    because the bundled tree is always applied as the framework base via
    :func:`framework_tenant_alembic_config`.
    """
    from alembic.config import Config

    candidate = explicit_ini or os.environ.get("ASTERION_ALEMBIC_TENANT_INI")
    if candidate:
        return Config(candidate)
    local = Path.cwd() / "alembic_tenant.ini"
    if local.exists():
        return Config(str(local))
    return None


def upgrade_tenant_schema(
    schema_name: str,
    *,
    database_url: str | None = None,
    explicit_ini: str | None = None,
) -> None:
    """Apply asterion's framework tenant base, THEN the app's tenant tree.

    Ordered, not either/or (Theme H): the bundled framework tree is *always*
    applied first — tracked in :data:`FRAMEWORK_TENANT_VERSION_TABLE` — so every
    tenant schema gets the framework tables (RBAC + audit) regardless of what the
    app's tree contains. The app tree (explicit ini / env var / project-local
    ``alembic_tenant.ini``) is applied second for the app's own domain tables,
    tracked in the default ``alembic_version``. With no app tree the framework
    base is the whole schema. Both steps target ``schema_name`` via
    ``-x schema=`` and are idempotent (the framework migrations skip tables that
    already exist, so an existing app whose old tree already created the
    framework tables simply gets its framework version table stamped).

    ``database_url`` overrides the env-var URL the bundled ``env.py`` reads;
    pass it from contexts (e.g. tenant bootstrap) that don't set the env var.
    """
    from alembic import command

    framework = framework_tenant_alembic_config()
    if database_url:
        framework.set_main_option("sqlalchemy.url", database_url)
    set_x_schema(framework, schema_name)
    command.upgrade(framework, "head")

    app = app_tenant_alembic_config(explicit_ini)
    if app is not None:
        if database_url:
            app.set_main_option("sqlalchemy.url", database_url)
        set_x_schema(app, schema_name)
        command.upgrade(app, "head")


def exclude_framework_tenant_tables(
    object_, name, type_, reflected, compare_to
) -> bool:
    """Alembic ``include_object`` filter for a downstream app's tenant ``env.py``.

    Returns ``False`` for asterion-owned tenant tables (:data:`FRAMEWORK_TENANT_TABLES`)
    so the app's autogenerate never re-creates or drops them — the framework
    base tree owns them. Wire it up in the app's tenant ``env.py``::

        from asterion.db.alembic_support import exclude_framework_tenant_tables
        context.configure(..., include_object=exclude_framework_tenant_tables)
    """
    if type_ == "table" and name in FRAMEWORK_TENANT_TABLES:
        return False
    return True


def set_x_schema(cfg, schema: str) -> None:
    """Pass ``-x schema=<schema>`` to an in-process alembic ``env.py``."""
    from argparse import Namespace

    existing = getattr(cfg, "cmd_opts", None)
    x = list(getattr(existing, "x", None) or [])
    x.append(f"schema={schema}")
    cfg.cmd_opts = Namespace(x=x)
