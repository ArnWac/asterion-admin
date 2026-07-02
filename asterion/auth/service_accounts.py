"""Provision token-only service / machine accounts.

A device or service-to-service caller (e.g. a stationary time-clock terminal,
"terminal = user") needs an account that authenticates **only** via a minted
access token — never a password. Assembling that today means stitching together
five asterion internals (``User`` + ``TenantMembership`` + ``TenantRole`` +
``TenantRolePermission`` + ``TenantMembershipRole``). :func:`create_service_account`
is the public one-call helper.

It provisions an **active, passwordless** user, binds it to a tenant, and grants
it a dedicated role carrying the requested permission keys. It does **not** mint
tokens — that stays the caller's job (separation of concerns):

    from asterion.auth.service_accounts import create_service_account
    from asterion.auth.tokens import create_access_token

    user = await create_service_account(
        session,                       # MUST be tenant-scoped (see below)
        tenant_id=tenant.id,
        label="lobby-terminal",
        permission_keys=["admin.time_entries.create"],
    )
    token = create_access_token(
        user.id,
        secret_key=config.secret_key,
        algorithm=config.jwt_algorithm,
        expires_minutes=config.access_token_expire_minutes,
        token_version=user.token_version,
    )

Session scoping
---------------

The RBAC tables (``TenantRole`` / ``TenantRolePermission`` /
``TenantMembershipRole``) are **tenant-local**, so ``session`` must be scoped to
the tenant schema (``SET LOCAL search_path``) — exactly like ``get_async_session``
and the CRUD path. ``User`` + ``TenantMembership`` are global (``public``, via an
explicit schema qualifier), so the same session covers both.

Revocation comes for free
-------------------------

To cut a service account's existing tokens, bump ``user.token_version`` or set
``user.is_active = False`` — the standard per-user token-revocation invariant
applies to these accounts unchanged.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from asterion.auth.provisioning import create_passwordless_user, ensure_membership
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
      1. Create an **active, passwordless** ``User`` (``is_active=True``,
         ``is_superadmin=False``). ``POST /auth/login`` rejects it — there is no
         valid password. A synthetic, unique email is derived from ``label`` +
         a uuid when ``email`` is not given.
      2. Create the ``TenantMembership`` (idempotent on ``(user, tenant)``).
      3. Create a dedicated tenant role ``service:<label>``, grant it
         ``permission_keys``, and assign it to the membership.

    Returns the ``User`` so the caller can mint tokens with
    :func:`asterion.auth.tokens.create_access_token`.

    ``session`` must be tenant-scoped (see module docstring). Raises
    :class:`ValueError` for an already-used email, a duplicate
    ``service:<label>`` role, or a malformed permission key
    (:func:`asterion.security.validation.validate_permission_key`).
    """
    # Validate + de-duplicate keys, preserving order.
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
    await session.flush()
    return user


async def delete_service_account(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    tenant_id: uuid.UUID,
) -> None:
    """Tear down a service account — the inverse of :func:`create_service_account`.

    Removes the user's membership in the tenant, the dedicated ``service:``
    role(s) assigned to that membership (with their permission grants and the
    membership-role link), and the global ``User`` row. ``session`` must be
    tenant-scoped (see module docstring).

    Raises :class:`ValueError` if ``user_id`` is not a service account — this
    helper refuses to delete a normal user.
    """
    user = await session.get(User, user_id)
    if user is None or not user.password_login_disabled:
        raise ValueError(f"No service account with id {user_id}.")

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

    await session.delete(user)
    await session.flush()
