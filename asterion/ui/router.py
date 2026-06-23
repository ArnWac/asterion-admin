from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

_PACKAGE_ROOT = Path(__file__).resolve().parent
TEMPLATES_DIR = _PACKAGE_ROOT / "templates"
STATIC_DIR = _PACKAGE_ROOT / "static"

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

router = APIRouter()


def get_static_app() -> StaticFiles:
    return StaticFiles(directory=str(STATIC_DIR))


def _template_context(
    request: Request,
    *,
    view: str,
    resource: str | None = None,
    record_id: str | None = None,
    page_id: str | None = None,
    page_module: str | None = None,
) -> dict[str, Any]:
    runtime = request.app.state.asterion
    config = runtime.config

    return {
        "config": config.to_safe_dict(),
        "admin_title": config.app_title,
        "admin_api_prefix": config.admin_api_prefix,
        "auth_api_prefix": config.auth_api_prefix,
        "admin_ui_path": config.admin_ui_path,
        "view": view,
        "resource": resource,
        "record_id": record_id,
        "page_id": page_id,
        "page_module": page_module,
    }


def _app(
    request: Request,
    *,
    view: str,
    resource: str | None = None,
    record_id: str | None = None,
    page_id: str | None = None,
    page_module: str | None = None,
):
    return templates.TemplateResponse(
        request,
        "app.html",
        _template_context(
            request,
            view=view,
            resource=resource,
            record_id=record_id,
            page_id=page_id,
            page_module=page_module,
        ),
    )


@router.get("/", response_class=HTMLResponse, include_in_schema=False)
async def ui_root(request: Request):
    return RedirectResponse(url=f"{request.app.state.asterion.config.admin_ui_path}/dashboard")


@router.get("/login", response_class=HTMLResponse, include_in_schema=False)
async def ui_login(request: Request):
    return templates.TemplateResponse(
        request,
        "login.html",
        _template_context(request, view="login"),
    )


@router.get("/login-complete", response_class=HTMLResponse, include_in_schema=False)
async def ui_login_complete(request: Request):
    """Landing page for the OAuth fragment-redirect.

    The OAuth callback ends with ``302 /admin/login-complete#token=…``
    so the JWT lives in the URL fragment (never the query string).
    This page's JS reads it, stores it under the same localStorage
    key the rest of the UI uses, replaces the URL to clear the
    fragment, and bounces to ``return_to``.
    """
    return templates.TemplateResponse(
        request,
        "login_complete.html",
        _template_context(request, view="login-complete"),
    )


@router.get("/dashboard", response_class=HTMLResponse, include_in_schema=False)
async def ui_dashboard(request: Request):
    return _app(request, view="dashboard")


@router.get("/settings", response_class=HTMLResponse, include_in_schema=False)
async def ui_settings(request: Request):
    return _app(request, view="settings")


@router.get("/permissions", response_class=HTMLResponse, include_in_schema=False)
async def ui_permissions(request: Request):
    """Permission-matrix view (Roadmap 5.2b).

    Renders the matrix shell; the JS view fetches roles + permissions
    + assignments from ``GET /_permission_matrix`` and the user
    toggles cells. Mounted on a static path (NOT under
    ``/{resource}``) so the dynamic CRUD router can't shadow it.
    """
    return _app(request, view="permissions")


@router.get("/_pages/{page_id}", response_class=HTMLResponse, include_in_schema=False)
async def ui_page(request: Request, page_id: str):
    """Serve a registered custom Admin Page (Roadmap 5.6).

    Mounted under the reserved ``_pages/`` prefix and BEFORE the dynamic
    ``/{resource}`` routes so a page slug can never be shadowed by — or
    shadow — a CRUD resource. The shell carries the page's ``js_module``
    so the SPA can dynamically import the page module without a second
    round-trip. Unknown slugs 404 instead of booting an empty shell.

    The shell itself is not data-bearing; the page's own API endpoints
    enforce permissions, and the sidebar link is permission-filtered by
    the navigation endpoint — same contract as every other UI route.
    """
    page = request.app.state.asterion.admin_pages.get(page_id)
    if page is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Admin page '{page_id}' is not registered.",
        )
    return _app(
        request,
        view="page",
        page_id=page.id,
        page_module=page.js_module,
    )


@router.get("/{resource}/new", response_class=HTMLResponse, include_in_schema=False)
async def ui_create(request: Request, resource: str):
    return _app(
        request,
        view="create",
        resource=resource,
    )


@router.get(
    "/{resource}/{record_id}/edit",
    response_class=HTMLResponse,
    include_in_schema=False,
)
async def ui_edit(request: Request, resource: str, record_id: str):
    return _app(
        request,
        view="edit",
        resource=resource,
        record_id=record_id,
    )


@router.get(
    "/{resource}/{record_id}/delete",
    response_class=HTMLResponse,
    include_in_schema=False,
)
async def ui_delete(request: Request, resource: str, record_id: str):
    return _app(
        request,
        view="delete",
        resource=resource,
        record_id=record_id,
    )


@router.get(
    "/{resource}/{record_id}/permissions",
    response_class=HTMLResponse,
    include_in_schema=False,
)
async def ui_role_permissions(request: Request, resource: str, record_id: str):
    """Per-role permission picker (two-list assign/unassign).

    Generic route; the view is meaningful for ``tenant_roles`` and is
    reached from that resource's detail page. Backed by the existing
    ``/_permission_matrix`` API scoped to one role.
    """
    return _app(request, view="role_permissions", resource=resource, record_id=record_id)


@router.get(
    "/{resource}/{record_id}",
    response_class=HTMLResponse,
    include_in_schema=False,
)
async def ui_detail(request: Request, resource: str, record_id: str):
    return _app(
        request,
        view="detail",
        resource=resource,
        record_id=record_id,
    )


@router.get("/{resource}", response_class=HTMLResponse, include_in_schema=False)
async def ui_list(request: Request, resource: str):
    return _app(
        request,
        view="list",
        resource=resource,
    )
