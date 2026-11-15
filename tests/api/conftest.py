import pytest
from fastapi.testclient import TestClient

from erasure_audit.api.app import create_app
from erasure_audit.service import AuditService
from erasure_audit.settings import Settings
from paths import DATASET


@pytest.fixture(scope="session")
def service():
    return AuditService(DATASET)


@pytest.fixture
def client(service, tmp_path):
    settings = Settings(_env_file=None, ERASURE_DATASET_DIR=str(DATASET), ERASURE_CACHE_DIR=str(tmp_path),
                        ERASURE_BATCH_LIMIT=5, ERASURE_LOG_JSON=False)
    app = create_app(settings, service=service, configure_logging=False)
    with TestClient(app) as c:
        c.settings = settings
        yield c
