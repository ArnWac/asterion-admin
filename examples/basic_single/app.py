"""
Single-tenant blog example.

Run:
    uvicorn examples.basic_single.app:app --reload

Admin UI: http://127.0.0.1:8000/admin

Optional Google OAuth sign-in: set GOOGLE_OAUTH_CLIENT_ID and
GOOGLE_OAUTH_CLIENT_SECRET before launching and the login page grows
a "Sign in with Google" button. See examples/basic_single/README.md
for the Google-side setup.
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI

from asterion import CoreAdminConfig, create_admin
from asterion.extensions import AdminExtension
from asterion.extensions.auth_oauth import (
    GoogleOIDCProvider,
    OAuthExtension,
)
from asterion.extensions.import_export import ImportExportExtension
from examples.basic_single.admin_config import register
from examples.basic_single.seed import print_banner, seed

config = CoreAdminConfig(
    database_url=os.environ.get(
        "DATABASE_URL",
        "sqlite+aiosqlite:///./basic_single.db",
    ),
    secret_key=os.environ.get("SECRET_KEY", "dev-secret"),
    app_title="asterion — basic_single",
    enable_multi_tenant=False,
    enable_builtin_admins=False,
)


def _build_extensions() -> list[AdminExtension]:
    """Always ship import/export; ship OAuth only when configured.

    The example stays runnable without any OAuth env vars — the login
    page falls back to the password form. Set both env vars to turn
    on the "Sign in with Google" button.
    """
    extensions: list[AdminExtension] = [ImportExportExtension()]

    google_client_id = os.environ.get("GOOGLE_OAUTH_CLIENT_ID")
    google_client_secret = os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET")
    if google_client_id and google_client_secret:
        extensions.append(
            OAuthExtension(
                providers=[
                    GoogleOIDCProvider(
                        client_id=google_client_id,
                        client_secret=google_client_secret,
                    ),
                ],
                # Demo-friendly: new Google users land with auto-created
                # accounts. For production set this False and pre-provision
                # users via the admin UI. The Builtin user provider still
                # refuses unverified emails + silent account linking — see
                # docs/auth-oauth.md § Auto-create for the security defaults.
                auto_create_users=True,
            )
        )

    return extensions


@asynccontextmanager
async def lifespan(app: FastAPI):
    await seed(app.state.asterion.db)
    print_banner()
    yield


app = create_admin(
    config=config,
    register=register,
    extensions=_build_extensions(),
    lifespan=lifespan,
)
