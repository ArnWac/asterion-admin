"""Anonymous-readable login-page metadata — Phase 8b.8.

Why this exists separately from ``/_contract``:

The main contract endpoint requires authentication. That's
intentional — the full contract enumerates every registered model
admin, including internal/sensitive ones whose names alone would help
an attacker map the attack surface.

But the login page needs to render an OAuth provider button list
BEFORE the user is authenticated. We can't ask anonymous callers to
hit ``/_contract``, and we don't want to silently widen its access
either. So this endpoint exposes ONLY what the login page actually
needs — the OAuth provider list — and nothing else.

Shape::

    GET /api/v1/admin/_login_contract
    -> {
         "oauth_providers": [
           {"id": "google", "label": "Google", "login_url": "/api/v1/oauth/google/login"}
         ]
       }

If the OAuth extension isn't installed, ``oauth_providers`` is an
empty list — the response shape stays stable so the UI can render
without special-casing the extension being absent.
"""

from __future__ import annotations

from fastapi import APIRouter, Request

router = APIRouter()


@router.get("/_login_contract")
async def get_login_contract(request: Request) -> dict:
    """Public surface for the login page — no auth required."""
    runtime = request.app.state.asterion
    contributions = runtime.contract_contributions.all()
    oauth_fragment = contributions.get("auth_oauth") or {}
    providers = oauth_fragment.get("providers") or []
    # Only echo the three fields the UI needs. If the extension ever
    # adds more fields to its contract contribution, they DO NOT leak
    # via this anonymous endpoint unless we explicitly add them here.
    return {
        "oauth_providers": [
            {
                "id": p.get("id"),
                "label": p.get("label"),
                "login_url": p.get("login_url"),
            }
            for p in providers
        ],
    }
