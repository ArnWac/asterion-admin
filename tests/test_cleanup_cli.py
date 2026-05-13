"""Cleanup is callable from the CLI, and the documented config flags exist."""
import os
import subprocess
import sys
import tempfile
import textwrap


def test_settings_expose_cleanup_flags():
    from adminfoundry.settings import settings
    assert settings.ENABLE_CLEANUP_TASK is True
    assert settings.CLEANUP_INTERVAL_SECONDS == 3600


def test_cleanup_once_exits_zero():
    # Use a tempfile-backed SQLite DB so the subprocess has a real schema to
    # delete from. (:memory: doesn't persist across the two-step setup/run.)
    with tempfile.TemporaryDirectory() as tmp:
        db_path = os.path.join(tmp, "cleanup.db").replace("\\", "/")
        env = {**os.environ, "DATABASE_URL": f"sqlite+aiosqlite:///{db_path}"}

        setup = textwrap.dedent("""
            import asyncio
            from adminfoundry.database import engine
            from adminfoundry.models.base import Base
            import adminfoundry.models  # noqa: F401 — register all core tables

            async def main():
                async with engine.begin() as conn:
                    await conn.run_sync(Base.metadata.create_all)
            asyncio.run(main())
        """)
        subprocess.check_call([sys.executable, "-c", setup], env=env)

        result = subprocess.run(
            [sys.executable, "-m", "adminfoundry.cli", "cleanup", "--once"],
            capture_output=True, text=True, env=env,
        )
        assert result.returncode == 0, result.stderr


def test_cleanup_without_flag_exits_nonzero():
    """Bare `adminfoundry cleanup` should not silently exit 0 — force the --once intent."""
    result = subprocess.run(
        [sys.executable, "-m", "adminfoundry.cli", "cleanup"],
        capture_output=True, text=True,
    )
    assert result.returncode != 0
