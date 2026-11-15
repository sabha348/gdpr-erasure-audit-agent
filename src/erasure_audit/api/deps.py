from __future__ import annotations

from fastapi import Request

from .backend import AuditBackend
from .errors import NotReady


def get_backend(request: Request) -> AuditBackend:
    backend = getattr(request.app.state, "backend", None)
    if backend is None:
        raise NotReady("audit backend is not initialised")
    return backend
