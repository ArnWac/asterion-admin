"""Built-in lightweight admin UI router.

Serves HTML shells; all data loading is done client-side via the Phase 6
admin contract API endpoints. Enabled only when ENABLE_BUILTIN_ADMIN_UI=True.
"""
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from adminfoundry.admin.ui_renderer import get_support_matrix, RENDERER_VERSION
from adminfoundry.settings import settings

_PACKAGE_ROOT = Path(__file__).parent.parent
TEMPLATES_DIR = _PACKAGE_ROOT / "templates" / "admin"
STATIC_DIR = _PACKAGE_ROOT / "static" / "admin"

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

router = APIRouter(tags=["admin-ui"])


def _tmpl(name: str, request: Request, **ctx):
    # Starlette 1.0 API: TemplateResponse(request, name, context)
    return templates.TemplateResponse(
        request, name, {"ui_base": settings.ADMIN_UI_PATH, "renderer_version": RENDERER_VERSION, **ctx}
    )


# ---------------------------------------------------------------------------
# Static assets — mounted separately in main.py
# ---------------------------------------------------------------------------

def get_static_app():
    return StaticFiles(directory=str(STATIC_DIR))


# ---------------------------------------------------------------------------
# Routes — explicit paths first, then parameterized
# ---------------------------------------------------------------------------

@router.get("/", response_class=HTMLResponse, include_in_schema=False)
async def admin_ui_root(request: Request):
    return RedirectResponse(url=f"{request.scope.get('root_path', '')}/login")


@router.get("/login", response_class=HTMLResponse, include_in_schema=False)
async def admin_ui_login(request: Request):
    return _tmpl("login.html", request)


@router.get("/dashboard", response_class=HTMLResponse, include_in_schema=False)
async def admin_ui_dashboard(request: Request):
    return _tmpl("nav.html", request, model=None)


@router.get("/renderer/support-matrix", response_class=JSONResponse, include_in_schema=False)
async def renderer_support_matrix():
    """Return the built-in renderer capability map."""
    return get_support_matrix()


# /{model_name}/new must come before /{model_name}/{object_id}
@router.get("/{model_name}/new", response_class=HTMLResponse, include_in_schema=False)
async def admin_ui_create(request: Request, model_name: str):
    return _tmpl("create.html", request, model=model_name)


# 3-segment routes (explicit suffixes) must come before /{model_name}/{object_id}
@router.get("/{model_name}/{object_id}/edit", response_class=HTMLResponse, include_in_schema=False)
async def admin_ui_edit(request: Request, model_name: str, object_id: str):
    return _tmpl("update.html", request, model=model_name, object_id=object_id)


@router.get("/{model_name}/{object_id}/delete", response_class=HTMLResponse, include_in_schema=False)
async def admin_ui_confirm_delete(request: Request, model_name: str, object_id: str):
    return _tmpl("confirm_delete.html", request, model=model_name, object_id=object_id)



@router.get("/{model_name}/{object_id}", response_class=HTMLResponse, include_in_schema=False)
async def admin_ui_detail(request: Request, model_name: str, object_id: str):
    return _tmpl("detail.html", request, model=model_name, object_id=object_id)


@router.get("/{model_name}", response_class=HTMLResponse, include_in_schema=False)
async def admin_ui_list(request: Request, model_name: str):
    return _tmpl("list.html", request, model=model_name)
