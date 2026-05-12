import math
import uuid
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import ValidationError
from sqlalchemy import select, func
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from adminfoundry import signals as _signals
from adminfoundry.pagination import paginate
from adminfoundry.admin.capabilities import build_capabilities, build_admin_context
from adminfoundry.admin.contract import build_model_contract, CONTRACT_VERSION
from adminfoundry.admin.filter_builder import filter_builder
from adminfoundry.admin.navigation import build_navigation
from adminfoundry.admin.registry import admin_site
from adminfoundry.admin.schema_builder import schema_builder
from adminfoundry.admin.serializer import serializer
from adminfoundry.admin.ui_preferences import UIPreference, get_preferences, set_preferences
from adminfoundry.admin.ui_renderer import get_support_matrix
from adminfoundry.authz.policy_engine import policy_engine
from adminfoundry.database import get_db, get_admin_db
from adminfoundry.dependencies import get_current_user, require_superadmin
from adminfoundry.models.user import User
from adminfoundry.extensions.observability.admin_metrics import get_snapshot as metrics_snapshot
from adminfoundry.schemas.client_config import ClientConfigResponse
from adminfoundry.schemas.policy import FieldPolicyMeta, ModelPolicyResponse
from adminfoundry.settings import settings

router = APIRouter(prefix="/api/v1/admin", tags=["admin"])


async def _resolve_impersonation_tenant(payload: dict, current_tenant, db: AsyncSession):
    """Return the Tenant for a same-origin impersonation token when TenantMiddleware
    didn't set it (no subdomain).  Returns current_tenant unchanged if it is already set
    or if the token carries no tenant_id."""
    if current_tenant is not None:
        return current_tenant
    if not (payload.get("impersonated_by") and payload.get("tenant_id")):
        return None
    from adminfoundry.models.tenant import Tenant as _Tenant
    return (
        await db.execute(
            select(_Tenant).where(_Tenant.id == uuid.UUID(payload["tenant_id"]))
        )
    ).scalar_one_or_none()


def _get_admin_or_404(model_name: str):
    model_admin = admin_site.get(model_name)
    if model_admin is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Model '{model_name}' not registered",
        )
    return model_admin


def _check_model_access(model_admin, user, token_payload: dict, tenant=None) -> None:
    """Raise 403 if the user lacks access to this model's admin CRUD interface.

    Superadmin without impersonation token in a tenant context → 403 (must use impersonation).
    Superadmin with impersonation token in tenant context → only tenant-scoped models allowed.
    """
    is_impersonating = bool(token_payload.get("impersonated_by"))

    if user.is_superadmin and not is_impersonating:
        if settings.MULTI_TENANT and tenant is not None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Use an impersonation token to access tenant panels",
            )
        if settings.MULTI_TENANT and tenant is None and model_admin.tenant_scoped:
            # global_only_in_root_panel models are accessible from the root panel;
            # _tenant_filter applies WHERE tenant_id IS NULL to scope to global records.
            if not getattr(model_admin, "global_only_in_root_panel", False):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Tenant context required — use impersonation to access tenant-scoped models",
                )
        return

    if user.is_superadmin and is_impersonating:
        token_tenant_id = token_payload.get("tenant_id")
        # Subdomain mode: token must match the resolved tenant
        if tenant is not None and token_tenant_id != str(tenant.id):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Impersonation token is not valid for this tenant",
            )
        # Same-origin mode: no subdomain, but token must carry tenant_id
        if tenant is None and not token_tenant_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Impersonation token is not valid for this tenant",
            )
        if model_admin.tenant_scoped:
            return
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only tenant-scoped models are accessible during tenant impersonation",
        )

    # Tenant admin: a user who holds the "tenant_admin" role scoped to the current tenant
    # gets full CRUD access to all tenant-scoped models in that tenant's panel.
    if tenant is not None and model_admin.tenant_scoped:
        for r in (user.roles or []):
            if r.name == "tenant_admin" and r.tenant_id == tenant.id:
                return

    if getattr(model_admin, "admin_only", True):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Superadmin required")
    access_roles = getattr(model_admin, "access_roles", [])
    if access_roles:
        user_role_names = {r.name for r in (user.roles or [])}
        if not user_role_names.intersection(access_roles):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient permissions"
            )


