"""Regression: importing User alone must not break SQLAlchemy mapper configuration.

The bug this guards against: User.roles used to reference user_roles by string,
while the table was defined in role.py. Importing User without also importing
Role left "user_roles" unresolved, so configure_mappers() failed with
InvalidRequestError.
"""
import subprocess
import sys


def test_user_only_import_does_not_break_mapper():
    subprocess.check_call([sys.executable, "-c", (
        "from adminfoundry.models import User; "
        "from sqlalchemy.orm import configure_mappers; "
        "configure_mappers()"
    )])


def test_user_direct_module_import_does_not_break_mapper():
    """Even importing only adminfoundry.models.user must work — associations.py
    is pulled in transitively by user.py itself."""
    subprocess.check_call([sys.executable, "-c", (
        "from adminfoundry.models.user import User; "
        "from sqlalchemy.orm import configure_mappers; "
        "configure_mappers()"
    )])
