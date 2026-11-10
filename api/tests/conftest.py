from pathlib import Path

import pytest

from api_test_support import ClientFactory, client_for_app


@pytest.fixture
def client_for() -> ClientFactory:
    return client_for_app


@pytest.fixture
def metrics_path(tmp_path: Path) -> Path:
    return tmp_path / "metrics.jsonl"
