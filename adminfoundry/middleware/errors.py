from fastapi import Request, status
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from starlette.middleware.base import BaseHTTPMiddleware


def _serializable_errors(errors: list) -> list:
    """Strip non-JSON-serializable objects from Pydantic v2 error dicts."""
    result = []
    for err in errors:
        e = {k: v for k, v in err.items() if k != "ctx"}
        if "ctx" in err:
            e["ctx"] = {
                k: str(v) if isinstance(v, Exception) else v
                for k, v in err["ctx"].items()
            }
        result.append(e)
    return result


async def validation_exception_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=422,
        content={"detail": "Validation error", "errors": _serializable_errors(exc.errors())},
    )


class UnhandledExceptionMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        try:
            return await call_next(request)
        except Exception:
            return JSONResponse(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                content={"detail": "Internal server error"},
            )