def _require_superadmin_or_impersonating(user, token_payload: dict, request: Request) -> None:
    """Allow superadmin (root panel or impersonating) OR tenant admin in their own tenant.

    Raises 403 otherwise.
    """
    from adminfoundry.auth_provider import AuthProvider
    provider = getattr(request.app.state, "auth_provider", AuthProvider())
    if provider.is_superadmin(user):
        # Superadmin always allowed (both regular and impersonation tokens).
        return
    # Tenant admin: allow if user holds tenant_admin role for the current tenant.
    tenant = getattr(request.state, "tenant", None)
    if tenant is not None:
        for r in (user.roles or []):
            if r.name == "tenant_admin" and r.tenant_id == tenant.id:
                return
    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Superadmin required")


def _tenant_filter(request: Request, model_admin):
    """Return a SQLAlchemy filter for tenant-scoped models, or None.

    Subdomain mode              → WHERE tenant_id = <tenant.id>
    Same-origin impersonation   → WHERE tenant_id = <token.tenant_id>
    Root panel, global model    → WHERE tenant_id IS NULL
    Root panel, non-scoped      → no filter (superadmin sees all)
    """
    if not model_admin.tenant_scoped or not settings.MULTI_TENANT:
        return None
    if not hasattr(model_admin.model, "tenant_id"):
        return None
    tenant = getattr(request.state, "tenant", None)
    if tenant is not None:
        return model_admin.model.tenant_id == str(tenant.id)
    # Same-origin impersonation: resolve tenant from token
    token_payload = getattr(request.state, "token_payload", {})
    if token_payload.get("impersonated_by") and token_payload.get("tenant_id"):
        return model_admin.model.tenant_id == token_payload["tenant_id"]
    if getattr(model_admin, "global_only_in_root_panel", False):
        return model_admin.model.tenant_id.is_(None)
    return None


def _validate_body(schema_class, body: dict):
    """Validate raw body dict against a dynamic schema; raise 422 on failure."""
    try:
        return schema_class.model_validate(body)
    except ValidationError as exc:
        from adminfoundry.middleware.errors import _serializable_errors
        raise HTTPException(
            status_code=422,
            detail={"detail": "Validation error", "errors": _serializable_errors(exc.errors())},
        )


# ---------------------------------------------------------------------------
# Registry overview
# ---------------------------------------------------------------------------

@router.get("")
async def list_registered_models(
    request: Request,
    current_user: User = Depends(get_current_user),
):
    """Return registry metadata filtered for the current panel context.

    Root panel (not impersonating): superadmin required; returns non-tenant-scoped models
    + global_only_in_root_panel.
    Tenant panel (impersonating or subdomain): returns tenant_scoped models only.
    Impersonation tokens are allowed here because this is read-only registry metadata
    needed to populate form selects in the tenant panel.
    """
    payload = getattr(request.state, "token_payload", {})
    tenant = getattr(request.state, "tenant", None)
    is_impersonating = bool(payload.get("impersonated_by"))
    in_tenant_context = is_impersonating or tenant is not None

    # Root panel: require superadmin; impersonation tokens may not browse root registry
    if not in_tenant_context:
        from adminfoundry.auth_provider import AuthProvider
        provider = getattr(request.app.state, "auth_provider", AuthProvider())
        if not provider.is_superadmin(current_user):
            raise HTTPException(status_code=403, detail="Superadmin required")

    all_meta = admin_site.metadata()
    if in_tenant_context:
        models = [
            m for m in all_meta
            if getattr(admin_site.get(m["model"]), "tenant_scoped", False)
        ]
    else:
        models = [
            m for m in all_meta
            if not getattr(admin_site.get(m["model"]), "tenant_scoped", False)
            or getattr(admin_site.get(m["model"]), "global_only_in_root_panel", False)
        ]
    return {"models": models}


# ---------------------------------------------------------------------------
# Phase 6 — admin contract endpoints
# These fixed-path routes must appear BEFORE /{model_name} to prevent FastAPI
# from matching "context"/"navigation"/"capabilities" as a model_name parameter.
# ---------------------------------------------------------------------------

