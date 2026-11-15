"""Worker process: `erasure-audit worker`. Loads the AuditService once and serves workflows and activities."""
from __future__ import annotations

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor

from temporalio.client import Client
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.worker import Worker

from ..logging_setup import configure
from ..pipeline import Options
from ..remediation import DryRunExecutor
from ..service import AuditService
from ..settings import Settings, get_settings
from .activities import AuditActivities
from .audit_workflow import ErasureAuditWorkflow

log = logging.getLogger("erasure_audit.worker")


async def connect(settings: Settings) -> Client:
    return await Client.connect(settings.temporal_address, namespace=settings.temporal_namespace,
                                data_converter=pydantic_data_converter)


def build_worker(client: Client, settings: Settings, activities: AuditActivities, *,
                 executor: ThreadPoolExecutor) -> Worker:
    return Worker(client, task_queue=settings.temporal_task_queue, workflows=[ErasureAuditWorkflow],
                  activities=activities.all(), activity_executor=executor)


async def run(settings: Settings | None = None) -> None:
    settings = settings or get_settings()
    configure(settings.log_level, settings.log_json)
    service = await asyncio.to_thread(AuditService, settings.dataset_dir, Options(classifier=settings.classifier))
    activities = AuditActivities(service, DryRunExecutor(settings.remediation_outbox))
    client = await connect(settings)
    with ThreadPoolExecutor(max_workers=8) as pool:
        worker = build_worker(client, settings, activities, executor=pool)
        log.info("worker started", extra={"task_queue": settings.temporal_task_queue,
                                          "address": settings.temporal_address, "startup_s": round(service.startup_s, 3)})
        await worker.run()
