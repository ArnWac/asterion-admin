"""asterion's Alembic migrations ship inside the package and resolve
package-relatively (Change 1).

These guard the embedding story: a pip-installed asterion (no repo checkout)
must be able to run ``asterion db upgrade-public`` from any cwd, and the tenant
tree must defer to a project-local ``alembic_tenant.ini`` when the downstream
app owns it.
"""

from __future__ import annotations

from importlib.resources import files

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect
from typer.testing import CliRunner

from asterion.cli import app
from asterion.db.alembic_support import (
    FRAMEWORK_TENANT_VERSION_TABLE,
    app_tenant_alembic_config,
    bundled_migrations_path,
    framework_tenant_alembic_config,
)

runner = CliRunner()

# Alembic's default version table is ``alembic_version.version_num VARCHAR(32)``
# (hard-coded in alembic.runtime.migration). Postgres enforces the width, so a
# revision id longer than this overflows on the ``UPDATE alembic_version`` stamp
# and rolls the whole upgrade back. SQLite ignores VARCHAR lengths, which is why
# this only ever bit on real Postgres.
ALEMBIC_VERSION_NUM_MAX = 32


# --- revision-id length guard ---


@pytest.mark.parametrize("env", ["shared", "tenant"])
def test_revision_ids_fit_alembic_version_column(env):
    cfg = Config()
    cfg.set_main_option("script_location", str(bundled_migrations_path(env)))
    script = ScriptDirectory.from_config(cfg)
    too_long = {
        rev.revision: len(rev.revision)
        for rev in script.walk_revisions()
        if len(rev.revision) > ALEMBIC_VERSION_NUM_MAX
    }
    assert not too_long, (
        f"{env} revision ids exceed alembic_version VARCHAR({ALEMBIC_VERSION_NUM_MAX}) "
        f"and will overflow the stamp on Postgres: {too_long}"
    )


# --- packaging ---


def test_shared_migrations_are_packaged():
    versions = bundled_migrations_path("shared") / "versions"
    names = {p.name for p in versions.glob("*.py")}
    assert "0001_initial.py" in names, f"shared versions not packaged: {names}"


def test_tenant_migrations_are_packaged():
    versions = bundled_migrations_path("tenant") / "versions"
    names = {p.name for p in versions.glob("*.py")}
    assert "0001_initial.py" in names, f"tenant versions not packaged: {names}"


def test_bundled_path_resolves_via_importlib_resources():
    # The same resolution a pip-installed asterion uses (no repo paths).
    pkg = files("asterion")
    assert (pkg / "_migrations" / "shared" / "env.py").is_file()
    assert (pkg / "_migrations" / "tenant" / "env.py").is_file()


# --- cwd-independent shared upgrade ---


def test_db_upgrade_public_runs_from_foreign_cwd(tmp_path, monkeypatch):
    """`asterion db upgrade-public` applies asterion's bundled shared migrations
    even when invoked from a directory that has no alembic config at all."""
    db_path = tmp_path / "public.db"
    monkeypatch.setenv("ASTERION_DATABASE_URL", f"sqlite+aiosqlite:///{db_path}")
    # A foreign cwd with no alembic_shared.ini / migrations dir.
    foreign = tmp_path / "elsewhere"
    foreign.mkdir()
    monkeypatch.chdir(foreign)

    result = runner.invoke(app, ["db", "upgrade-public"])
    assert result.exit_code == 0, result.output

    engine = create_engine(f"sqlite:///{db_path}")
    try:
        tables = set(inspect(engine).get_table_names())
    finally:
        engine.dispose()
    # The shared tree a downstream app depends on.
    assert {"users", "tenants", "audit_logs", "revoked_tokens"}.issubset(tables), tables


# --- tenant config resolution order (Theme H: app tree vs framework base) ---