@router.get("/context")
async def admin_context(
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Return authenticated admin context — user info, tenant, impersonation state."""
    payload = getattr(request.state, "token_payload", {})
    t = await _resolve_impersonation_tenant(payload, getattr(request.state, "tenant", None), db)
    if t is not None:
        request.state.tenant = t
    return build_admin_context(current_user, payload, request)


@router.get("/navigation")
async def admin_navigation(
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Return visible navigation structure for the current user and context."""
    payload = getattr(request.state, "token_payload", {})
    tenant = await _resolve_impersonation_tenant(
        payload, getattr(request.state, "tenant", None), db
    )
    return build_navigation(current_user, payload, admin_site, tenant=tenant)


@router.get("/capabilities")
async def admin_capabilities(
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Return UI-safe capability metadata for the current user and context."""
    payload = getattr(request.state, "token_payload", {})
    from adminfoundry.authz.role_caps import fetch_all_model_caps
    all_db_caps = await fetch_all_model_caps(current_user, db)
    in_tenant_context = bool(
        payload.get("impersonated_by") or getattr(request.state, "tenant", None)
    )
    return build_capabilities(current_user, payload, admin_site, all_db_caps or None, in_tenant_context=in_tenant_context)


@router.get("/client-config", response_model=ClientConfigResponse)
async def client_config(
    _: User = Depends(get_current_user),
):
    """
    Bootstrap config for external renderer clients (e.g. Flutter).

    Returns the active contract version, renderer support matrix, canonical
    endpoint map, and deprecation policy.  Clients must not depend on
    built-in-UI internals; consume this endpoint instead.
    """
    matrix = get_support_matrix()
    return ClientConfigResponse(
        contract_version=CONTRACT_VERSION,
        renderer_id=matrix["renderer"],
        renderer_version=matrix["version"],
        supported_features=matrix["supported"],
        endpoints={
            "context": "/api/v1/admin/context",
            "navigation": "/api/v1/admin/navigation",
            "capabilities": "/api/v1/admin/capabilities",
            "registry": "/api/v1/admin",
            "client_config": "/api/v1/admin/client-config",
            "model_meta": "/api/v1/admin/{model}/meta",
            "model_list": "/api/v1/admin/{model}",
            "model_lookup": "/api/v1/admin/{model}/lookup",
        },
        breaking_change_policy=(
            "A breaking change increments the major contract_version. "
            "Clients must check contract_version on bootstrap and refuse "
            "to operate against an unsupported major version."
        ),
        additive_change_policy=(
            "New optional fields may be added without changing contract_version. "
            "Clients must ignore unknown fields (Postel's law)."
        ),
    )


@router.get("/metrics")
async def admin_metrics(
    _: User = Depends(require_superadmin),
):
    """Return admin operational metrics snapshot — no secrets or protected field content."""
    return metrics_snapshot()


@router.get("/dashboard")
async def admin_dashboard(
    request: Request,
    db: AsyncSession = Depends(get_admin_db),
    current_user: User = Depends(get_current_user),
):
    """Return rendered dashboard widgets for the current user."""
    from adminfoundry.dashboard import DEFAULT_WIDGETS

    payload = getattr(request.state, "token_payload", {})
    t = await _resolve_impersonation_tenant(payload, getattr(request.state, "tenant", None), db)
    if t is not None:
        request.state.tenant = t

    widgets_cfg = (
        (_admin_config.dashboard_widgets if _admin_config else None) or DEFAULT_WIDGETS
    )
    provider = getattr(request.app.state, "auth_provider", None)
    is_super = provider.is_superadmin(current_user) if provider else getattr(current_user, "is_superadmin", False)

    result = []
    for w in widgets_cfg:
        if w.superadmin_only and not is_super:
            continue
        try:
            data = await w.get_data(current_user, db, request)
        except Exception:
            data = {}
        result.append({"id": w.id, "title": w.title, "type": w.widget_type(), "data": data})
    return {"widgets": result}


@router.get("/compatibility")
async def admin_compatibility(
    _: User = Depends(get_current_user),
):
    """
    Multi-surface compatibility manifest.
    Describes which flows are baseline (builtin UI + external client),
    advanced (enterprise client only), or client-specific.
    """
    matrix = get_support_matrix()
    return {
        "contract_version": CONTRACT_VERSION,
        "surfaces": {
            "builtin_ui": {
                "renderer": matrix["renderer"],
                "version": matrix["version"],
                "supported_features": matrix["supported"],
            },
            "external_client": {
                "note": "Must consume the same admin contract endpoints as builtin UI.",
                "additional_hints": ["renderer_hints", "async_actions", "requires_approval"],
            },
            "api_only": {
                "note": "All contract endpoints remain functional when builtin UI is disabled.",
            },
        },
        "baseline_flows": [
            "list", "detail", "create", "update", "delete",
            "search", "filter", "order", "pagination",
            "tenant_context", "impersonation_indicator",
            "auth_login", "auth_logout", "auth_refresh",
        ],
        "advanced_flows": [
            "workflow_approval", "bulk_action",
            "import_export", "job_tracking", "step_up_auth",
            "session_management", "audit_visibility",
        ],
        "breaking_change_policy": (
            "Major contract_version increment signals a breaking change. "
            "Clients must refuse to operate against an unsupported major version."
        ),
    }


@router.get("/profile")
async def get_profile(
    current_user: User = Depends(get_current_user),
):
    """Return the current user's own profile."""
    from adminfoundry.schemas.user import UserPublic
    return UserPublic.model_validate(current_user)


@router.patch("/profile")
async def update_profile(
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Update name, email, or password for the current user."""
    from adminfoundry.auth import hash_password, verify_password
    from adminfoundry.schemas.user import ProfileUpdate, UserPublic

    body = ProfileUpdate(**await request.json())

    if body.new_password is not None or body.current_password is not None:
        if not body.current_password or not body.new_password:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                                detail="Both current_password and new_password are required")
        if not verify_password(body.current_password, current_user.hashed_password):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                                detail="Current password is incorrect")
        current_user.hashed_password = hash_password(body.new_password)

    if body.email is not None and body.email != current_user.email:
        conflict = (await db.execute(
            select(User).where(User.email == body.email, User.id != current_user.id)
        )).scalar_one_or_none()
        if conflict:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email already in use")
        current_user.email = body.email

    if body.full_name is not None:
        current_user.full_name = body.full_name

    await db.commit()
    await db.refresh(current_user)
    return UserPublic.model_validate(current_user)


@router.get("/preferences")
async def get_user_preferences(
    current_user: User = Depends(get_current_user),
):
    """Return personal UI display preferences for the current user."""
    return get_preferences(str(current_user.id))


@router.put("/preferences")
async def update_user_preferences(
    prefs: UIPreference,
    current_user: User = Depends(get_current_user),
):
    """Persist personal UI display preferences — never overrides server permissions."""
    return set_preferences(str(current_user.id), prefs)


# ---------------------------------------------------------------------------
# Model list / create
# ---------------------------------------------------------------------------

@router.get("/{model_name}")
async def list_objects(
    model_name: str,
    request: Request,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    q: str | None = Query(None),
    order_by: str | None = Query(None),
    db: AsyncSession = Depends(get_admin_db),
    current_user: User = Depends(get_current_user),
):
    model_admin = _get_admin_or_404(model_name)
    payload = getattr(request.state, "token_payload", {})
    _check_model_access(model_admin, current_user, payload, tenant=getattr(request.state, "tenant", None))

    stmt = select(model_admin.model)

    # Tenant filter
    tf = _tenant_filter(request, model_admin)
    if tf is not None:
        stmt = stmt.where(tf)

    # Policy record filter (restricts non-superadmin scope)
    rf = policy_engine.get_record_filter(current_user, model_admin, payload)
    if rf is not None:
        stmt = stmt.where(rf)

    # Search
    search = filter_builder.build_search(model_admin, q)
    if search is not None:
        stmt = stmt.where(search)

    # Field filters (from query params)
    for f in filter_builder.build_filters(model_admin, dict(request.query_params)):
        stmt = stmt.where(f)

    # Ordering
    ordering = filter_builder.build_ordering(model_admin, order_by)
    if ordering is not None:
        stmt = stmt.order_by(ordering)

    items, total, pages = await paginate(db, stmt, page, page_size)
    return {
        "items": serializer.serialize_many(items, model_admin),
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": pages,
    }


@router.post("/{model_name}", status_code=status.HTTP_201_CREATED)
async def create_object(
    model_name: str,
    request: Request,
    db: AsyncSession = Depends(get_admin_db),
    current_user: User = Depends(get_current_user),
):
    model_admin = _get_admin_or_404(model_name)
    payload = getattr(request.state, "token_payload", {})
    _check_model_access(model_admin, current_user, payload, tenant=getattr(request.state, "tenant", None))

    create_schema = schema_builder.build_create_schema(model_admin)
    body = await request.json()
    validated = _validate_body(create_schema, body)

    # Enforce field-level edit policy
    for field_name in validated.model_dump(exclude_none=True):
        fp = policy_engine.evaluate_field(current_user, model_admin, field_name, payload)
        if not fp.can_edit:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Field '{field_name}' is not editable",
            )

    data = model_admin.before_create(validated.model_dump(exclude_none=True))

    # For tenant-scoped models, auto-inject tenant_id from the request context.
    # The field is protected (not in the schema), so the client cannot set it.
    if model_admin.tenant_scoped and settings.MULTI_TENANT and hasattr(model_admin.model, "tenant_id"):
        tenant = getattr(request.state, "tenant", None)
        if tenant is not None:
            data.setdefault("tenant_id", str(tenant.id))
        elif payload.get("impersonated_by") and payload.get("tenant_id"):
            # Same-origin impersonation: inject tenant_id from token
            data.setdefault("tenant_id", payload["tenant_id"])

    obj = model_admin.model(**data)
    db.add(obj)
    try:
        await db.commit()
        await db.refresh(obj)
    except IntegrityError as exc:
        await db.rollback()
        orig = str(exc.orig)
        if "UNIQUE constraint" in orig or "unique constraint" in orig:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="A record with these values already exists",
            )
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=orig)
    except OperationalError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Database error: {exc.orig}",
        )

    request.state.audit_action = "created"
    request.state.audit_object_id = str(obj.id)
    request.state.audit_actor = current_user.email

    await _signals.emit("post_create", model_name=model_name, obj=obj, user=current_user)
    return serializer.serialize(obj, model_admin)


# ---------------------------------------------------------------------------
# Model meta / detail / update / delete
# /{model_name}/meta must appear BEFORE /{model_name}/{object_id} so that the
# literal "meta" segment is matched before FastAPI tries to coerce it to UUID.
# ---------------------------------------------------------------------------

@router.get("/{model_name}/meta")
async def model_meta(
    model_name: str,
    request: Request,
    current_user: User = Depends(get_current_user),
):
    """Return full field and action contract metadata for a registered model."""
    model_admin = _get_admin_or_404(model_name)
    payload = getattr(request.state, "token_payload", {})
    _check_model_access(model_admin, current_user, payload, tenant=getattr(request.state, "tenant", None))
    return build_model_contract(model_admin, registry=admin_site)


@router.get("/{model_name}/lookup")
async def lookup_objects(
    model_name: str,
    request: Request,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    q: str | None = Query(None),
    db: AsyncSession = Depends(get_admin_db),
    current_user: User = Depends(get_current_user),
):
    """
    Generic async relation-selection lookup for external clients.

    Returns lightweight {id, label} items suitable for select widgets.
    Uses search_fields for q-based filtering and lookup_field (or
    list_display[0]) as the display label.  Always tenant-safe and
    protected-field safe.
    """
    model_admin = _get_admin_or_404(model_name)
    payload = getattr(request.state, "token_payload", {})
    _check_model_access(model_admin, current_user, payload, tenant=getattr(request.state, "tenant", None))

    stmt = select(model_admin.model)

    tf = _tenant_filter(request, model_admin)
    if tf is not None:
        stmt = stmt.where(tf)

    search = filter_builder.build_search(model_admin, q)
    if search is not None:
        stmt = stmt.where(search)

    items, total, pages = await paginate(db, stmt, page, page_size)

    label_field = (
        model_admin.lookup_field
        or (model_admin.list_display[0] if model_admin.list_display else None)
    )
    result = []
    for obj in items:
        label = str(getattr(obj, label_field, None) or obj.id) if label_field else str(obj.id)
        result.append({"id": str(obj.id), "label": label})

    return {
        "items": result,
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": pages,
    }


@router.get("/{model_name}/policy")
async def model_policy(
    model_name: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Return effective field policies and capability flags for the current user."""
    model_admin = _get_admin_or_404(model_name)
    payload = getattr(request.state, "token_payload", {})

    from adminfoundry.admin.contract import build_field_metadata
    from adminfoundry.authz.role_caps import fetch_model_caps
    fields = build_field_metadata(model_admin)
    field_policies = [
        FieldPolicyMeta(
            field=f.name,
            **vars(policy_engine.evaluate_field(current_user, model_admin, f.name, payload)),
        )
        for f in fields
    ]
    db_caps = await fetch_model_caps(current_user, model_name, db)
    in_tenant_context = bool(
        payload.get("impersonated_by") or getattr(request.state, "tenant", None)
    )
    caps = policy_engine.effective_model_caps(current_user, model_admin, payload, db_caps=db_caps, in_tenant_context=in_tenant_context)
    return ModelPolicyResponse(
        model=model_name,
        contract_version=CONTRACT_VERSION,
        field_policies=field_policies,
        can_list=caps["can_list"],
        can_create=caps["can_create"],
        can_read=caps["can_read"],
        can_update=caps["can_update"],
        can_delete=caps["can_delete"],
    )


# ---------------------------------------------------------------------------
# Permission matrix — must be before /{model_name}/{object_id} to avoid conflict
# ---------------------------------------------------------------------------

@router.get("/permission-matrix/{role_id}")
async def get_permission_matrix(
    role_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Return CRUD caps for every registered model for this role."""
    payload = getattr(request.state, "token_payload", {})
    _require_superadmin_or_impersonating(current_user, payload, request)

    from sqlalchemy import select as _select
    from adminfoundry.models.role_permission import RolePermission
    rows = (
        await db.execute(
            _select(RolePermission).where(RolePermission.role_id == role_id)
        )
    ).scalars().all()
    perms = {r.model_name: r for r in rows}

    # In tenant context, show only tenant-scoped models
    is_impersonating = bool(payload.get("impersonated_by"))
    tenant = getattr(request.state, "tenant", None)
    in_tenant_context = is_impersonating or tenant is not None
    if in_tenant_context:
        names = [mn for mn in admin_site.model_names()
                 if getattr(admin_site.get(mn), "tenant_scoped", False)]
    else:
        names = admin_site.model_names()

    return [
        {
            "model_name": mn,
            "label": getattr(admin_site.get(mn), "label_plural", mn),
            "can_list": perms[mn].can_list if mn in perms else False,
            "can_create": perms[mn].can_create if mn in perms else False,
            "can_update": perms[mn].can_update if mn in perms else False,
            "can_delete": perms[mn].can_delete if mn in perms else False,
        }
        for mn in names
    ]


@router.put("/permission-matrix/{role_id}", status_code=status.HTTP_204_NO_CONTENT)
async def save_permission_matrix(
    role_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Replace all RolePermission records for this role with the submitted matrix."""
    payload = getattr(request.state, "token_payload", {})
    _require_superadmin_or_impersonating(current_user, payload, request)

    from sqlalchemy import delete as _delete, select as _select
    from adminfoundry.models.role_permission import RolePermission

    body = await request.json()  # list of {model_name, can_list, can_create, can_update, can_delete}
    ops = ("can_list", "can_create", "can_update", "can_delete")

    # Snapshot old state for audit diff
    old_rows = (await db.execute(
        _select(RolePermission).where(RolePermission.role_id == role_id)
    )).scalars().all()
    old_map = {r.model_name: {op: getattr(r, op) for op in ops} for r in old_rows}

    await db.execute(_delete(RolePermission).where(RolePermission.role_id == role_id))

    # Resolve tenant_id so matrix records stay consistent with CRUD-created records.
    injected_tenant_id: uuid.UUID | None = None
    if settings.MULTI_TENANT:
        tenant = getattr(request.state, "tenant", None)
        if tenant is not None:
            injected_tenant_id = tenant.id
        elif payload.get("impersonated_by") and payload.get("tenant_id"):
            try:
                injected_tenant_id = uuid.UUID(payload["tenant_id"])
            except (ValueError, AttributeError):
                pass

    new_map: dict = {}
    for entry in body:
        caps = {op: bool(entry.get(op, False)) for op in ops}
        if any(caps.values()):
            db.add(RolePermission(
                role_id=role_id,
                model_name=entry["model_name"],
                tenant_id=injected_tenant_id,
                **caps,
            ))
            new_map[entry["model_name"]] = caps

    # Build audit diff — only models where something changed
    changes: dict = {}
    all_models = set(old_map) | set(new_map)
    for mn in sorted(all_models):
        old_caps = old_map.get(mn, {op: False for op in ops})
        new_caps = new_map.get(mn, {op: False for op in ops})
        if old_caps != new_caps:
            old_label = " ".join(op.replace("can_", "") for op in ops if old_caps.get(op))
            new_label = " ".join(op.replace("can_", "") for op in ops if new_caps.get(op))
            changes[mn] = {"from": old_label or "—", "to": new_label or "—"}

    await db.commit()

    request.state.audit_action = "updated"
    request.state.audit_object_id = str(role_id)
    request.state.audit_actor = current_user.email
    request.state.audit_changes = changes or None


@router.post("/{model_name}/bulk-action")
async def bulk_action_direct(
    model_name: str,
    request: Request,
    db: AsyncSession = Depends(get_admin_db),
    current_user: User = Depends(get_current_user),
):
    """Execute a declared bulk action directly — no job queue required."""
    from adminfoundry.admin.actions import AdminAction as _AdminAction

    model_admin = _get_admin_or_404(model_name)
    payload = getattr(request.state, "token_payload", {})
    _check_model_access(model_admin, current_user, payload, tenant=getattr(request.state, "tenant", None))

    body = await request.json()
    action_name: str = body.get("action", "")
    object_ids: list = body.get("object_ids", [])

    def _attr(a, key, default=None):
        return getattr(a, key, None) if isinstance(a, _AdminAction) else a.get(key, default)

    action_def = next(
        (a for a in (model_admin.actions or []) if _attr(a, "name") == action_name), None
    )
    if action_def is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail=f"Action '{action_name}' not defined on '{model_name}'")
    if not _attr(action_def, "bulk", False):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail=f"Action '{action_name}' does not support bulk execution")

    objects = (
        await db.execute(select(model_admin.model).where(model_admin.model.id.in_(object_ids)))
    ).scalars().all()

    if not isinstance(action_def, _AdminAction):
        raise HTTPException(status_code=status.HTTP_501_NOT_IMPLEMENTED,
                            detail=f"Action '{action_name}' has no execute() implementation — use AdminAction subclass")

    try:
        result = await action_def.execute(objects, db, current_user)
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc))

    request.state.audit_action = "bulk_action"
    request.state.audit_object_id = action_name
    request.state.audit_actor = current_user.email

    return {
        "action": action_name,
        "affected": len(objects),
        "summary": result.get("summary", f"{len(objects)} object(s) updated"),
    }


