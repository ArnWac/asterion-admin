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


def tenant_alembic_config(explicit_ini: str | None = None):
    """Resolve the Alembic ``Config`` for the TENANT tree.

    The tenant tree is owned by the **downstream app** (its domain tables live
    alongside asterion's ``tenant_rbac``), so we do NOT hard-wire asterion's
    bundled tenant migrations. Resolution order:

    1. an explicit ``--config`` / ``-c`` path, or ``ASTERION_ALEMBIC_TENANT_INI``;
    2. a project-local ``alembic_tenant.ini`` in the current directory
       (the app owns the tenant migrations tree);
    3. fall back to asterion's bundled tenant migrations (pure-asterion
       deployments and asterion's own tests).
    """
    from alembic.config import Config

    candidate = explicit_ini or os.environ.get("ASTERION_ALEMBIC_TENANT_INI")
    if candidate:
        return Config(candidate)
    local = Path.cwd() / "alembic_tenant.ini"
    if local.exists():
        return Config(str(local))
    cfg = Config()
    cfg.set_main_option("script_location", str(bundled_migrations_path("tenant")))
    return cfg


def set_x_schema(cfg, schema: str) -> None:
    """Pass ``-x schema=<schema>`` to an in-process alembic ``env.py``."""
    from argparse import Namespace

    existing = getattr(cfg, "cmd_opts", None)
    x = list(getattr(existing, "x", None) or [])
    x.append(f"schema={schema}")
    cfg.cmd_opts = Namespace(x=x)
