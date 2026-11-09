"""v1 progress tests run with PROGRESS_REGISTRY=v1; registry v2 tests (tests/progress/dsm) run with dsm."""
import pytest


@pytest.fixture(autouse=True)
def _registry_kind(request, monkeypatch):
    monkeypatch.setenv("PROGRESS_REGISTRY", "dsm" if "/dsm/" in str(request.node.fspath) else "v1")