@router.post("/upload", status_code=status.HTTP_201_CREATED)
async def upload_file(
    request: Request,
    current_user: User = Depends(get_current_user),
):
    """Upload a file to the configured storage backend. Returns {path, url}."""
    from fastapi import UploadFile
    from adminfoundry.storage import storage, generate_path

    content_type = request.headers.get("content-type", "")
    if "multipart/form-data" not in content_type:
        raise HTTPException(status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                            detail="Multipart form-data required")

    form = await request.form()
    file: UploadFile | None = form.get("file")  # type: ignore[assignment]
    if file is None:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail="Field 'file' is required")

    prefix = str(form.get("prefix", ""))
    path = generate_path(file.filename or "upload", prefix)
    saved = await storage.save(path, file.file)
    return {"path": saved, "url": storage.url(saved)}


@router.get("/{model_name}/{object_id}")
async def get_object(
    model_name: str,
    object_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_admin_db),
    current_user: User = Depends(get_current_user),
):
    model_admin = _get_admin_or_404(model_name)
    payload = getattr(request.state, "token_payload", {})
    _check_model_access(model_admin, current_user, payload, tenant=getattr(request.state, "tenant", None))

    obj = (
        await db.execute(select(model_admin.model).where(model_admin.model.id == object_id))
    ).scalar_one_or_none()
    if obj is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Object not found")

    rp = policy_engine.check_record_access(current_user, model_admin, obj, payload)
    if not rp.can_read:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access to this record is denied")

    return serializer.serialize(obj, model_admin)


