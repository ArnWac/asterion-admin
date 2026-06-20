"""asterion's Alembic migrations ship inside the package and resolve
package-relatively (Change 1).

These guard the embedding story: a pip-installed asterion (no repo checkout)
must be able to run ``asterion db upgrade-public`` from any cwd, and the tenant
tree must defer to a project-local ``alembic_tenant.ini`` when the downstream
app owns it.
"""

from __future__ import annotations

from importlib.resources import files

from sqlalchemy import create_engine, inspect
from typer.testing import CliRunner

from asterion.cli import app
from asterion.db.alembic_support import bundled_migrations_path, tenant_alembic_config

runner = CliRunner()


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


# --- tenant config resolution order ---


def test_tenant_config_prefers_explicit_ini(tmp_path, monkeypatch):
    explicit = tmp_path / "custom_tenant.ini"
    explicit.write_text("[alembic]\nscript_location = whatever\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)  # even with a local one present, explicit wins
    (tmp_path / "alembic_tenant.ini").write_text("[alembic]\n", encoding="utf-8")

    cfg = tenant_alembic_config(str(explicit))
    assert cfg.config_file_name == str(explicit)


def test_tenant_config_prefers_local_ini(tmp_path, monkeypatch):
    local = tmp_path / "alembic_tenant.ini"
    local.write_text("[alembic]\nscript_location = migrations/tenant\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    cfg = tenant_alembic_config(None)
    assert cfg.config_file_name == str(local)


def test_tenant_config_falls_back_to_bundled(tmp_path, monkeypatch):
    monkeypatch.delenv("ASTERION_ALEMBIC_TENANT_INI", raising=False)
    monkeypatch.chdir(tmp_path)  # no local alembic_tenant.ini here

    cfg = tenant_alembic_config(None)
    assert cfg.config_file_name is None  # built programmatically, no ini
    assert cfg.get_main_option("script_location") == str(bundled_migrations_path("tenant"))


def test_tenant_config_explicit_env_var(tmp_path, monkeypatch):
    explicit = tmp_path / "env_tenant.ini"
    explicit.write_text("[alembic]\n", encoding="utf-8")
    monkeypatch.setenv("ASTERION_ALEMBIC_TENANT_INI", str(explicit))
    monkeypatch.chdir(tmp_path)

    cfg = tenant_alembic_config(None)
    assert cfg.config_file_name == str(explicit)
