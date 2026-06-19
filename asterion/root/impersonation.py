"""Impersonation endpoint at POST /api/v1/root/impersonate.

Only callable by a superadmin holding a normal access token (impersonation
tokens are rejected by ``require_superadmin``). Mints a short-lived
impersonation access token, persists an ``ImpersonationLog`` row, and
appends an ``impersonation_start`` audit entry.
"""

from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from asterion.audit import (
    IMPERSONATION_START,
    record_audit_in_session,
    request_audit_kwargs,
)
from asterion.auth.dependencies import require_superadmin
from asterion.auth.tokens import create_impersonation_token, get_token_jti
from asterion.db.dependencies import get_async_session
from asterion.models.impersonation_log import ImpersonationLog
from asterion.models.tenant import Tenant
from asterion.models.user import User

logger = logging.getLogger(__name__)


DEFAULT_IMPERSONATION_MINUTES = 60
MAX_IMPERSONATION_MINUTES = 8 * 60


router = APIRouter()


class ImpersonateRequest(BaseModel):
    target_user_id: uuid.UUID
    tenant_id: uuid.UUID | None = None
    duration_minutes: int | None = Field(default=None, ge=1, le=MAX_IMPERSONATION_MINUTES)


class ImpersonateResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    target_user_id: uuid.UUID
    tenant_id: uuid.UUID | None = None


async def _load_target(session: AsyncSession, target_user_id: uuid.UUID) -> User:
    result = await session.execute(select(User).where(User.id == target_user_id))
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Target user not found.",
        )
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Target user is inactive.",
        )
    return user


async def _validate_tenant(session: AsyncSession, tenant_id: uuid.UUID) -> Tenant:
    result = await session.execute(select(Tenant).where(Tenant.id == tenant_id))
    tenant = result.scalar_one_or_none()
    if tenant is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tenant not found.",
        )
    if not tenant.is_active:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Tenant is inactive.",
        )
    return tenant


@router.post("/impersonate", response_model=ImpersonateResponse)
async def impersonate(
    payload: ImpersonateRequest,
    request: Request,
    session: AsyncSession = Depends(get_async_session),
    current_user: User = Depends(require_superadmin),
) -> ImpersonateResponse:
    if payload.target_user_id == current_user.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot impersonate yourself.",
        )

    target = await _load_target(session, payload.target_user_id)

    if payload.tenant_id is not None:
        await _validate_tenant(session, payload.tenant_id)

    config = request.app.state.asterion.config
    duration = payload.duration_minutes or DEFAULT_IMPERSONATION_MINUTES

    token = create_impersonation_token(
        target.id,
        impersonated_by_user_id=current_user.id,
        tenant_id=payload.tenant_id,
        secret_key=config.secret_key,
        algorithm=config.jwt_algorithm,
        expires_minutes=duration,
        token_version=target.token_version,
        issuer=config.jwt_issuer,
        audience=config.jwt_audience,
    )

    from asterion.auth.tokens import decode_access_token

    decoded = decode_access_token(
        token,
        secret_key=config.secret_key,
        algorithm=config.jwt_algorithm,
        allow_impersonation=True,
        issuer=config.jwt_issuer,
        audience=config.jwt_audience,
    )
    jti = get_token_jti(decoded)

    session.add(
        ImpersonationLog(
            superadmin_id=current_user.id,
            target_user_id=target.id,
            tenant_id=payload.tenant_id,
            jti=jti,
        )
    )
    await session.flush()

    try:
        await record_audit_in_session(
            session,
            action=IMPERSONATION_START,
            actor=current_user,
            tenant_id=payload.tenant_id,
            resource="users",
            record_id=target.id,
            changes={
                "target_user_id": str(target.id),
                "tenant_id": str(payload.tenant_id) if payload.tenant_id else None,
                "duration_minutes": duration,
                "jti": jti,
            },
            **request_audit_kwargs(request, status_code=200),
        )
    except Exception:
        logger.warning(
            "impersonation audit hook failed for target=%s",
            target.id,
            exc_info=True,
        )

    return ImpersonateResponse(
        access_token=token,
        expires_in=duration * 60,
        target_user_id=target.id,
        tenant_id=payload.tenant_id,
    )