@router.patch("/{model_name}/{object_id}")
async def update_object(
    model_name: str,
    object_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_admin_db),
    current_user: User = Depends(get_current_user),
):
    model_admin = _get_admin_or_404(model_name)
    payload = getattr(request.state, "token_payload", {})
    _check_model_access(model_admin, current_user, payload, tenant=getattr(request.state, "tenant", None))

    update_schema = schema_builder.build_update_schema(model_admin)
    body = await request.json()
    validated = _validate_body(update_schema, body)

    obj = (
        await db.execute(select(model_admin.model).where(model_admin.model.id == object_id))
    ).scalar_one_or_none()
    if obj is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Object not found")

    rp = policy_engine.check_record_access(current_user, model_admin, obj, payload)
    if not rp.can_update:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access to this record is denied")

    # Enforce field-level edit policy (pass obj for per-record hooks)
    for field_name in validated.model_dump(exclude_none=True):
        fp = policy_engine.evaluate_field(current_user, model_admin, field_name, payload, record=obj)
        if not fp.can_edit:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Field '{field_name}' is not editable",
            )

    changes = {}
    for field, value in validated.model_dump(exclude_none=True).items():
        old = getattr(obj, field, None)
        if str(old) != str(value):
            changes[field] = {"from": str(old) if old is not None else None, "to": str(value)}
        setattr(obj, field, value)

    await db.commit()
    await db.refresh(obj)

    request.state.audit_action = "updated"
    request.state.audit_object_id = str(object_id)
    request.state.audit_actor = current_user.email
    request.state.audit_changes = changes or None

    await _signals.emit("post_update", model_name=model_name, obj=obj, user=current_user, changes=changes)
    return serializer.serialize(obj, model_admin)


