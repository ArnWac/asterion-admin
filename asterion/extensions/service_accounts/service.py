"""Provision token-only service / machine accounts (ADR-0005).

A device or service-to-service caller (e.g. a stationary time-clock terminal,
"terminal = user") needs an account that authenticates **only** via a minted
access token — never a password. :func:`create_service_account` is the one-call
helper; it composes core primitives (``User`` + ``TenantMembership`` +
``TenantRole`` + ``TenantRolePermission`` + ``TenantMembershipRole``) and records
a :class:`ServiceAccount` row so the account is discoverable and the teardown
guard has a source of truth.

It provisions an **active, passwordless** user with
``password_login_disabled=True`` (the core auth mechanism — no password login, no
reset token), binds it to a tenant, grants it a dedicated ``service:<label>``
role carrying the requested permission keys, and does **not** mint tokens (that
stays the caller's job — separation of concerns)::

    from asterion.extensions.service_accounts import create_service_account
    from asterion.auth.tokens import create_access_token

    user = await create_service_account(
        session,                       # MUST be tenant-scoped (see below)
        tenant_id=tenant.id,
        label="lobby-terminal",
        permission_keys=["admin.time_entries.create"],
    )
    token = create_access_token(user.id, secret_key=..., algorithm=...,
                                expires_minutes=..., token_version=user.token_version)

Session scoping
---------------

The RBAC tables (``TenantRole`` / ``TenantRolePermission`` /
``TenantMembershipRole``) are **tenant-local**, so ``session`` must be scoped to
the tenant schema (``SET LOCAL search_path``) — exactly like ``get_async_session``
and the CRUD path. ``User`` / ``TenantMembership`` / ``ServiceAccount`` are global
(``public``), so the same session covers both.

Revocation comes for free
-------------------------

To cut a service account's existing tokens, bump ``user.token_version`` or set
``user.is_active = False`` — the standard per-user revocation invariant applies.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from asterion.auth.provisioning import create_passwordless_user, ensure_membership
from asterion.extensions.service_accounts.models import ServiceAccount
from asterion.models.tenant_membership import TenantMembership
from asterion.models.tenant_rbac import (
    TenantMembershipRole,
    TenantRole,
    TenantRolePermission,
)
from asterion.models.user import User
from asterion.security.validation import validate_permission_key


def _slug(label: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-")
    return slug or "svc"


def service_role_name(label: str) -> str:
    """The dedicated tenant-role name for a service account: ``service:<label>``."""
    return f"service:{label}"


async def create_service_account(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    label: str,
    permission_keys: Iterable[str],
    email: str | None = None,
) -> User:
    """Provision an active, passwordless service account bound to ``tenant_id``.

    Steps:
      1. Create an **active, passwordless** ``User`` with
         ``password_login_disabled=True`` (``POST /auth/login`` rejects it — no
         valid password; and it never receives a reset token). A synthetic,
         unique email is derived from ``label`` + a uuid when ``email`` is None.
      2. Create the ``TenantMembership`` (idempotent on ``(user, tenant)``).
      3. Create a dedicated tenant role ``service:<label>``, grant it
         ``permission_keys``, and assign it to the membership.
      4. Record a :class:`ServiceAccount` row (the extension's marker).

    Returns the ``User`` so the caller can mint tokens with
    :func:`asterion.auth.tokens.create_access_token`.

    ``session`` must be tenant-scoped (see module docstring). Raises
    :class:`ValueError` for an already-used email, a duplicate
    ``service:<label>`` role, or a malformed permission key.
    """
    keys = list(dict.fromkeys(validate_permission_key(k) for k in permission_keys))

    if email is None:
        email = f"service+{_slug(label)}-{uuid.uuid4().hex}@service.invalid"
    email = email.lower().strip()

    if (await session.execute(select(User).where(User.email == email))).scalar_one_or_none():
        raise ValueError(f"A user with email {email!r} already exists.")

    role_name = service_role_name(label)
    if (
        await session.execute(select(TenantRole).where(TenantRole.name == role_name))
    ).scalar_one_or_none():
        raise ValueError(f"A tenant role named {role_name!r} already exists — pick a unique label.")

    user = await create_passwordless_user(
        session, email=email, full_name=label, is_active=True, password_login_disabled=True
    )
    membership = await ensure_membership(session, user_id=user.id, tenant_id=tenant_id)

    role = TenantRole(name=role_name, description=f"Service account: {label}", is_system=False)
    session.add(role)
    await session.flush()
    for key in keys:
        session.add(TenantRolePermission(role_id=role.id, permission_key=key))
    session.add(TenantMembershipRole(membership_id=membership.id, role_id=role.id))

    session.add(ServiceAccount(user_id=user.id, tenant_id=tenant_id, label=label))
    await session.flush()
    return user


async def delete_service_account(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    tenant_id: uuid.UUID,
) -> None:
    """Tear down a service account — the inverse of :func:`create_service_account`.

    Removes the ``ServiceAccount`` row, the user's membership in the tenant, the
    dedicated ``service:`` role(s) (with grants + membership-role link), and the
    global ``User`` row. ``session`` must be tenant-scoped (see module docstring).

    Raises :class:`ValueError` if ``user_id`` has no ``ServiceAccount`` row — this
    helper refuses to delete a normal user.
    """
    marker = (
        await session.execute(
            select(ServiceAccount).where(ServiceAccount.user_id == user_id)
        )
    ).scalar_one_or_none()
    if marker is None:
        raise ValueError(f"No service account with id {user_id}.")

    user = await session.get(User, user_id)

    membership = (
        await session.execute(
            select(TenantMembership).where(
                TenantMembership.user_id == user_id,
                TenantMembership.tenant_id == tenant_id,
            )
        )
    ).scalar_one_or_none()

    if membership is not None:
        links = (
            (
                await session.execute(
                    select(TenantMembershipRole).where(
                        TenantMembershipRole.membership_id == membership.id
                    )
                )
            )
            .scalars()
            .all()
        )
        role_ids = [link.role_id for link in links]
        for link in links:
            await session.delete(link)

        if role_ids:
            roles = (
                (
                    await session.execute(
                        select(TenantRole).where(
                            TenantRole.id.in_(role_ids),
                            TenantRole.name.startswith("service:"),
                            TenantRole.is_system.is_(False),
                        )
                    )
                )
                .scalars()
                .all()
            )
            for role in roles:
                grants = (
                    (
                        await session.execute(
                            select(TenantRolePermission).where(
                                TenantRolePermission.role_id == role.id
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
                for grant in grants:
                    await session.delete(grant)
                await session.delete(role)

        await session.delete(membership)

    await session.delete(marker)
    if user is not None:
        await session.delete(user)
    await session.flush()
