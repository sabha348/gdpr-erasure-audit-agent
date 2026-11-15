import json

from erasure_audit.api.app import create_app
from paths import ROOT


def test_checked_in_openapi_is_current():
    """Regenerate with: erasure-audit openapi > docs/openapi.json"""
    current = json.loads(json.dumps(create_app(configure_logging=False).openapi(), sort_keys=True))
    assert json.loads((ROOT / "docs" / "openapi.json").read_text(encoding="utf-8")) == current