@router.delete("/{model_name}/{object_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_object(
    model_name: str,
    object_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_admin_db),
    current_user: User = Depends(get_current_user),
):
    model_admin = _get_admin_or_404(model_name)
    if not getattr(model_admin, "allow_delete", True):
        raise HTTPException(status_code=status.HTTP_405_METHOD_NOT_ALLOWED, detail="Deletion not allowed for this model")
    payload = getattr(request.state, "token_payload", {})
    _check_model_access(model_admin, current_user, payload, tenant=getattr(request.state, "tenant", None))

    obj = (
        await db.execute(select(model_admin.model).where(model_admin.model.id == object_id))
    ).scalar_one_or_none()
    if obj is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Object not found")

    rp = policy_engine.check_record_access(current_user, model_admin, obj, payload)
    if not rp.can_delete:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access to this record is denied")

    await _signals.emit("pre_delete", model_name=model_name, obj=obj, user=current_user)
    await db.delete(obj)
    await db.commit()

    request.state.audit_action = "deleted"
    request.state.audit_object_id = str(object_id)
    request.state.audit_actor = current_user.email
    await _signals.emit("post_delete", model_name=model_name, object_id=str(object_id), user=current_user)


def create_coreadmin(app, config=None) -> None:
    """Register the admin router on a FastAPI app.

    config: optional CoreAdminConfig instance.  When provided, enabled-feature
    metadata is surfaced through /admin/context and diagnostics.
    """
    global _admin_config
    _admin_config = config

    from adminfoundry.auth_provider import AuthProvider
    app.state.auth_provider = (config.auth_provider if config else None) or AuthProvider()

    if config:
        from adminfoundry import cache as _cache_mod, storage as _storage_mod, i18n as _i18n_mod
        if config.cache_backend:
            _cache_mod.configure(config.cache_backend)
        if config.storage_backend:
            _storage_mod.configure(config.storage_backend)
        if config.default_language:
            _i18n_mod.set_default_language(config.default_language)

    app.include_router(router)

    # Jobs + Import/Export — always-on, no extra pip deps
    from adminfoundry.extensions.jobs.models import Job as _Job  # noqa: F401 — register table with Base.metadata
    from adminfoundry.extensions.jobs.router import router as _jobs_router
    app.include_router(_jobs_router)

    from adminfoundry.routers import admin_ui as _admin_ui_module
    _admin_ui_module._locale_defaults = {
        "language": config.default_language if config else "en",
        "date_format": config.default_date_format if config else "locale",
        "date_pattern": config.default_date_pattern if config else "%Y-%m-%d %H:%M",
        "show_timezone": config.default_show_timezone if config else False,
    }

    if config is None or config.enable_builtin_ui:
        from adminfoundry.settings import settings
        from adminfoundry.routers.admin_ui import router as admin_ui_router, get_static_app
        ui_path = settings.ADMIN_UI_PATH
        app.mount(f"{ui_path}/static", get_static_app(), name="admin-static")
        app.include_router(admin_ui_router, prefix=ui_path)

    # Always include the audit endpoint (required for change history in the UI)
    from adminfoundry.routers.audit import router as audit_router
    app.include_router(audit_router)

    # Add audit middleware when enabled (writes a log entry after every response)
    if config is None or config.enable_basic_audit:
        from adminfoundry.middleware.audit import AuditMiddleware
        app.add_middleware(AuditMiddleware)


# Module-level config reference — set by create_coreadmin; None until wired
_admin_config = None
