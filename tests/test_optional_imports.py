"""Importing adminfoundry must not require optional integration packages.

Verified in a fresh subprocess so the parent process's already-imported modules
(it has imported httpx etc. via test fixtures) cannot leak in.
"""
import subprocess
import sys
import textwrap


_BLOCKER = textwrap.dedent("""
    import sys

    class _Block:
        def find_spec(self, name, path=None, target=None):
            top = name.split('.', 1)[0]
            if top in {'boto3', 'redis', 'httpx', 'openpyxl'}:
                raise ImportError(f"blocked optional dep: {top}")
            return None

    sys.meta_path.insert(0, _Block())
""")


def _run(script: str) -> None:
    subprocess.check_call([sys.executable, "-c", _BLOCKER + script])


def test_import_adminfoundry_without_optional_deps():
    _run("import adminfoundry")


def test_import_cache_storage_webhooks_without_optional_deps():
    _run(
        "import adminfoundry; "
        "from adminfoundry import cache, storage, webhooks, signals; "
        "from adminfoundry.extensions.storage_s3 import S3Storage"
    )


def test_import_models_without_optional_deps():
    _run(
        "from adminfoundry.models import User, Role, user_roles; "
        "from sqlalchemy.orm import configure_mappers; "
        "configure_mappers()"
    )
