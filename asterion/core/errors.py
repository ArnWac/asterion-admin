"""Consistent error envelope (plan §PR-5).

Every API error response — whether raised from inside a handler, surfaced
by FastAPI's request validation, or bubbled up as an uncaught exception —
goes out as::

    {
      "error": {
        "code": "<stable_machine_readable_code>",
        "message": "<human readable summary>",
        "fields": [ {"name": "...", "message": "..."} ],   # optional
        "details": { ... },                                  # optional
        "request_id": "<correlation id from middleware>"
      }
    }

Clients can rely on the envelope shape and on the ``code`` taxonomy below
without having to inspect status codes.

To raise a custom envelope from a handler, either:

  * use :class:`AdminError`::

        raise AdminError(
            status_code=409,
            code="tenant_inactive",
            message="Tenant is inactive.",
        )

  * or raise a plain :class:`fastapi.HTTPException` and let the handler
    map the status code to one of the default codes below.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger(__name__)


# --- canonical error codes (plan §PR-5) ---

AUTHENTICATION_REQUIRED = "authentication_required"
INVALID_TOKEN = "invalid_token"
FORBIDDEN = "forbidden"
NOT_FOUND = "not_found"
VALIDATION_ERROR = "validation_error"
CONFLICT = "conflict"
RATE_LIMITED = "rate_limited"
INTERNAL_ERROR = "internal_error"


_STATUS_TO_DEFAULT_CODE: dict[int, str] = {
    status.HTTP_401_UNAUTHORIZED: AUTHENTICATION_REQUIRED,
    status.HTTP_403_FORBIDDEN: FORBIDDEN,
    status.HTTP_404_NOT_FOUND: NOT_FOUND,
    status.HTTP_409_CONFLICT: CONFLICT,
    status.HTTP_422_UNPROCESSABLE_CONTENT: VALIDATION_ERROR,
    status.HTTP_429_TOO_MANY_REQUESTS: RATE_LIMITED,
    status.HTTP_500_INTERNAL_SERVER_ERROR: INTERNAL_ERROR,
}

_DEFAULT_MESSAGES: dict[str, str] = {
    AUTHENTICATION_REQUIRED: "Authentication required.",
    INVALID_TOKEN: "Invalid or expired token.",
    FORBIDDEN: "Operation is not permitted.",
    NOT_FOUND: "Resource not found.",
    VALIDATION_ERROR: "Request validation failed.",
    CONFLICT: "Request conflicts with current state.",
    RATE_LIMITED: "Too many requests.",
    INTERNAL_ERROR: "Internal server error.",
}


class AdminError(HTTPException):
    """HTTPException carrying a stable error code + optional field list."""

    def __init__(
        self,
        *,
        status_code: int,
        code: str,
        message: str,
        fields: list[dict[str, str]] | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(status_code=status_code, detail=message)
        self.code = code
        self.message = message
        self.fields = fields
        self.details = details


def _request_id(request: Request) -> str | None:
    return getattr(getattr(request, "state", None), "request_id", None)


def _envelope_response(
    request: Request,
    *,
    status_code: int,
    code: str,
    message: str,
    fields: list[dict[str, str]] | None = None,
    details: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    error: dict[str, Any] = {
        "code": code,
        "message": message,
        "request_id": _request_id(request),
    }
    if fields is not None:
        error["fields"] = fields
    if details is not None:
        error["details"] = details
    return JSONResponse(
        status_code=status_code,
        content={"error": error},
        headers=headers,
    )


def error_response(
    request: Request,
    *,
    status_code: int,
    code: str,
    message: str,
    fields: list[dict[str, str]] | None = None,
    details: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    """Public helper for emitting the consistent error envelope from places
    that cannot raise an :class:`AdminError` (e.g. middleware, where the
    registered FastAPI exception handlers don't reliably fire)."""
    return _envelope_response(
        request,
        status_code=status_code,
        code=code,
        message=message,
        fields=fields,
        details=details,
        headers=headers,
    )


# --- handlers ---


def _extract_from_detail(detail: Any) -> tuple[str | None, list[dict[str, str]] | None]:
    """Pull a (message, fields) pair out of HTTPException.detail.

    Supports two legacy shapes from the older CRUD code::

        detail = "string"
        detail = {"message": "...", "fields": ["a", "b"]}

    plus the new richer shape::

        detail = {"code": "...", "message": "...", "fields": [...]}
    """
    if isinstance(detail, dict):
        message = detail.get("message")
        raw_fields = detail.get("fields")
        fields: list[dict[str, str]] | None = None
        if isinstance(raw_fields, list):
            normalized: list[dict[str, str]] = []
            for item in raw_fields:
                if isinstance(item, dict) and "name" in item:
                    normalized.append(
                        {
                            "name": str(item.get("name")),
                            "message": str(item.get("message") or "Invalid field."),
                        }
                    )
                else:
                    normalized.append({"name": str(item), "message": "Invalid field."})
            fields = normalized
        return message, fields
    if isinstance(detail, str):
        return detail, None
    return None, None


def _refine_code_for_401(message: str | None) -> str:
    """Distinguish 'no token provided' from 'bad token'."""
    text = (message or "").lower()
    if "authentication" in text or "credentials required" in text:
        return AUTHENTICATION_REQUIRED
    if "token" in text or "credential" in text or "expired" in text:
        return INVALID_TOKEN
    return AUTHENTICATION_REQUIRED


async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    code: str
    message: str | None
    fields: list[dict[str, str]] | None = None
    details: dict[str, Any] | None = None

    if isinstance(exc, AdminError):
        code = exc.code
        message = exc.message
        fields = exc.fields
        details = exc.details
    else:
        detail_message, detail_fields = _extract_from_detail(exc.detail)
        message = detail_message
        fields = detail_fields
        if isinstance(exc.detail, dict) and isinstance(exc.detail.get("code"), str):
            code = exc.detail["code"]
        else:
            code = _STATUS_TO_DEFAULT_CODE.get(exc.status_code, INTERNAL_ERROR)
            if exc.status_code == status.HTTP_401_UNAUTHORIZED:
                code = _refine_code_for_401(message)

    if not message:
        message = _DEFAULT_MESSAGES.get(code, "An error occurred.")

    return _envelope_response(
        request,
        status_code=exc.status_code,
        code=code,
        message=message,
        fields=fields,
        details=details,
        headers=getattr(exc, "headers", None),
    )


async def validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    fields: list[dict[str, str]] = []
    for err in exc.errors():
        loc = err.get("loc") or []
        # Drop the leading "body" / "query" prefix for readability.
        path = (
            ".".join(str(p) for p in loc[1:]) if len(loc) > 1 else (".".join(str(p) for p in loc))
        )
        fields.append(
            {
                "name": path or "?",
                "message": str(err.get("msg") or "Invalid value."),
            }
        )
    return _envelope_response(
        request,
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        code=VALIDATION_ERROR,
        message="Request validation failed.",
        fields=fields,
    )


async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception(
        "unhandled exception during request",
        extra={"request_id": _request_id(request)},
    )
    return _envelope_response(
        request,
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        code=INTERNAL_ERROR,
        message=_DEFAULT_MESSAGES[INTERNAL_ERROR],
    )


def register_error_handlers(app: FastAPI) -> None:
    """Replace FastAPI's default error responses with the envelope shape."""
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)
    app.add_exception_handler(HTTPException, http_exception_handler)
    app.add_exception_handler(RequestValidationError, validation_exception_handler)
    app.add_exception_handler(Exception, unhandled_exception_handler)
