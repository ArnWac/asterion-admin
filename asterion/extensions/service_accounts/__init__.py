"""Service-accounts extension (ADR-0005).

Token-only machine accounts (a stationary terminal, a service-to-service caller)
as an **optional extension**, mirroring ``auth_oauth`` — an alternative
authentication method that plugs in via ``extensions=[…]`` and owns its own DB
table instead of a column on the core ``User``.

What's HERE:

* :class:`~asterion.extensions.service_accounts.models.ServiceAccount` — the
  public-schema marker table (``register_models``).
* :func:`create_service_account` / :func:`delete_service_account` — the
  provisioning helpers (``service.py``).
* :class:`ServiceAccountsExtension` — the ``AdminExtension`` that attaches the
  model.

What core keeps (NOT here): the generic ``User.password_login_disabled`` auth
mechanism (no password login, no reset token). Core owns *that a login can be
disabled*; this extension owns *the service-account concept* that uses it.

Migration story: the framework ships **no** migration for ``service_accounts``.
Host apps wiring this extension run ``alembic revision --autogenerate`` against
their own env.py (see ``models.py`` and ``docs/extensions.md``).

Usage::

    from asterion import create_admin
    from asterion.extensions.service_accounts import ServiceAccountsExtension

    app = create_admin(config=..., extensions=[ServiceAccountsExtension()])

Provisioning (CLI): ``asterion service-account create --tenant … --label … \
--permission admin.time_entries.create`` mints a token once. The CLI command
lives in the framework CLI but delegates to this extension's helpers.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from asterion.extensions.base import AdminExtension
from asterion.extensions.service_accounts.models import ServiceAccount
from asterion.extensions.service_accounts.service import (
    create_service_account,
    delete_service_account,
    service_role_name,
)


class ServiceAccountsExtension(AdminExtension):
    """Wires the ``service_accounts`` table into the app.

    Minimal by design — the provisioning helpers are called from the CLI /
    application code, so the extension itself only needs to attach the model so
    ``metadata.create_all`` and migration autogenerate see the table.
    """

    name = "service_accounts"

    def register_models(self) -> Iterable[type[Any]]:
        return (ServiceAccount,)


__all__ = [
    "ServiceAccount",
    "ServiceAccountsExtension",
    "create_service_account",
    "delete_service_account",
    "service_role_name",
]
