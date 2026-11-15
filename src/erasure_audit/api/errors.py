"""Domain errors and their RFC 9457 problem+json rendering."""
from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse


class AuditError(Exception):
    status = 500
    title = "Internal error"

    def __init__(self, detail: str):
        super().__init__(detail)
        self.detail = detail


class AuditNotFound(AuditError):
    status, title = 404, "Audit not found"


class InvalidState(AuditError):
    status, title = 409, "Audit is not in a state that allows this"


class RequestConflict(AuditError):
    status, title = 409, "A different request already uses this case_id"


class NotReady(AuditError):
    status, title = 503, "Service is starting or its backend is unavailable"


class BatchTooLarge(AuditError):
    status, title = 413, "Batch too large"


def problem(status: int, title: str, detail, request: Request) -> JSONResponse:
    body = {"type": "about:blank", "title": title, "status": status, "detail": detail,
            "instance": str(request.url.path), "request_id": getattr(request.state, "request_id", None)}
    return JSONResponse(body, status_code=status, media_type="application/problem+json")


def install(app: FastAPI) -> None:
    @app.exception_handler(AuditError)
    async def _domain(request: Request, exc: AuditError):
        return problem(exc.status, exc.title, exc.detail, request)

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError):
        errors = [{"loc": list(e["loc"]), "msg": e["msg"], "type": e["type"]} for e in exc.errors()]
        return problem(422, "Request validation failed", errors, request)
