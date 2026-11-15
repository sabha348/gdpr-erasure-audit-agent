"""FastAPI application factory.

    uvicorn erasure_audit.api.app:create_app --factory --host 0.0.0.0 --port 8000
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

import anyio
from fastapi import FastAPI

from .. import __version__
from ..logging_setup import configure
from ..pipeline import Options
from ..remediation import DryRunExecutor
from ..service import AuditService
from ..settings import Settings, get_settings
from . import errors
from .backend import AuditBackend, SyncBackend
from .middleware import RequestContextMiddleware
from .routes import router

log = logging.getLogger("erasure_audit.api")


async def build_backend(settings: Settings, service: AuditService) -> AuditBackend:
    if settings.execution_mode == "temporal":
        from ..workflows.worker import connect
        from .temporal_backend import TemporalBackend
        return TemporalBackend(await connect(settings), settings)
    from .store import InMemoryAuditStore
    return SyncBackend(service, InMemoryAuditStore(), DryRunExecutor(settings.remediation_outbox))


def create_app(settings: Settings | None = None, *, service: AuditService | None = None,
               backend: AuditBackend | None = None, configure_logging: bool = True) -> FastAPI:
    settings = settings or get_settings()
    if configure_logging:
        configure(settings.log_level, settings.log_json)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        svc = service or await anyio.to_thread.run_sync(
            lambda: AuditService(settings.dataset_dir, Options(classifier=settings.classifier)))
        app.state.service = svc
        app.state.backend = backend or await build_backend(settings, svc)
        log.info("ready", extra={"backend": app.state.backend.name, "startup_s": round(svc.startup_s, 3),
                                 "classifier": svc.options.classifier})
        yield
        app.state.backend = None

    app = FastAPI(title="GDPR Erasure Audit Agent", version=__version__, lifespan=lifespan,
                  description="Audits whether an erasure request really removed a person's data, and proposes an "
                              "ordered remediation plan that a human approves before it is issued.")
    app.state.settings = settings
    app.add_middleware(RequestContextMiddleware)
    errors.install(app)
    app.include_router(router)
    return app
