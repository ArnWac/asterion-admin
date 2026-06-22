"""Shared account-provisioning primitives.

Used by the member-onboarding router (:mod:`asterion.admin.member_router`) and
the public service-account helper (:mod:`asterion.auth.service_accounts`) so
the "passwordless user + tenant membership" creation lives in ONE place instead
of being re-stitched from auth/RBAC internals per caller.

These helpers touch the GLOBAL tables (``User``, ``TenantMembership``), which
carry an explicit ``public.`` schema qualifier — so they work on the same
tenant-scoped session the rest of the admin stack uses (``search_path`` =
``tenant_<slug>, public``).
"""

from __future__ import annotations

import secrets
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from asterion.auth.password import hash_password
from asterion.models.tenant_membership import TenantMembership
from asterion.models.user import User


def unusable_password_hash() -> str:
    """Return a password hash that no submitted password can match.

    Marks an account **passwordless**: the login path verifies the submitted
    password against this hash and always fails, so the account can only
    authenticate via a minted token (or after an invite/reset sets a real
    password). We hash a throwaway random secret and discard the plaintext.
    """
    return hash_password(secrets.token_urlsafe(32))


async def create_passwordless_user(
    session: AsyncSession,
    *,
    email: str,
    full_name: str | None = None,
    is_active: bool,
) -> User:
    """Create a passwordless, non-superadmin :class:`User` and flush it."""
    user = User(
        email=email,
        hashed_password=unusable_password_hash(),
        full_name=full_name,
        is_active=is_active,
        is_superadmin=False,
    )
    session.add(user)
    await session.flush()
    return user


async def ensure_membership(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    tenant_id: uuid.UUID,
) -> TenantMembership:
    """Create — or reactivate — the ``(user, tenant)`` membership. Idempotent."""
    membership = (
        await session.execute(
            select(TenantMembership).where(
                TenantMembership.user_id == user_id,
                TenantMembership.tenant_id == tenant_id,
            )
        )
    ).scalar_one_or_none()
    if membership is None:
        membership = TenantMembership(user_id=user_id, tenant_id=tenant_id, is_active=True)
        session.add(membership)
        await session.flush()
    elif not membership.is_active:
        membership.is_active = True
    return membership