def test_app_tenant_config_prefers_explicit_ini(tmp_path, monkeypatch):
    explicit = tmp_path / "custom_tenant.ini"
    explicit.write_text("[alembic]\nscript_location = whatever\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)  # even with a local one present, explicit wins
    (tmp_path / "alembic_tenant.ini").write_text("[alembic]\n", encoding="utf-8")

    cfg = app_tenant_alembic_config(str(explicit))
    assert cfg is not None
    assert cfg.config_file_name == str(explicit)


def test_app_tenant_config_prefers_local_ini(tmp_path, monkeypatch):
    local = tmp_path / "alembic_tenant.ini"
    local.write_text("[alembic]\nscript_location = migrations/tenant\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    cfg = app_tenant_alembic_config(None)
    assert cfg is not None
    assert cfg.config_file_name == str(local)


def test_app_tenant_config_is_none_without_ini(tmp_path, monkeypatch):
    """Theme H: there is no bundled fallback for the APP tree any more — the
    bundled tree is always applied as the framework base instead. With no app
    ini the app owns no tenant tree (the framework base is the whole schema)."""
    monkeypatch.delenv("ASTERION_ALEMBIC_TENANT_INI", raising=False)
    monkeypatch.chdir(tmp_path)  # no local alembic_tenant.ini here

    assert app_tenant_alembic_config(None) is None


def test_framework_tenant_config_points_at_bundled_with_own_version_table():
    """The framework base always resolves to asterion's bundled tenant tree and
    is tracked in its own version table, regardless of cwd / app ini."""
    cfg = framework_tenant_alembic_config()
    assert cfg.config_file_name is None  # built programmatically, no ini
    assert cfg.get_main_option("script_location") == str(bundled_migrations_path("tenant"))
    assert cfg.get_main_option("version_table") == FRAMEWORK_TENANT_VERSION_TABLE


def test_app_tenant_config_explicit_env_var(tmp_path, monkeypatch):
    explicit = tmp_path / "env_tenant.ini"
    explicit.write_text("[alembic]\n", encoding="utf-8")
    monkeypatch.setenv("ASTERION_ALEMBIC_TENANT_INI", str(explicit))
    monkeypatch.chdir(tmp_path)

    cfg = app_tenant_alembic_config(None)
    assert cfg is not None
    assert cfg.config_file_name == str(explicit)


# --- ordered upgrade: framework base first, then app tree (Theme H) ---


def _capture_upgrades(monkeypatch):
    import alembic.command as alembic_command

    calls: list[dict] = []

    def fake_upgrade(cfg, revision):
        calls.append(
            {
                "revision": revision,
                "script_location": cfg.get_main_option("script_location"),
                "version_table": cfg.get_main_option("version_table"),
                "config_file_name": cfg.config_file_name,
            }
        )

    monkeypatch.setattr(alembic_command, "upgrade", fake_upgrade)
    return calls


def test_upgrade_tenant_schema_runs_framework_base_only_without_app_tree(tmp_path, monkeypatch):
    from asterion.db.alembic_support import upgrade_tenant_schema

    monkeypatch.delenv("ASTERION_ALEMBIC_TENANT_INI", raising=False)
    monkeypatch.chdir(tmp_path)  # no app tenant tree
    calls = _capture_upgrades(monkeypatch)

    upgrade_tenant_schema("tenant_acme", database_url="postgresql+asyncpg://u:p@h/db")

    assert len(calls) == 1
    assert calls[0]["script_location"] == str(bundled_migrations_path("tenant"))
    assert calls[0]["version_table"] == FRAMEWORK_TENANT_VERSION_TABLE
    assert calls[0]["revision"] == "head"


def test_upgrade_tenant_schema_runs_framework_then_app(tmp_path, monkeypatch):
    from asterion.db.alembic_support import upgrade_tenant_schema

    monkeypatch.delenv("ASTERION_ALEMBIC_TENANT_INI", raising=False)
    app_ini = tmp_path / "alembic_tenant.ini"
    app_ini.write_text("[alembic]\nscript_location = migrations/tenant\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    calls = _capture_upgrades(monkeypatch)

    upgrade_tenant_schema("tenant_acme", database_url="postgresql+asyncpg://u:p@h/db")

    assert len(calls) == 2
    # Framework base first, in its own version table...
    assert calls[0]["script_location"] == str(bundled_migrations_path("tenant"))
    assert calls[0]["version_table"] == FRAMEWORK_TENANT_VERSION_TABLE
    # ...then the app tree (default alembic_version), resolved from the local ini.
    assert calls[1]["config_file_name"] == str(app_ini)
    assert calls[1]["version_table"] is None
